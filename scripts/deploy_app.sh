#!/usr/bin/env bash
# Ship app/ (and optionally the gateway config) to the EC2 host and restart.
#
#   scripts/deploy_app.sh                 # app/ only
#   scripts/deploy_app.sh --gateway       # also refresh Bifrost's config
#   scripts/deploy_app.sh --no-restart    # stage files, leave the service alone
#
# Transport is S3 + SSM RunShellScript, NOT ssh. An earlier version of this
# script used ssh+rsync and had to be abandoned mid-project: it needs inbound 22
# from whatever your current IP is, and a rotating home IP locked us out three
# times. SSM needs no inbound access at all - the agent polls outbound and is
# authorised by the instance role - so this works from anywhere, including CI,
# with no key material and no security-group edits.
#
# Requires: AWS credentials with ssm:SendCommand + s3:PutObject on the data
# bucket, and terraform state readable for the bucket name.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WITH_GATEWAY=0
RESTART=1
for arg in "$@"; do
  case "$arg" in
    --gateway)    WITH_GATEWAY=1 ;;
    --no-restart) RESTART=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# ── locate the target ──────────────────────────────────────────────────────
BUCKET="${DATA_BUCKET:-$(cd "$ROOT/infra" && terraform output -raw data_bucket)}"
# The instance id is not a terraform output, so find it by tag. Restricting to
# running instances stops a terminated predecessor from matching.
IID="${APP_INSTANCE_ID:-$(aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=graphrag" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)}"
[[ -n "$IID" && "$IID" != *[[:space:]]* ]] || {
  echo "error: expected exactly one running graphrag instance, got: '$IID'" >&2
  exit 1
}
echo "==> instance $IID   bucket $BUCKET"

# ── package ────────────────────────────────────────────────────────────────
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PATHS=(app scripts/rescore.py scripts/build_ui_data.py)
[[ $WITH_GATEWAY -eq 1 ]] && PATHS+=(gateway)
tar czf "$TMP/app.tgz" --exclude='__pycache__' --exclude='*.pyc' -C "$ROOT" "${PATHS[@]}"
echo "==> packaged $(wc -c < "$TMP/app.tgz" | tr -d '[:space:]') bytes: ${PATHS[*]}"

aws s3 cp "$TMP/app.tgz" "s3://$BUCKET/deploy/app.tgz" --only-show-errors
echo "==> uploaded"

# ── remote steps ───────────────────────────────────────────────────────────
# Built as a JSON array of shell lines. Keep each line free of single quotes:
# they have to survive being embedded in the --parameters JSON.
STEPS=(
  "set -euo pipefail"
  "aws s3 cp s3://$BUCKET/deploy/app.tgz /tmp/app.tgz --only-show-errors"
  # tar warns about macOS xattr headers on every file; harmless, and it would
  # otherwise trip the errexit above.
  "rm -rf /tmp/deploy && mkdir -p /tmp/deploy && tar xzf /tmp/app.tgz -C /tmp/deploy 2>/dev/null"
  # --delete so a file removed locally is removed on the host. Without it, a
  # renamed module leaves the old one importable and the bug is invisible.
  "rsync -a --delete --exclude __pycache__ /tmp/deploy/app/ /opt/graphrag/app/"
  "install -d -o ec2-user /opt/graphrag/scripts /opt/graphrag/results"
  "cp /tmp/deploy/scripts/*.py /opt/graphrag/scripts/ 2>/dev/null || true"
)
if [[ $WITH_GATEWAY -eq 1 ]]; then
  STEPS+=(
    # Bifrost reads APP_DIR/config.json = /app/data/config.json, so the file
    # lives inside the persistent data dir, not one level up.
    "cp /tmp/deploy/gateway/config.json /opt/graphrag/gateway/data/config.json"
    "chown -R 1000:1000 /opt/graphrag/gateway/data"
    "docker restart graphrag-bifrost >/dev/null && echo bifrost-restarted"
  )
fi
if [[ $RESTART -eq 1 ]]; then
  STEPS+=(
    "systemctl restart graphrag-api"
    "sleep 9"
    "systemctl is-active graphrag-api"
    "curl -fsS localhost:8020/health"
    "echo"
    # Prove the new code is actually serving, not just that the port is open.
    "curl -fsS localhost:8020/api/meta | head -c 120"
  )
fi

CMD_JSON="$(python3 -c '
import json, sys
print(json.dumps({"commands": sys.argv[1:]}))' "${STEPS[@]}")"

CID="$(aws ssm send-command \
  --instance-ids "$IID" \
  --document-name AWS-RunShellScript \
  --comment "deploy graphrag app" \
  --timeout-seconds 600 \
  --parameters "$CMD_JSON" \
  --query 'Command.CommandId' --output text)"
echo "==> ssm command $CID"

# ── wait for a terminal state ──────────────────────────────────────────────
for _ in $(seq 1 60); do
  STATUS="$(aws ssm get-command-invocation --command-id "$CID" --instance-id "$IID" \
            --query 'Status' --output text 2>/dev/null || echo Pending)"
  case "$STATUS" in
    Success|Failed|Cancelled|TimedOut) break ;;
  esac
  sleep 5
done

aws ssm get-command-invocation --command-id "$CID" --instance-id "$IID" \
  --query 'StandardOutputContent' --output text | sed 's/^/    /'

if [[ "$STATUS" != "Success" ]]; then
  echo "==> $STATUS" >&2
  aws ssm get-command-invocation --command-id "$CID" --instance-id "$IID" \
    --query 'StandardErrorContent' --output text | sed 's/^/    !! /' >&2
  echo "    journal: aws ssm send-command --instance-ids $IID --document-name AWS-RunShellScript \\" >&2
  echo "               --parameters 'commands=[\"journalctl -u graphrag-api -n 60 --no-pager\"]'" >&2
  exit 1
fi
echo "==> deployed"
