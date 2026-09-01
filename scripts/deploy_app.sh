#!/usr/bin/env bash
# Ship the query API to the EC2 host and restart it.
#
#   scripts/deploy_app.sh
#
# Reads the host and key path from terraform outputs, so it always targets the
# instance the current state describes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"

HOST="$(terraform output -raw app_public_ip)"
KEY="$ROOT/infra/.secrets/graphrag-demo.pem"
[[ -f "$KEY" ]] || { echo "error: key not found at $KEY" >&2; exit 1; }

SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new ec2-user@"$HOST")

echo "==> waiting for the bootstrap to finish on $HOST"
for _ in $(seq 1 60); do
  if "${SSH[@]}" 'test -f /var/log/graphrag-bootstrap.done' 2>/dev/null; then
    echo "    bootstrap complete"
    break
  fi
  sleep 10
done

echo "==> syncing app/"
rsync -az --delete \
  -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new" \
  --exclude '__pycache__' --exclude '*.pyc' \
  "$ROOT/app/" ec2-user@"$HOST":/opt/graphrag/app/

echo "==> restarting graphrag-api"
"${SSH[@]}" 'sudo systemctl restart graphrag-api && sleep 4 && sudo systemctl is-active graphrag-api'

echo "==> health check"
"${SSH[@]}" 'curl -fsS localhost:8020/health && echo' || {
  echo "    API not healthy yet; last 40 log lines:" >&2
  "${SSH[@]}" 'sudo journalctl -u graphrag-api -n 40 --no-pager' >&2
  exit 1
}

API="$(terraform output -raw api_base_url)"
echo
echo "deployed. API base URL: $API"
echo "  curl -s $API/api/meta | head -c 400"
