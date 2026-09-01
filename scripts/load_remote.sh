#!/usr/bin/env bash
# Push the locally-built dataset into the AWS databases.
#
#   scripts/load_remote.sh              # everything
#   scripts/load_remote.sh schema       # just apply sql/01_schema.sql
#   scripts/load_remote.sh postgres     # relational load + embeddings
#   scripts/load_remote.sh neo4j        # project the graph
#
# RDS is private, so this opens an SSH tunnel through the app host and talks to
# 127.0.0.1:55432 as if the database were local. Neo4j is reached directly over
# Bolt, which the security group allows from the operator's IP only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STEP="${1:-all}"
PY="${PY:-$ROOT/../.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3)"

cd "$ROOT/infra"
HOST="$(terraform output -raw app_public_ip)"
RDS="$(terraform output -raw rds_endpoint)"
KEY="$ROOT/infra/.secrets/graphrag-demo.pem"
DB_PW="$(aws ssm get-parameter --region ap-south-1 \
  --name "$(terraform output -raw db_password_ssm_path)" \
  --with-decryption --query 'Parameter.Value' --output text)"
cd "$ROOT"

LOCAL_PORT=55432
export PG_DSN="postgresql://sec:${DB_PW}@127.0.0.1:${LOCAL_PORT}/secedgar"
export NEO4J_URI="bolt://${HOST}:7687"
export NEO4J_USER=neo4j
export NEO4J_PASS="${TF_VAR_neo4j_password:?export TF_VAR_neo4j_password first}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

echo "==> opening tunnel 127.0.0.1:${LOCAL_PORT} -> ${RDS}:5432 via ${HOST}"
ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ExitOnForwardFailure=yes \
    -f -N -L "${LOCAL_PORT}:${RDS}:5432" ec2-user@"$HOST"
TUNNEL_PID="$(pgrep -f "L ${LOCAL_PORT}:${RDS}:5432" | head -1 || true)"
cleanup() { [[ -n "${TUNNEL_PID:-}" ]] && kill "$TUNNEL_PID" 2>/dev/null || true; }
trap cleanup EXIT

for _ in $(seq 1 30); do
  PGPASSWORD="$DB_PW" psql -h 127.0.0.1 -p "$LOCAL_PORT" -U sec -d secedgar \
    -c 'SELECT 1' >/dev/null 2>&1 && break
  sleep 2
done
echo "    tunnel up"

run_schema() {
  echo "==> applying schema"
  PGPASSWORD="$DB_PW" psql -h 127.0.0.1 -p "$LOCAL_PORT" -U sec -d secedgar \
    -v ON_ERROR_STOP=1 -f sql/01_schema.sql
}

run_postgres() {
  echo "==> loading relational tables"
  "$PY" -u etl/05_load_postgres.py
  echo "==> embedding narrative sections (the slow stage)"
  "$PY" -u etl/07_embed.py
}

run_neo4j() {
  echo "==> projecting the graph"
  "$PY" -u etl/06_load_neo4j.py
}

case "$STEP" in
  schema)   run_schema ;;
  postgres) run_postgres ;;
  neo4j)    run_neo4j ;;
  all)      run_schema; run_postgres; run_neo4j ;;
  *) echo "unknown step: $STEP" >&2; exit 2 ;;
esac

echo
echo "==> row counts on RDS"
PGPASSWORD="$DB_PW" psql -h 127.0.0.1 -p "$LOCAL_PORT" -U sec -d secedgar -P pager=off -c "
SELECT 'filing' t, count(*) FROM filing UNION ALL
SELECT 'company', count(*) FROM company UNION ALL
SELECT 'filing_section', count(*) FROM filing_section UNION ALL
SELECT 'section_chunk', count(*) FROM section_chunk UNION ALL
SELECT 'subsidiary', count(*) FROM subsidiary UNION ALL
SELECT 'reporting_owner', count(*) FROM reporting_owner ORDER BY 1;"
echo "done"
