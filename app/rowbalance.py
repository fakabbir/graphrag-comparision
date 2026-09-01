"""System-level guardrails applied IDENTICALLY to the SQL and Cypher paths.

Both LLM-authored query languages hit the same failure: a generated `LIMIT 50` on a
fan-out join lets one entity's hundreds of child rows consume the whole budget, so
every other entity silently disappears - and the model then reports "only one company
found" or "none exist". Prompting against it is unreliable; these two transforms make
it structurally impossible.

Applied to both modes so the comparison measures retrieval architecture, not which
prompt happened to dodge the trap.
"""
from __future__ import annotations
import re

TRAILING_LIMIT = re.compile(r"\blimit\s+(\d+)\s*;?\s*$", re.I)

ENTITY_KEY = re.compile(r"(company_?cik|issuer_?cik|parent_?cik|\bcik\b|"
                        r"company_?name|issuer_?name|company\b)", re.I)


def raise_limit(query: str, cap: int = 2000) -> tuple[str, int | None]:
    """Replace a trailing LIMIT n with LIMIT cap (or append one). Returns (query, old)."""
    q = query.strip().rstrip(";")
    m = TRAILING_LIMIT.search(q)
    if m:
        old = int(m.group(1))
        if old >= cap:
            return q, old
        return TRAILING_LIMIT.sub(f"LIMIT {cap}", q), old
    return f"{q}\nLIMIT {cap}", None


def pick_entity_column(columns: list[str]) -> str | None:
    for c in columns:
        if ENTITY_KEY.search(c):
            return c
    return None


def balance(columns: list[str], rows: list[tuple], *,
            per_entity: int = 6, total: int = 48) -> tuple[list[tuple], dict]:
    """Round-robin a fan-out result so every entity is represented.

    Returns (kept_rows, stats). Falls back to a plain head() when no entity column
    can be identified.
    """
    key = pick_entity_column(columns)
    if key is None or not rows:
        return rows[:total], {"entity_column": None, "entities": 0,
                              "rows_in": len(rows), "rows_kept": min(len(rows), total)}
    idx = columns.index(key)
    groups: dict = {}
    for r in rows:
        groups.setdefault(r[idx], []).append(r)
    kept: list[tuple] = []
    # round-robin so a big group cannot crowd out a small one
    for slot in range(per_entity):
        for g in groups.values():
            if slot < len(g) and len(kept) < total:
                kept.append(g[slot])
    return kept, {"entity_column": key, "entities": len(groups),
                  "rows_in": len(rows), "rows_kept": len(kept)}
