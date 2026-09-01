#!/usr/bin/env python
"""Stage 7 - chunk + embed the narrative sections for the Vector-RAG baseline.

Local CPU embeddings (all-MiniLM-L6-v2, 384 dims) so the baseline costs no API
tokens and the comparison isolates *retrieval strategy*, not model quality.
"""
from __future__ import annotations
import sys, pathlib, re, itertools

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import PG_DSN        # noqa: E402

import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import psycopg

CHUNK, OVERLAP = 1400, 200
MODEL = str(pathlib.Path(__file__).resolve().parent.parent / "data/models/all-MiniLM-L6-v2")


def chunk_text(t: str):
    t = re.sub(r"\s+", " ", t).strip()
    i, out = 0, []
    while i < len(t):
        out.append(t[i:i + CHUNK])
        if i + CHUNK >= len(t):
            break
        i += CHUNK - OVERLAP
    return out


def main() -> None:
    from sentence_transformers import SentenceTransformer
    print(f"loading {MODEL} …")
    model = SentenceTransformer(MODEL)
    dim = model.get_sentence_embedding_dimension()
    assert dim == 384, f"expected 384 dims, got {dim}"

    conn = psycopg.connect(PG_DSN)
    cur = conn.cursor()
    cur.execute("TRUNCATE section_chunk RESTART IDENTITY")
    conn.commit()

    cur.execute("""
        SELECT fs.accession_number, fs.item_code, fs.company_cik, c.name, fs.section_text
        FROM filing_section fs JOIN company c ON c.cik = fs.company_cik
        ORDER BY fs.accession_number, fs.item_code
    """)
    rows = cur.fetchall()
    print(f"sections: {len(rows):,}")

    pending = []
    for acc, item, cik, name, text in rows:
        for idx, ch in enumerate(chunk_text(text)):
            pending.append((acc, item, cik, name, idx, ch))
    print(f"chunks  : {len(pending):,}")

    B = 256
    ins = conn.cursor()
    done = 0
    for i in range(0, len(pending), B):
        blk = pending[i:i + B]
        vecs = model.encode([b[5] for b in blk], batch_size=64,
                            show_progress_bar=False, normalize_embeddings=True)
        with ins.copy("""COPY section_chunk
            (accession_number, item_code, company_cik, company_name,
             chunk_index, chunk_text, embedding) FROM STDIN""") as cp:
            for b, v in zip(blk, vecs):
                cp.write_row([b[0], b[1], b[2], b[3], b[4], b[5],
                              "[" + ",".join(f"{x:.6f}" for x in v) + "]"])
        done += len(blk)
        if (i // B) % 10 == 0:
            print(f"  {done:,}/{len(pending):,}")
    conn.commit()

    cur.execute("SELECT count(*), pg_size_pretty(pg_total_relation_size('section_chunk')) FROM section_chunk")
    n, size = cur.fetchone()
    print(f"\nsection_chunk: {n:,} rows, {size}")
    cur.execute("ANALYZE section_chunk")
    conn.commit(); conn.close()


if __name__ == "__main__":
    main()
