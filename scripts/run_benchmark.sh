#!/usr/bin/env bash
# Launch the benchmark on the app host, detached, with the service environment.
#
#   scripts/run_benchmark.sh results/bench21.json [--trials 3]
#
# Sources /etc/graphrag/graphrag.env in the SAME shell as the launch. A previous
# run was invalidated because the env was sourced in a separate SSM command and
# shell state does not persist between them: EMBED_MODEL was unset, the vector
# arm fell back to a non-existent model path, and 48 attempts were scored as
# failures before anyone noticed. The preflight below makes that loud.
set -euo pipefail

OUT="${1:-results/bench$(date +%s).json}"
shift || true
TRIALS="${*:---trials 3}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IID="${APP_INSTANCE_ID:-$(aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=graphrag" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)}"
[[ -n "$IID" && "$IID" != *[[:space:]]* ]] || { echo "need exactly one running instance, got '$IID'" >&2; exit 1; }

read -r -d '' REMOTE <<'SH' || true
set -euo pipefail
set -a; . /etc/graphrag/graphrag.env; set +a
cd /opt/graphrag

# The 45s statement timeout is a production guardrail for the live playground,
# but it was added AFTER the first published run (whose 445s worst-case latency
# proves it was absent). Leaving it on would confound a before/after comparison
# with a second change, so the benchmark runs without it unless overridden.
export PG_STATEMENT_TIMEOUT_MS="${BENCH_PG_TIMEOUT_MS:-0}"
echo "PG_STATEMENT_TIMEOUT_MS=$PG_STATEMENT_TIMEOUT_MS for this run"
install -d -o ec2-user results

# Preflight: every one of these silently degrades a whole arm if missing.
: "${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY unset}"
: "${PG_DSN:?PG_DSN unset}"
: "${NEO4J_URI:?NEO4J_URI unset}"
: "${EMBED_MODEL:?EMBED_MODEL unset}"
test -d "$EMBED_MODEL" || { echo "EMBED_MODEL=$EMBED_MODEL is not a directory" >&2; exit 1; }
test -f "$EMBED_MODEL/sentence_bert_config.json" || { echo "no encoder at $EMBED_MODEL" >&2; exit 1; }
echo "preflight ok: encoder at $EMBED_MODEL"

pgrep -f benchmark.py >/dev/null && { echo "a benchmark is already running" >&2; exit 1; }
rm -f LOGFILE
nohup setsid ./venv/bin/python app/benchmark.py TRIALS --quiet --out OUTFILE \
  > LOGFILE 2>&1 < /dev/null &
sleep 6
pgrep -f benchmark.py >/dev/null || { echo "failed to start; log:" >&2; tail -20 LOGFILE >&2; exit 1; }
echo "started, writing OUTFILE"
# fail fast if the first attempts are already crashing
sleep 25
if grep -q "mode crashed" LOGFILE; then
  echo "ABORTING: a mode crashed in the first attempts" >&2
  grep -m3 "mode crashed" LOGFILE >&2
  pkill -f benchmark.py || true
  exit 1
fi
echo "first attempts clean"
SH

LOG="/tmp/$(basename "$OUT" .json).log"
REMOTE="${REMOTE//OUTFILE/$OUT}"
REMOTE="${REMOTE//LOGFILE/$LOG}"
REMOTE="${REMOTE//TRIALS/$TRIALS}"

CMD_JSON="$(python3 -c '
import json,sys
print(json.dumps({"commands": sys.stdin.read().splitlines()}))' <<<"$REMOTE")"

CID="$(aws ssm send-command --instance-ids "$IID" --document-name AWS-RunShellScript \
  --comment "run graphrag benchmark" --timeout-seconds 300 \
  --parameters "$CMD_JSON" --query 'Command.CommandId' --output text)"
echo "==> ssm $CID (instance $IID)"

for _ in $(seq 1 40); do
  ST="$(aws ssm get-command-invocation --command-id "$CID" --instance-id "$IID" \
        --query Status --output text 2>/dev/null || echo Pending)"
  case "$ST" in Success|Failed|Cancelled|TimedOut) break ;; esac
  sleep 5
done
aws ssm get-command-invocation --command-id "$CID" --instance-id "$IID" \
  --query 'StandardOutputContent' --output text | sed 's/^/    /'
if [[ "$ST" != Success ]]; then
  aws ssm get-command-invocation --command-id "$CID" --instance-id "$IID" \
    --query 'StandardErrorContent' --output text | sed 's/^/    !! /' >&2
  exit 1
fi
echo "==> running; log is $LOG on the host"
