"""Baseline B - Vector RAG over 10-K narrative chunks (pgvector + MiniLM).

This is the architecture the thesis predicts will fail on relational questions:
retrieval is driven purely by semantic similarity to the question, so a fact that
lives in a *different document type* (Form 4 ownership, EX-21 subsidiary list)
can never be retrieved, no matter how good the embedding model is.
"""
from __future__ import annotations
import textwrap

from config import TOP_K, TEXT_BUDGET
from llm import ask
from stores import vector_search

SYSTEM = textwrap.dedent("""
    You are a financial-disclosure analyst. Answer ONLY from the provided excerpts.
    Cite the accession number for every claim, like [0001177394-22-000010].
    If the excerpts do not contain the answer, say exactly what is missing.
    Never guess a company relationship, ownership stake, or executive role that is
    not stated verbatim in the excerpts.
    Be concise: under 300 words.
""").strip()


def run(question: str, *, k: int = TOP_K, verbose: bool = True) -> dict:
    hits = vector_search(question, k=k)
    ctx, used = [], 0
    for acc, item, cik, name, idx, text, sim in hits:
        block = f"[{acc}] {name} (CIK {cik}) - 10-K Item {item}, chunk {idx}, sim={sim:.3f}\n{text}"
        if used + len(block) > TEXT_BUDGET:
            break
        ctx.append(block); used += len(block)

    if verbose:
        print(f"  retrieved {len(hits)} chunks, used {len(ctx)} ({used:,} chars)")
        for acc, item, cik, name, idx, _t, sim in hits[:k]:
            print(f"    sim={sim:.3f}  {name[:34]:34s} Item {item:<3s} {acc}")

    answer = ask(SYSTEM, f"Question: {question}\n\nExcerpts:\n\n" + "\n\n---\n\n".join(ctx),
                 max_tokens=1500)
    return {
        "mode": "vector_rag",
        "answer": answer,
        "evidence_chars": used,
        "chunks_used": len(ctx),
        "sources": sorted({h[0] for h in hits[:len(ctx)]}),
        "retrieved": [{"accession": h[0], "item": h[1], "company": h[3],
                       "similarity": round(h[6], 4)} for h in hits],
    }
