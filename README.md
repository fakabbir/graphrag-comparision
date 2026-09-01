# graphrag-comparision

Backend, data pipeline and infrastructure for **GraphRAG Performance Metrics** — a
Trussk research subproject comparing three retrieval architectures over SEC EDGAR
filings.

Frontend lives in [`trussk-landing-page`](https://github.com/fakabbir/trussk-landing-page)
and is served from Vercel at `trussk.com/graphrag`.

---

## What this compares

| Architecture | Store | What it does |
|---|---|---|
| **text-to-SQL** | Postgres only | LLM writes a read-only SQL query against the full relational schema |
| **Vector RAG** | pgvector | Cosine similarity over 1,400-char chunks of 10-K narrative text |
| **GraphRAG** | Neo4j + Postgres | LLM plans graph hops; the system expands entities completely, then pulls the exact passage from Postgres by `filing_id` |

All three use the same model, settings and call budget. Scoring is programmatic
against hand-verified ground truth — no LLM judge. See `app/questions.py`.

The headline result on the reference month: **GraphRAG 9/12, text-to-SQL 3/12,
Vector RAG 3/12**, with 2 confident falsehoods from SQL and none from GraphRAG.
The reason is not that SQL cannot join — it is that identity and provenance are
structure you either store or reconstruct.

---

## Architecture

```
        SEC EDGAR monthly XBRL RSS  ──┐
        (the filing spine)           │
                                     ├──► 01 parse ──► staging JSONL
        Form 3/4/5 quarterly TSVs  ──┤                     │
        (the ONLY ownership source)  │                     │
                                     │                     ▼
        10-K primaries + EX-21     ──┘         05 load ──► Postgres 17 + pgvector
        (fetched, SEC-rate-limited)            07 embed     (system of record)
                                                              │
                                               06 project ────┘
                                                              ▼
                                                          Neo4j 5.26
                                                    (filingId on every edge)
```

Postgres is the system of record; Neo4j is a projection of it. Stage 06 reads only
from Postgres, so the graph can be rebuilt in minutes without re-fetching anything.

### AWS (ap-south-1, ~$122/month)

```
Vercel (trussk.com/graphrag)
   │  HTTPS
   ▼
CloudFront  ──── TLS termination on *.cloudfront.net, no DNS record needed
   │  HTTP, origin-facing prefix list only
   ▼
EC2 m7g.large (public subnet, Elastic IP)
   ├── Caddy      :80  → reverse proxy
   ├── uvicorn    :8020 app/api.py
   └── Neo4j      :7474 / :7687  (admin CIDRs only)
   │
   ▼  private subnets, no internet route
RDS PostgreSQL 17.11 + pgvector 0.8.2   ← S3 Gateway Endpoint for bulk import
```

No NAT Gateway: the app host sits in a public subnet with an EIP, and RDS reaches
S3 through a free Gateway VPC Endpoint. That is a deliberate ~$41/month saving.

Neo4j has no managed AWS equivalent, so an instance is required regardless — which
is exactly why the API runs on it rather than Lambda. Lambda would have cost *more*
(VPC + NAT to reach the DeepSeek API) and added 10–20s cold starts.

---

## Quick start

### 1. Infrastructure

```bash
export TF_VAR_neo4j_password='<strong password>'
export TF_VAR_deepseek_api_key='sk-...'      # optional; stored in SSM SecureString
scripts/tf.sh plan
scripts/tf.sh apply
```

`scripts/tf.sh` refreshes `admin_cidrs` from your current public IP on every run —
home IPs rotate, and a stale CIDR locks you out of SSH.

Outputs include `api_base_url` (set this as `VITE_API_BASE` in Vercel),
`ssh_command`, `pg_tunnel_command` and `data_bucket`.

### 2. Build the dataset locally

```bash
python etl/00_fetch_feeds.py --months 12     # or --from 2025-09 --to 2026-08
python etl/01_parse_rss.py                   # parses every data/xbrlrss-*.xml
python etl/02_fetch_docs.py                  # 10-K primaries + EX-21, 8 req/s
python etl/03_form345.py                     # ownership edges (separate download)
python etl/04_extract.py                     # HTML → sections, subsidiaries, auditors
```

### 3. Push it to AWS

```bash
scripts/load_remote.sh          # schema, relational load, embeddings, graph
```

RDS is private, so this opens an SSH tunnel through the app host and loads through
it. For much larger loads, stage CSVs to the S3 bucket and use
`aws_s3.table_import_from_s3` — the IAM role for that is already provisioned
(`rds_s3_import_role_arn`).

### 4. Deploy the API

```bash
scripts/deploy_app.sh
```

Waits for cloud-init to finish, rsyncs `app/`, restarts the systemd unit and
health-checks it.

---

## Layout

```
infra/                  Terraform: VPC, RDS, EC2, CloudFront, S3, IAM, SSM
  templates/            cloud-init: Docker, Neo4j, Caddy, venv, systemd
etl/00_fetch_feeds.py   download N monthly XBRL RSS feeds
etl/01_parse_rss.py     feeds → companies / filings / document manifest
etl/02_fetch_docs.py    fetch 10-K primaries + EX-21 (resumable)
etl/03_form345.py       Form 3/4/5 → role and ownership edges
etl/04_extract.py       HTML → Item sections, subsidiaries, dei:Auditor* tags
etl/05_load_postgres.py COPY into Postgres
etl/06_load_neo4j.py    project Postgres → Neo4j
etl/07_embed.py         chunk + embed for the vector baseline
app/api.py              FastAPI query API (the playground backend)
app/mode_*.py           the three retrieval architectures
app/rowbalance.py       LIMIT/fan-out guardrail, applied to both query languages
app/benchmark.py        programmatic scoring, --trials N
sql/01_schema.sql       relational schema + HNSW / GIN / trigram indexes
scripts/                tf.sh, deploy_app.sh, load_remote.sh
```

---

## Operational notes worth knowing

1. **`requests`/urllib3 is ~70× slower than curl against sec.gov** on macOS — the
   same gzipped 8 MB document takes 1.4s with curl and 100s+ with `requests`, same
   HTTP version, no proxy. `etl/common.py` shells out to curl. Re-measure on Linux
   before keeping the workaround.
2. **The SEC caps requests at 10/second per requester.** ~22 minutes of the pipeline
   is unavoidable waiting, and no amount of parallelism removes it. Prefer the bulk
   archives (`submissions.zip`, quarterly Form 345/13F zips) over per-document
   requests wherever possible.
3. **`master.idx` rows are CIK × filing, not filings.** Always `DISTINCT` on
   accession number.
4. **Concurrent `torch.encode()` SIGSEGVs the interpreter** (multiple OpenMP
   runtimes). `app/stores.py` pins torch to one thread and serialises the forward
   pass behind a mutex; a query embedding is ~5 ms so the lock is free. Scale
   embeddings with **processes**, never threads.
5. **pgvector post-filters.** A `WHERE` clause is applied after the HNSW walk picks
   its `ef_search` candidates, so a selective filter can silently return fewer rows
   than requested — measured: filtering to one company returned 0 of 8 while 164
   rows matched. `stores.py` sets `hnsw.iterative_scan = relaxed_order` on every
   connection.
6. **DeepSeek v4 reasons by default** and `max_tokens` bounds only the visible
   answer. One run burned 33,051 completion tokens in 2 calls. With
   `reasoning_effort=none`: 4,678 tokens, same result — 9× cheaper, 44× faster.
7. **The HNSW index is created before the bulk load** in `sql/01_schema.sql`. Fine
   at 14k rows, slow at millions — `COPY` first, `CREATE INDEX` after.
8. **`Exhibit 21 is legally incomplete.`** Item 601(b)(21)(ii) lets filers omit
   non-significant subsidiaries. Boeing discloses 15; Lennar 806. Never treat it as
   exhaustive.

## Cost

Measured against the live AWS Pricing API for ap-south-1:

| | USD/month |
|---|---:|
| RDS db.t4g.medium (Single-AZ) | 61.32 |
| RDS storage, 100 GB gp3 | 9.12 |
| EC2 m7g.large | 42.56 |
| EC2 storage, 100 GB gp3 | 9.12 |
| S3 + CloudFront (free tier at demo volume) | ~0.50 |
| NAT Gateway (deliberately not used) | 0.00 |
| **Total** | **~122** |

`scripts/tf.sh destroy` removes everything; `deletion_protection` and
`skip_final_snapshot` are set so that actually works.

## Licence & data

Code: see [`LICENSE`](LICENSE) — no rights provided.
Data: SEC EDGAR, public domain. Not investment advice.
