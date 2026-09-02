"""Thin data-access layer shared by all three retrieval modes."""
from __future__ import annotations
import functools, os, re, threading

import psycopg
from neo4j import GraphDatabase

from config import (PG_DSN, NEO4J_URI, NEO4J_USER, NEO4J_PASS, EMBED_MODEL)


# ── Postgres ────────────────────────────────────────────────────────────────
# One connection PER THREAD. A single shared psycopg connection is not safe for
# concurrent use, and the local server runs all three retrieval modes in parallel
# threads - a shared handle interleaves cursors and corrupts result sets.
_local = threading.local()


def pg():
    conn = getattr(_local, "pg", None)
    if conn is None or conn.closed:
        conn = psycopg.connect(PG_DSN, autocommit=True)
        # pgvector applies a WHERE clause AFTER the HNSW walk has picked its
        # ef_search candidates, so a selective filter can silently return fewer
        # rows than requested - measured: filtering to one company returned 0 of 8
        # while 164 rows matched. Iterative scan (pgvector >= 0.8) re-walks the
        # graph until the LIMIT is satisfied. Harmless when unfiltered.
        try:
            conn.execute("SET hnsw.iterative_scan = relaxed_order")
        except Exception:                     # noqa: BLE001  (older pgvector)
            pass
        # LLM-authored SQL is untrusted for COST as well as for correctness. On the
        # multi-hop questions the model joins subsidiary x filing_section x
        # reporting_owner without narrowing first; measured worst case was 445s for
        # a single SELECT. Unbounded, that is a denial of service against your own
        # warehouse, so every connection carries a deadline. 0 disables it, which is
        # what the archived benchmark ran with.
        ms = int(os.environ.get("PG_STATEMENT_TIMEOUT_MS", "45000"))
        if ms > 0:
            conn.execute(f"SET statement_timeout = {ms}")
        _local.pg = conn
    return conn


def pg_close_thread() -> None:
    """Release this thread's connection (call when a worker thread retires)."""
    conn = getattr(_local, "pg", None)
    if conn is not None and not conn.closed:
        conn.close()
    _local.pg = None


def sql_rows(query: str, params=None, limit: int = 200):
    """Run a read-only query. Returns (columns, rows). Raises on non-SELECT."""
    if not re.match(r"^\s*(select|with)\b", query, re.I):
        raise ValueError("only SELECT/WITH statements are allowed")
    if re.search(r"\b(insert|update|delete|drop|alter|truncate|create|grant|copy)\b", query, re.I):
        raise ValueError("statement contains a write keyword")
    with pg().cursor() as cur:
        cur.execute(query)
        cols = [d.name for d in (cur.description or [])]
        rows = cur.fetchmany(limit)
    return cols, rows


def fetch_section(accession: str, item_code: str) -> str | None:
    with pg().cursor() as cur:
        cur.execute("""SELECT section_text FROM filing_section
                       WHERE accession_number = %s AND item_code = %s""",
                    (accession, item_code))
        r = cur.fetchone()
    return r[0] if r else None


def fetch_snippets(accession: str, item_code: str, terms: list[str],
                   *, window: int = 900, max_hits: int = 3) -> list[str]:
    """Pull the passages around `terms` inside one filing section.

    This is the GraphRAG payoff: the graph decided WHICH filing to read, so the
    text lookup is an exact, cheap, provenance-linked slice rather than a search.
    """
    text = fetch_section(accession, item_code)
    if not text:
        return []
    out, used = [], []
    for term in terms:
        for m in re.finditer(re.escape(term), text, re.I):
            s, e = max(0, m.start() - window // 2), min(len(text), m.end() + window // 2)
            if any(abs(s - u) < window for u in used):
                continue
            used.append(s)
            out.append(re.sub(r"\s+", " ", text[s:e]).strip())
            if len(out) >= max_hits:
                return out
    return out


# ── Neo4j ───────────────────────────────────────────────────────────────────
@functools.lru_cache(maxsize=1)
def neo():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))


def cypher_rows(query: str, limit: int = 200, **params):
    if re.search(r"\b(create|merge|delete|detach|set|remove|drop|load\s+csv)\b", query, re.I):
        raise ValueError("write Cypher is not allowed")
    with neo().session() as s:
        res = s.run(query, **params)
        rows = [r.data() for r in res][:limit]
    return rows


# ── embeddings (vector-RAG baseline) ────────────────────────────────────────
_embed_lock = threading.Lock()      # guards model construction
_encode_lock = threading.Lock()     # guards every forward pass - see note below
_embed_model = None


def embedder():
    """Load the model once. Guarded: two modes can ask for it at the same time."""
    global _embed_model
    if _embed_model is None:
        with _embed_lock:
            if _embed_model is None:
                import torch
                # Pin intra-op threads to 1. Torch's OpenMP runtime and the other
                # native libs in this process (psycopg, neo4j) coexist badly on
                # macOS: concurrent encode() calls SIGSEGV'd the interpreter
                # (exit 139) rather than merely running slowly.
                torch.set_num_threads(1)
                from sentence_transformers import SentenceTransformer
                _embed_model = SentenceTransformer(EMBED_MODEL)
    return _embed_model


def encode_query(text: str):
    """Embed one string. Serialised: a query embedding is ~15 ms, so the mutex
    costs nothing and removes the concurrent-forward-pass crash entirely."""
    with _encode_lock:
        return embedder().encode([text], normalize_embeddings=True)[0]


def vector_search(question: str, k: int = 8, item_codes: tuple[str, ...] | None = None):
    vec = encode_query(question)
    lit = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
    where = "WHERE item_code = ANY(%s)" if item_codes else ""
    with pg().cursor() as cur:
        cur.execute(f"""
            SELECT accession_number, item_code, company_cik, company_name,
                   chunk_index, chunk_text,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM section_chunk
            {where}
            ORDER BY embedding <=> (%s)::vector
            LIMIT %s
        """, [lit] + ([list(item_codes)] if item_codes else []) + [lit, k])
        return cur.fetchall()
