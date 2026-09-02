#!/usr/bin/env python
"""Stage 7 - chunk + embed the narrative sections for the Vector-RAG baseline.

Local CPU embeddings (all-MiniLM-L6-v2, 384 dims) so the baseline costs no API
tokens and the comparison isolates *retrieval strategy*, not model quality.

Memory-bounded by construction. The first version did
`cur.fetchall()` on filing_section and then built every chunk in a list: for the
12-month corpus that is 1.3 GB of section text plus ~1.09M chunk tuples, and the
OOM killer took it on an 8 GB host that also runs Neo4j and the API. Now a
server-side cursor streams one section at a time and each section's chunks are
embedded and COPY'd before the next is read, so peak memory is one section's
worth regardless of corpus size.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import PG_DSN  # noqa: E402

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import psycopg  # noqa: E402

CHUNK, OVERLAP = 1400, 200
EMBED_BATCH = 256

# Which 10-K items to embed. Defaults to Item 1A (Risk Factors) because that is
# the only section any benchmark question asks about and the only one GraphRAG
# ever reads (fetch_snippets pulls item_code='1A'). Embedding all seven items on
# the 12-month corpus is 1.10M chunks / ~5.2 GB at ~102 chunks/s = ~3 hours;
# Item 1A alone is ~508k. A focused corpus also makes the vector baseline
# STRONGER, not weaker, so the comparison stays fair.
# Set EMBED_ITEMS="1,1A,7" (or "ALL") to widen it.
EMBED_ITEMS = os.environ.get("EMBED_ITEMS", "1A")

# Honour EMBED_MODEL when set (the app host keeps the model outside data/, which
# holds regenerable staging output); fall back to the local checkout layout.
MODEL = os.environ.get(
    "EMBED_MODEL",
    str(pathlib.Path(__file__).resolve().parent.parent / "data/models/all-MiniLM-L6-v2"),
)

_WS = re.compile(r"\s+")


def chunk_text(t: str) -> list[str]:
    t = _WS.sub(" ", t).strip()
    out, i = [], 0
    while i < len(t):
        out.append(t[i:i + CHUNK])
        if i + CHUNK >= len(t):
            break
        i += CHUNK - OVERLAP
    return out


def main() -> None:
    from sentence_transformers import SentenceTransformer

    print(f"loading {MODEL} …", flush=True)
    import torch
    torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))
    model = SentenceTransformer(MODEL)
    dim = model.get_sentence_embedding_dimension()
    assert dim == 384, f"expected 384 dims, got {dim}"

    read = psycopg.connect(PG_DSN)          # streaming reader
    write = psycopg.connect(PG_DSN, autocommit=True)

    with write.cursor() as cur:
        cur.execute("TRUNCATE section_chunk RESTART IDENTITY")
        if EMBED_ITEMS.upper() == "ALL":
            item_filter, item_params = "", []
            print("embedding ALL item codes", flush=True)
        else:
            items = [x.strip() for x in EMBED_ITEMS.split(",") if x.strip()]
            item_filter = "WHERE fs.item_code = ANY(%s)"
            item_params = [items]
            print(f"embedding item codes: {', '.join(items)}", flush=True)
        cur.execute(
            "SELECT count(*), coalesce(sum(char_len), 0) FROM filing_section fs "
            + item_filter.replace("fs.", ""), item_params)
        n_sections, total_chars = cur.fetchone()
    est = total_chars // (CHUNK - OVERLAP) + n_sections
    print(f"sections {n_sections:,} · {total_chars:,} chars · ~{est:,} chunks expected",
          flush=True)

    t0 = time.time()
    done_sections = 0
    done_chunks = 0

    # A named (server-side) cursor keeps the 1.3 GB of section text on the server
    # instead of buffering the whole result set in this process.
    with read.cursor(name="sections_stream") as src:
        src.itersize = 20
        src.execute(f"""
            SELECT fs.accession_number, fs.item_code, fs.company_cik,
                   c.name, fs.section_text
            FROM filing_section fs
            JOIN company c ON c.cik = fs.company_cik
            {item_filter}
            ORDER BY fs.accession_number, fs.item_code
        """, item_params)

        buf: list[tuple] = []          # at most one section's chunks + a partial batch

        def flush(rows: list[tuple]) -> int:
            """Embed and COPY a batch. Returns rows written."""
            if not rows:
                return 0
            vecs = model.encode([r[5] for r in rows], batch_size=64,
                                show_progress_bar=False, normalize_embeddings=True)
            with write.cursor().copy("""COPY section_chunk
                (accession_number, item_code, company_cik, company_name,
                 chunk_index, chunk_text, embedding) FROM STDIN""") as cp:
                for r, v in zip(rows, vecs):
                    cp.write_row([r[0], r[1], r[2], r[3], r[4], r[5],
                                  "[" + ",".join(f"{x:.6f}" for x in v) + "]"])
            return len(rows)

        for acc, item, cik, name, text in src:
            for idx, ch in enumerate(chunk_text(text)):
                buf.append((acc, item, cik, name, idx, ch))
                if len(buf) >= EMBED_BATCH:
                    done_chunks += flush(buf)
                    buf = []
            done_sections += 1
            if done_sections % 500 == 0:
                el = time.time() - t0
                rate = done_chunks / el if el else 0
                pct = 100 * done_chunks / max(est, 1)
                eta = (est - done_chunks) / rate / 60 if rate else 0
                print(f"  {done_sections:,}/{n_sections:,} sections · "
                      f"{done_chunks:,} chunks ({pct:.0f}%) · "
                      f"{rate:.0f} chunks/s · eta {eta:.0f} min", flush=True)

        done_chunks += flush(buf)

    read.close()

    with write.cursor() as cur:
        cur.execute("""SELECT count(*),
                              pg_size_pretty(pg_total_relation_size('section_chunk'))
                       FROM section_chunk""")
        n, size = cur.fetchone()
    el = time.time() - t0
    print(f"\nsection_chunk: {n:,} rows, {size}", flush=True)
    print(f"embedded in {el/60:.1f} min ({n/el:.0f} chunks/s)", flush=True)
    write.close()


if __name__ == "__main__":
    main()
