#!/usr/bin/env python3
"""Derive facts.json from a stored benchmark result set.

    python3 build_facts.py ../graphrag-comparision/results/bench21.json

facts.json is the single source of numbers for the paper: build_assets.py reads
it to generate every table and chart, and verify_numbers.py asserts against it.
This script is the only thing that writes it.

It exists because the first facts.json was produced by an ad-hoc heredoc, which
meant the chain from stored answers to printed tables could not be re-run. The
corpus block is carried over from the previous facts.json, since those counts
come from the load rather than from scoring.
"""
from __future__ import annotations
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
MODES = ["text_to_sql", "vector_rag", "graphrag"]

src = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                   else HERE.parent / "graphrag-comparision/results/bench21.json")
raw = json.loads(src.read_text())
RS = raw["results"]
prev = json.loads((HERE / "facts.json").read_text()) if (HERE / "facts.json").exists() else {}

QMETA = {}
sys.path.insert(0, str(HERE.parent / "graphrag-comparision/app"))
from questions import KILLER_ID, QUESTIONS  # noqa: E402
for q in QUESTIONS:
    QMETA[q["id"]] = q


def label(sc: dict, qid: str) -> str:
    """One exclusive label per attempt, matching the scorer's own vocabulary."""
    q = QMETA[qid]
    if sc.get("hallucinated"):
        return "halluc"
    if sc.get("correct"):
        return "pass"
    if (q["valid_accessions"] and sc.get("entity_recall") == 1.0
            and not sc.get("forbidden_present") and bool(sc.get("keywords_found"))
            and not sc.get("correct_citations")):
        return "uncited"
    if sc.get("refused"):
        return "refused"
    return "fail"


def attempts(qid: str, mode: str) -> list[dict]:
    return sorted([r for r in RS if r["question_id"] == qid and r["mode"] == mode],
                  key=lambda r: r["trial"])


def n_attempts(r: dict, key: str) -> int:
    v = r.get(key)
    return len(v) if isinstance(v, list) else (v or 1)


ids = sorted({r["question_id"] for r in RS},
             key=lambda i: (i.split("-")[0], int(i.split("-")[1])))

# ── per-question grid ────────────────────────────────────────────────────────
grid = []
for qid in ids:
    row = {"id": qid, "type": QMETA[qid]["type"]}
    for m in MODES:
        row[m] = [label(r["score"], qid) for r in attempts(qid, m)]
    grid.append(row)

# ── per-type rollup ──────────────────────────────────────────────────────────
types = []
for tid in ("T1", "T2", "T3", "T4"):
    qs = [g for g in grid if g["type"] == tid]
    first = QMETA[qs[0]["id"]]
    row = {"id": tid, "kind": first["kind"], "expect": first["expect_winner"], "modes": {}}
    for m in MODES:
        st = [s for g in qs for s in g[m]]
        sub = [r for r in RS if r["mode"] == m and r["type"] == tid]
        row["modes"][m] = {
            "attempts_pass": st.count("pass"), "attempts": len(st),
            "q_solid": sum(1 for g in qs if all(s == "pass" for s in g[m])),
            "q_partial": sum(1 for g in qs
                             if any(s == "pass" for s in g[m])
                             and not all(s == "pass" for s in g[m])),
            "questions": len(qs),
            "halluc": st.count("halluc"), "uncited": st.count("uncited"),
            "refused": st.count("refused"), "fail": st.count("fail"),
            "lat_mean": round(sum(r["elapsed_s"] for r in sub) / len(sub), 1),
            "lat_max": round(max(r["elapsed_s"] for r in sub), 1),
            "tokens": sum(r.get("tokens") or 0 for r in sub),
        }
    types.append(row)

# ── totals ───────────────────────────────────────────────────────────────────
totals = {}
for m in MODES:
    sub = [r for r in RS if r["mode"] == m]
    st = [label(r["score"], r["question_id"]) for r in sub]
    totals[m] = {
        "passes": st.count("pass"), "runs": len(sub),
        "halluc": st.count("halluc"), "uncited": st.count("uncited"),
        "refused": st.count("refused"),
        "q_solid": sum(1 for g in grid if all(s == "pass" for s in g[m])),
        "q_partial": sum(1 for g in grid if any(s == "pass" for s in g[m])
                         and not all(s == "pass" for s in g[m])),
        "questions": len(grid),
        "tokens": sum(r.get("tokens") or 0 for r in sub),
        "calls": sum(r.get("llm_calls") or 0 for r in sub),
        "latency": round(sum(r["elapsed_s"] for r in sub) / len(sub), 1),
        "lat_max": round(max(r["elapsed_s"] for r in sub), 1),
        "evidence": round(sum(r.get("evidence_chars") or 0 for r in sub) / len(sub)),
    }

# ── repair usage ─────────────────────────────────────────────────────────────
repair = {"vector_rag": {"total": 0, "of": len([r for r in RS if r["mode"] == "vector_rag"]),
                         "by_type": {t: 0 for t in ("T1", "T2", "T3", "T4")}}}
for m, key in (("text_to_sql", "sql_attempts"), ("graphrag", "cypher_attempts")):
    sub = [r for r in RS if r["mode"] == m]
    repair[m] = {
        "total": sum(1 for r in sub if n_attempts(r, key) > 1), "of": len(sub),
        "by_type": {t: sum(1 for r in sub if r["type"] == t and n_attempts(r, key) > 1)
                    for t in ("T1", "T2", "T3", "T4")},
    }

out = {
    "window": prev.get("window", ""),
    "corpus": prev.get("corpus", {}),
    "items": prev.get("items", []), "graph": prev.get("graph", {}),
    "pipeline": prev.get("pipeline", []), "measured": prev.get("measured", {}),
    "forms": prev.get("forms", []), "months": prev.get("months", []),
    "sic": prev.get("sic", []), "auditors": prev.get("auditors", []),
    "jurisdictions": prev.get("jurisdictions", []), "topParents": prev.get("topParents", []),
    "subDist": prev.get("subDist", []), "roles": prev.get("roles", []),
    "multiCompany": prev.get("multiCompany", []), "tables": prev.get("tables", []),
    "resolution": prev.get("resolution", {}),
    "types": types, "totals": totals, "usage": raw.get("usage", {}),
    "repair": repair, "grid": grid, "killer": KILLER_ID,
    "inconsistent": [(g["id"], m, g[m]) for g in grid for m in MODES if len(set(g[m])) > 1],
    "halluc": [{"qid": r["question_id"], "mode": r["mode"], "trial": r["trial"],
                "forbidden": r["score"]["forbidden_present"],
                "answer_head": (r.get("answer") or "")[:300].replace("\n", " ")}
               for r in RS if r["score"].get("hallucinated")],
    "questions": [{"id": q, "type": QMETA[q]["type"], "kind": QMETA[q]["kind"],
                   "expect": QMETA[q]["expect_winner"], "question": QMETA[q]["question"],
                   "valid": sorted(QMETA[q]["valid_accessions"]),
                   "required": QMETA[q]["required_entities"]} for q in ids],
}

(HERE / "facts.json").write_text(json.dumps(out, indent=1))
print(f"facts.json written from {src.name} ({len(RS)} attempts)")
for m in MODES:
    t = totals[m]
    print(f"  {m:12} {t['q_solid']:2}/20 questions  {t['passes']:2}/60 attempts  "
          f"halluc={t['halluc']} uncited={t['uncited']} refused={t['refused']}")
print("  by type (attempts passed):")
for row in types:
    print(f"    {row['id']} " + "  ".join(f"{m}={row['modes'][m]['attempts_pass']:2}/15"
                                          for m in MODES))
