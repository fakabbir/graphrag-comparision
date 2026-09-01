#!/usr/bin/env bash
# Run a load stage ON the app host, detached, and follow its log.
#
#   scripts/remote_run.sh all      # pull from S3, load, embed, project graph
#   scripts/remote_run.sh embed    # embed + build the HNSW index
#   scripts/remote_run.sh neo4j    # project the graph
#   scripts/remote_run.sh status   # is anything running? tail the log
#
# The previous version ran the work inside the SSH session, so a dropped
# connection during a ten-minute ANALYZE killed the load. Here the work is
# nohup'd on the host and writes to /opt/graphrag/load.log; this script only
# follows that log, so losing the connection costs nothing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STEP="${1:-all}"

cd "$ROOT/infra"
HOST="$(terraform output -raw app_public_ip)"
BUCKET="$(terraform output -raw data_bucket)"
cd "$ROOT"

KEY="$ROOT/infra/.secrets/graphrag-demo.pem"
SSHOPT=(-i "$KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20
        -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
SSH=(ssh "${SSHOPT[@]}" ec2-user@"$HOST")
LOG=/home/ec2-user/graphrag-load.log

if [[ "$STEP" == "status" ]]; then
  "${SSH[@]}" "if [ -f /home/ec2-user/graphrag_stage.pid ] && kill -0 \$(cat /home/ec2-user/graphrag_stage.pid) 2>/dev/null; then echo RUNNING; else echo idle; fi; tail -25 $LOG 2>/dev/null"
  exit 0
fi

echo "==> preparing $HOST"
"${SSH[@]}" 'sudo install -d -o ec2-user -g ec2-user \
  /opt/graphrag/etl /opt/graphrag/sql /opt/graphrag/data/staging /opt/graphrag/data/staging/parts'

rsync -az -e "ssh ${SSHOPT[*]}" --exclude '__pycache__' --exclude '*.pyc' \
  "$ROOT/etl/" ec2-user@"$HOST":/opt/graphrag/etl/
rsync -az -e "ssh ${SSHOPT[*]}" "$ROOT/sql/" ec2-user@"$HOST":/opt/graphrag/sql/

# The stage script lives on the host so nothing depends on the SSH session.
"${SSH[@]}" "cat > /home/ec2-user/graphrag_stage.sh" <<'STAGE'
#!/usr/bin/env bash
set -euo pipefail
set -a; . /etc/graphrag/graphrag.env; set +a
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
PY=/opt/graphrag/venv/bin/python
cd /opt/graphrag
STEP="$1"; BUCKET="$2"
log() { echo "[$(date -u +%H:%M:%S)] $*"; }

if [[ "$STEP" == "all" ]]; then
  log "pulling staging data from s3://$BUCKET/staging/"
  aws s3 sync "s3://$BUCKET/staging/" data/staging/ --only-show-errors --exclude '*' --include '*.jsonl.gz'
  aws s3 sync "s3://$BUCKET/staging/parts/" data/staging/parts/ --only-show-errors
  for base in $(ls data/staging/parts/ 2>/dev/null | sed 's/\.[a-z][a-z]$//' | sort -u); do
    [[ -n "$base" ]] || continue
    log "reassembling $base"
    cat data/staging/parts/"$base".* > "data/staging/$base"
  done
  log "decompressing"
  for f in data/staging/*.jsonl.gz; do
    out="${f%.gz}"; [[ -s "$out" && "$out" -nt "$f" ]] || gunzip -c "$f" > "$out"
  done
  log "loading relational tables"
  $PY -u etl/05_load_postgres.py
fi

if [[ "$STEP" == "all" || "$STEP" == "embed" ]]; then
  log "dropping the HNSW index before the bulk load"
  psql "$PG_DSN" -q -c "DROP INDEX IF EXISTS section_chunk_hnsw;"
  log "embedding"
  $PY -u etl/07_embed.py
  log "building the HNSW index"
  psql "$PG_DSN" -q -c "SET maintenance_work_mem='1GB';
    SET max_parallel_maintenance_workers=2;
    CREATE INDEX IF NOT EXISTS section_chunk_hnsw ON section_chunk
      USING hnsw (embedding vector_cosine_ops);"
  log "analyzing section_chunk"
  psql "$PG_DSN" -q -c "ANALYZE section_chunk (accession_number, item_code, company_cik);"
fi

if [[ "$STEP" == "all" || "$STEP" == "neo4j" ]]; then
  log "projecting the graph"
  $PY -u etl/06_load_neo4j.py
fi

log "final counts"
psql "$PG_DSN" -P pager=off -c "
SELECT 'company' t, count(*) FROM company UNION ALL
SELECT 'filing', count(*) FROM filing UNION ALL
SELECT 'filing_section', count(*) FROM filing_section UNION ALL
SELECT 'section_chunk', count(*) FROM section_chunk UNION ALL
SELECT 'subsidiary', count(*) FROM subsidiary UNION ALL
SELECT 'reporting_owner', count(*) FROM reporting_owner ORDER BY 1;"
psql "$PG_DSN" -t -A -c "SELECT 'db size: '||pg_size_pretty(pg_database_size(current_database()))"
log "STAGE COMPLETE"
STAGE

"${SSH[@]}" "chmod +x /home/ec2-user/graphrag_stage.sh"

echo "==> launching stage '$STEP' detached on $HOST"
"${SSH[@]}" "PIDF=/home/ec2-user/graphrag_stage.pid
  if [ -f \$PIDF ] && kill -0 \$(cat \$PIDF) 2>/dev/null; then
    echo 'a stage is already running (pid '\$(cat \$PIDF)')'; exit 1
  fi
  : > $LOG
  nohup setsid /home/ec2-user/graphrag_stage.sh '$STEP' '$BUCKET' >> $LOG 2>&1 < /dev/null &
  echo \$! > \$PIDF
  sleep 3
  kill -0 \$(cat \$PIDF) 2>/dev/null && echo \"launched pid \$(cat \$PIDF)\" || { echo 'died immediately'; tail -20 $LOG; exit 1; }"

echo "==> following $LOG (safe to interrupt; the job keeps running)"
"${SSH[@]}" "tail -f -n +1 $LOG | sed '/STAGE COMPLETE/q'" || true
echo
echo "done. check again with: scripts/remote_run.sh status"
