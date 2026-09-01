#!/usr/bin/env python
"""GraphRAG query API - the backend the /graphrag/playground page calls.

    uvicorn api:app --host 127.0.0.1 --port 8020 --workers 2

Endpoints
    GET  /health        liveness for CloudFront / Caddy
    GET  /api/meta      dataset stats, preset questions, saved benchmark
    POST /api/ask       {question, mode, question_id?} -> one mode's full result

Endpoints are plain `def`, so FastAPI runs them in its threadpool. The retrieval
modes are blocking, and stores.py serialises the embedding forward pass - concurrent
torch encode() calls SIGSEGV the interpreter, so that lock is load-bearing here.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mode_graphrag  # noqa: E402
import mode_sql  # noqa: E402
import mode_vector  # noqa: E402
import stores  # noqa: E402
from config import DEEPSEEK_MODEL  # noqa: E402
from llm import USAGE, begin_request, request_usage  # noqa: E402
from questions import QUESTIONS  # noqa: E402

MODES = {"text_to_sql": mode_sql, "vector_rag": mode_vector, "graphrag": mode_graphrag}

# One in-flight LLM run per mode: a hammered refresh must not fan out paid calls.
_mode_locks = {m: threading.Lock() for m in MODES}

ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
if not ORIGINS:
    ORIGINS = ["https://trussk.com", "https://www.trussk.com", "http://localhost:5173"]

app = FastAPI(title="GraphRAG Performance Metrics API", version="1.0.0", docs_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",  # preview deployments
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    max_age=3600,
)


# ── helpers ─────────────────────────────────────────────────────────────────
def build_trace(mode: str, out: dict) -> str:
    """Render what the mode did, from its structured result.

    Deliberately not a stdout capture: redirect_stdout swaps the process-wide
    sys.stdout, so under a threadpool one mode's trace lands in another's buffer.
    """
    L: list[str] = []
    if mode == "text_to_sql":
        n = len(out.get("sql_attempts") or [])
        if n > 1:
            L.append(f"{n} SQL attempts (first failed or returned nothing)")
        if out.get("sql_error"):
            L.append(f"error: {out['sql_error']}")
        L.append(f"rows returned: {out.get('row_count')}")
        rb = out.get("row_balance") or {}
        if rb.get("rows_in", 0) > rb.get("rows_kept", 0):
            L.append(f"{rb['rows_in']} rows across {rb['entities']} distinct "
                     f"{rb['entity_column']} values; showed {rb['rows_kept']} balanced")
    elif mode == "vector_rag":
        L.append(f"top-{len(out.get('retrieved') or [])} chunks by cosine similarity; "
                 f"{out.get('chunks_used')} fitted the context budget")
        for h in (out.get("retrieved") or [])[:8]:
            L.append(f"  {h['similarity']:.3f}  {h['company'][:34]:34s} "
                     f"Item {h['item']}  {h['accession']}")
    else:
        n = len(out.get("cypher_attempts") or [])
        if n > 1:
            L.append(f"{n} Cypher attempts (first failed or returned nothing)")
        if out.get("cypher_error"):
            L.append(f"error: {out['cypher_error']}")
        L.append(f"graph rows: {out.get('graph_rows')}")
        for c in out.get("companies_expanded") or []:
            L.append(f"  expanded {c['name']}: {c['subsidiaryCount']} subsidiaries, "
                     f"risk filings {c.get('riskFilings')}")
        L.append(f"filings identified: {len(out.get('filings_identified') or [])}")
        L.append(f"text terms: {', '.join(out.get('text_terms') or []) or '(none)'}")
        L.append(f"passages pulled by filing_id: {out.get('snippets')}")
    L.append(f"evidence handed to the model: {out.get('evidence_chars', 0):,} chars")
    return "\n".join(L)


ACC_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")


def score_against_preset(question_id: str | None, answer: str) -> dict | None:
    """Only the preset questions have hand-verified ground truth; free-form
    questions get no verdict rather than an invented one."""
    q = next((x for x in QUESTIONS if x["id"] == question_id), None)
    if not q:
        return None
    low = (answer or "").lower()
    cited = set(ACC_RE.findall(answer or ""))
    valid = q["valid_accessions"]
    found = [e for e in q["required_entities"] if e.lower() in low]
    bad = [e for e in q["forbidden_entities"] if e.lower() in low]
    kw = [t for t in q["required_any"] if t.lower() in low]
    correct = cited & valid
    entity_recall = len(found) / max(len(q["required_entities"]), 1)
    return {
        "question_id": question_id,
        "correct": (entity_recall == 1.0 and bool(kw) and not bad
                    and (not valid or bool(correct))),
        "entity_recall": round(entity_recall, 2),
        "entities_found": found,
        "entities_missing": [e for e in q["required_entities"] if e not in found],
        "forbidden_present": bad,
        "cited": sorted(cited),
        "correct_citations": sorted(correct),
        "cite_precision": round(len(correct) / len(cited), 2) if cited else None,
        "cite_recall": round(len(correct) / len(valid), 2) if valid else None,
    }


def db_stats() -> dict:
    out: dict = {}
    try:
        _cols, rows = stores.sql_rows("""
            SELECT (SELECT count(*) FROM filing),
                   (SELECT count(*) FROM company),
                   (SELECT count(*) FROM filing_section),
                   (SELECT count(*) FROM section_chunk),
                   (SELECT count(*) FROM subsidiary),
                   (SELECT count(*) FROM reporting_owner),
                   pg_size_pretty(pg_database_size(current_database()))
        """)
        r = rows[0]
        out["postgres"] = {"ok": True, "filings": r[0], "companies": r[1],
                           "sections": r[2], "chunks": r[3], "subsidiaries": r[4],
                           "owner_rows": r[5], "size": r[6]}
    except Exception as e:  # noqa: BLE001
        out["postgres"] = {"ok": False, "error": str(e)[:200]}
    try:
        n = stores.cypher_rows("MATCH (n) RETURN count(n) AS c")[0]["c"]
        e_ = stores.cypher_rows("MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]
        out["neo4j"] = {"ok": True, "nodes": n, "edges": e_}
    except Exception as e:  # noqa: BLE001
        out["neo4j"] = {"ok": False, "error": str(e)[:200]}
    out["api_key"] = bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())
    out["model"] = DEEPSEEK_MODEL
    return out


# ── routes ──────────────────────────────────────────────────────────────────
@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"


@app.get("/api/meta")
def meta() -> dict:
    return {
        "stats": db_stats(),
        "presets": [
            {"id": q["id"], "kind": q["kind"], "question": q["question"],
             "expect": q["expect_winner"],
             "valid_accessions": sorted(q["valid_accessions"]),
             "required": q["required_entities"]}
            for q in QUESTIONS
        ],
        "usage": USAGE.snapshot(),
    }


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: str
    question_id: str | None = None


@app.post("/api/ask")
def ask(body: AskBody) -> JSONResponse:
    if body.mode not in MODES:
        return JSONResponse({"error": f"Unknown mode {body.mode!r}."}, status_code=400)
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return JSONResponse(
            {"error": "DEEPSEEK_API_KEY is not set on the server."}, status_code=503)

    question = body.question.strip()
    begin_request()
    t0 = time.time()
    try:
        with _mode_locks[body.mode]:
            out = MODES[body.mode].run(question, verbose=False)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(
            {"mode": body.mode, "error": f"{type(e).__name__}: {e}",
             "elapsed_s": round(time.time() - t0, 2)},
            status_code=500,
        )

    mine = request_usage()
    out = dict(out)
    out.update({
        "elapsed_s": round(time.time() - t0, 2),
        "tokens": mine["total_tokens"],
        "llm_calls": mine["calls"],
        "trace": build_trace(body.mode, out),
        "score": score_against_preset(body.question_id, out.get("answer", "")),
    })
    return JSONResponse(out)


@app.on_event("startup")
def warm() -> None:
    """Load the embedding model at boot so the first query is not slow."""
    try:
        stores.encode_query("warm up the embedding model")
        print("embedder ready", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"embedder warmup failed: {e}", flush=True)
