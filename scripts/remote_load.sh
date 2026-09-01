#!/usr/bin/env bash
# Run the load ON the app host, where RDS is one hop away instead of one
# 0.7 MB/s uplink away.
#
#   scripts/remote_load.sh              # sync code, pull data, load, embed, graph
#   scripts/remote_load.sh embed        # just re-embed
#   scripts/remote_load.sh neo4j        # just re-project the graph
#
# The 5.2 GB of embeddings are produced and written entirely inside AWS; nothing
# but the compressed staging JSONL crosses the operator's connection.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STEP="${1:-all}"

cd "$ROOT/infra"
HOST="$(terraform output -raw app_public_ip)"
BUCKET="$(terraform output -raw data_bucket)"
cd "$ROOT"

KEY="$ROOT/infra/.secrets/graphrag-demo.pem"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 ec2-user@"$HOST")

# cloud-init only creates app/, models/ and data/. Creating etl/ and sql/ here
# instead of in user_data avoids queueing an instance replacement: user_data has
# user_data_replace_on_change = true, so editing it would destroy the host.
echo "==> ensuring directories exist on $HOST"
"${SSH[@]}" 'sudo install -d -o ec2-user -g ec2-user \
  /opt/graphrag/etl /opt/graphrag/sql /opt/graphrag/data/staging /opt/graphrag/data/staging/parts'

echo "==> syncing etl/ + sql/ to $HOST"
rsync -az -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new" \
  --exclude '__pycache__' --exclude '*.pyc' \
  "$ROOT/etl/" ec2-user@"$HOST":/opt/graphrag/etl/
rsync -az -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new" \
  "$ROOT/sql/" ec2-user@"$HOST":/opt/graphrag/sql/

echo "==> running the load remotely (step: $STEP)"
"${SSH[@]}" BUCKET="$BUCKET" STEP="$STEP" 'bash -s' <<'REMOTE'
set -euo pipefail
set -a; . /etc/graphrag/graphrag.env; set +a
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
PY=/opt/graphrag/venv/bin/python
cd /opt/graphrag

log() { echo "    [$(date -u +%H:%M:%S)] $*"; }

if [[ "$STEP" == "all" ]]; then
  log "pulling staging data from s3://$BUCKET/staging/"
  mkdir -p data/staging data/staging/parts
  aws s3 sync "s3://$BUCKET/staging/" data/staging/ --only-show-errors --exclude '*' --include '*.jsonl.gz'
  aws s3 sync "s3://$BUCKET/staging/parts/" data/staging/parts/ --only-show-errors

  # Large files are uploaded as split parts: a single 434 MB multipart aborted
  # twice on the operator's link while the CLI still reported success. Reassemble
  # any <name>.gz.<suffix> series back into <name>.gz.
  for base in $(ls data/staging/parts/ 2>/dev/null | sed 's/\.[a-z][a-z]$//' | sort -u); do
    [[ -n "$base" ]] || continue
    log "reassembling $base from parts"
    cat data/staging/parts/"$base".* > "data/staging/$base"
    ls -la "data/staging/$base" | awk '{printf "      %.0f MB\n", $5/1048576}'
  done

  log "decompressing"
  for f in data/staging/*.jsonl.gz; do
    out="${f%.gz}"
    [[ -s "$out" && "$out" -nt "$f" ]] || gunzip -c "$f" > "$out"
  done
  du -sh data/staging | sed 's/^/    /'

  log "loading relational tables into RDS"
  $PY -u etl/05_load_postgres.py
fi

if [[ "$STEP" == "all" || "$STEP" == "embed" ]]; then
  log "embedding (this is the long stage; runs inside AWS)"
  # HNSW maintenance is much cheaper if the index is built after the bulk load.
  psql "$PG_DSN" -q -c "DROP INDEX IF EXISTS section_chunk_hnsw;"
  $PY -u etl/07_embed.py
  log "building the HNSW index now that the rows are in"
  psql "$PG_DSN" -q -c "SET maintenance_work_mem='1GB';
    CREATE INDEX IF NOT EXISTS section_chunk_hnsw ON section_chunk
      USING hnsw (embedding vector_cosine_ops);"
fi

if [[ "$STEP" == "all" || "$STEP" == "neo4j" ]]; then
  log "projecting the graph"
  $PY -u etl/06_load_neo4j.py
fi

log "row counts on RDS"
psql "$PG_DSN" -P pager=off -c "
SELECT 'company' t, count(*) FROM company UNION ALL
SELECT 'filing', count(*) FROM filing UNION ALL
SELECT 'filing_document', count(*) FROM filing_document UNION ALL
SELECT 'filing_section', count(*) FROM filing_section UNION ALL
SELECT 'section_chunk', count(*) FROM section_chunk UNION ALL
SELECT 'subsidiary', count(*) FROM subsidiary UNION ALL
SELECT 'reporting_owner', count(*) FROM reporting_owner UNION ALL
SELECT 'filing_auditor', count(*) FROM filing_auditor ORDER BY 1;"
psql "$PG_DSN" -t -A -c "SELECT 'db size: '||pg_size_pretty(pg_database_size(current_database()));"
REMOTE

echo
echo "done. restart the API so it picks up the new data:"
echo "  ssh -i infra/.secrets/graphrag-demo.pem ec2-user@$HOST 'sudo systemctl restart graphrag-api'"
