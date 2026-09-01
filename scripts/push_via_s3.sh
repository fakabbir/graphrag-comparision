#!/usr/bin/env bash
# Push the locally-built dataset to S3, compressed.
#
# Measured on this link: 0.7 MB/s upload, and it is the operator's bandwidth, not
# tunnel overhead - raw parallel multipart S3 upload gets the same rate. So the
# 5.2 GB of embeddings are never uploaded at all: only the extracted staging
# JSONL goes up (~650 MB gzipped, ~15 min), and the app host does the embedding
# and the COPY into RDS over the AWS-internal network.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"
BUCKET="$(terraform output -raw data_bucket)"
cd "$ROOT"

FILES=(
  companies.jsonl form345_issuers.jsonl filings.jsonl documents.jsonl
  sections.jsonl subsidiaries.jsonl auditors.jsonl owner_edges.jsonl
  insiders.jsonl
)

aws configure set default.s3.max_concurrent_requests 16
aws configure set default.s3.multipart_chunksize 16MB

echo "==> compressing and uploading to s3://$BUCKET/staging/"
total_raw=0
total_gz=0
for f in "${FILES[@]}"; do
  src="data/staging/$f"
  [[ -s "$src" ]] || { echo "  skip $f (missing)"; continue; }
  gz="data/staging/$f.gz"
  if [[ ! -s "$gz" || "$src" -nt "$gz" ]]; then
    gzip -1 -c "$src" > "$gz"
  fi
  raw=$(wc -c < "$src"); zip=$(wc -c < "$gz")
  total_raw=$((total_raw + raw)); total_gz=$((total_gz + zip))
  printf "  %-26s %7.1f MB -> %6.1f MB  " "$f" "$(bc -l <<<"$raw/1048576")" "$(bc -l <<<"$zip/1048576")"
  aws s3 cp "$gz" "s3://$BUCKET/staging/$f.gz" --only-show-errors
  echo "uploaded"
done

printf "\n  total %.0f MB raw -> %.0f MB uploaded (%.0f%%)\n" \
  "$(bc -l <<<"$total_raw/1048576")" "$(bc -l <<<"$total_gz/1048576")" \
  "$(bc -l <<<"100*$total_gz/$total_raw")"

echo
echo "next: scripts/remote_load.sh   (runs the load ON the app host)"
