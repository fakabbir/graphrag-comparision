#!/usr/bin/env bash
# Upload data/staging/parts/* to S3, one verified PUT at a time.
#
# A single 434 MB multipart upload aborted twice on this link while `aws s3 cp`
# still exited 0, leaving nothing in the bucket. 40 MB parts sit under the 64 MB
# multipart threshold, so each is one atomic PUT that either lands or doesn't -
# and each is size-verified before moving on. A failure costs one minute of
# retry instead of eleven.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra"; BUCKET="$(terraform output -raw data_bucket)"; cd "$ROOT"

aws configure set default.s3.multipart_threshold 64MB
aws configure set default.s3.max_concurrent_requests 8

shopt -s nullglob
parts=(data/staging/parts/*)
[[ ${#parts[@]} -gt 0 ]] || { echo "no parts in data/staging/parts/" >&2; exit 1; }

echo "==> uploading ${#parts[@]} parts to s3://$BUCKET/staging/parts/"
for p in "${parts[@]}"; do
  name="$(basename "$p")"
  want=$(wc -c < "$p" | tr -d "[:space:]")
  have=$(aws s3api head-object --bucket "$BUCKET" --key "staging/parts/$name" \
           --query ContentLength --output text 2>/dev/null || echo "")
  if [[ "$have" == "$want" ]]; then
    printf "  %-28s %5.0f MB  already there\n" "$name" "$(bc -l <<<"$want/1048576")"
    continue
  fi
  for attempt in 1 2 3; do
    printf "  %-28s %5.0f MB  attempt %d ... " "$name" "$(bc -l <<<"$want/1048576")" "$attempt"
    if aws s3api put-object --bucket "$BUCKET" --key "staging/parts/$name" \
         --body "$p" --output text --query ETag >/dev/null 2>&1; then
      have=$(aws s3api head-object --bucket "$BUCKET" --key "staging/parts/$name" \
               --query ContentLength --output text 2>/dev/null || echo "")
      if [[ "$have" == "$want" ]]; then echo "ok"; break; fi
      echo "size mismatch ($have)"
    else
      echo "put failed"
    fi
    [[ $attempt -eq 3 ]] && { echo "  ERROR: $name would not upload" >&2; exit 1; }
    sleep 5
  done
done

echo
echo "==> verifying every part"
total=0
for p in "${parts[@]}"; do
  name="$(basename "$p")"; want=$(wc -c < "$p" | tr -d "[:space:]")
  have=$(aws s3api head-object --bucket "$BUCKET" --key "staging/parts/$name" \
           --query ContentLength --output text)
  [[ "$have" == "$want" ]] || { echo "  MISMATCH $name: $have != $want" >&2; exit 1; }
  total=$((total + have))
done
printf "  all %d parts verified, %.0f MB total\n" "${#parts[@]}" "$(bc -l <<<"$total/1048576")"
