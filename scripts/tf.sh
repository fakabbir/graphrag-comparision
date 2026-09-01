#!/usr/bin/env bash
# Terraform wrapper. Refreshes admin_cidrs from the current public IP on every run
# (home IPs rotate, and a stale CIDR locks you out of SSH), and pulls secrets from
# the environment so nothing sensitive lands in a committed file.
#
#   export TF_VAR_neo4j_password='...'
#   export TF_VAR_deepseek_api_key='sk-...'
#   scripts/tf.sh plan
#   scripts/tf.sh apply
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"

if [[ -z "${TF_VAR_neo4j_password:-}" ]]; then
  echo "error: TF_VAR_neo4j_password is not set." >&2
  echo "  export TF_VAR_neo4j_password='<choose a strong password>'" >&2
  exit 1
fi

MYIP="$(curl -fsS --max-time 10 https://checkip.amazonaws.com | tr -d '[:space:]')"
if [[ ! "$MYIP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
  echo "error: could not determine public IP (got '$MYIP')" >&2
  exit 1
fi
export TF_VAR_admin_cidrs="[\"$MYIP/32\"]"
echo "admin_cidrs = [\"$MYIP/32\"]"

exec terraform "$@"
