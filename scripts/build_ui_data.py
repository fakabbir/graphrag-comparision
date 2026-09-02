#!/usr/bin/env python
"""Turn a saved benchmark result set into the JSON the website reads.

    python scripts/build_ui_data.py results/bench20.json \
        ../trussk-landing-page/src/graphrag/data/benchmark.json

Pure transform - no LLM, no database. The corpus block is carried over from the
existing file, because those counts come from the load and not from scoring.

Written after the first hand-built version of benchmark.json drifted out of step
with the result set it claimed to summarise.
"""
from __future__ import annotations
import json, pathlib, sys

MODES = ["text_to_sql", "vector_rag", "graphrag"]


def status(sc: dict, q: dict) -> str:
    """One label per attempt, matching the vocabulary the UI legend explains."""
    if sc.get("hallucinated"):
        return "halluc"
    if sc.get("correct"):
        return "pass"
    # right entities, right keywords, no factual error, but cited no filing
    if (q["validAccessions"] and sc.get("entity_recall") == 1.0
            and not sc.get("forbidden_present") and bool(sc.get("keywords_found"))
            and not sc.get("correct_citations")):
        return "uncited"
    if sc.get("refused"):
        return "refused"
    return "fail"


def sample(runs: list[dict], q: dict) -> dict:
    """The attempt shown in the case study: prefer a pass, else the first."""
    r = next((x for x in runs if x["score"].get("correct")), runs[0])
    sc = r["score"]
    return {
        "status": status(sc, q),
        "answer": r.get("answer") or "",
        "query": r.get("sql") or r.get("cypher"),
        "queryLang": "sql" if r.get("sql") else ("cypher" if r.get("cypher") else None),
        "rows": r.get("row_count", r.get("graph_rows")),
        "retrieved": [
            {"sim": h.get("similarity", h.get("sim", 0)), "company": h.get("company"),
             "item": h.get("item_code", h.get("item"))}
            for h in (r.get("retrieved") or [])
        ],
        "expanded": [
            {"cik": e.get("cik"), "name": e.get("name"),
             "subsidiaryCount": e.get("subsidiary_count", e.get("subsidiaryCount", 0)),
             "riskFilings": e.get("risk_filings", e.get("riskFilings", []))}
            for e in (r.get("companies_expanded") or [])
        ],
        "snippets": r.get("snippets"),
        "textTerms": r.get("text_terms") or [],
        "evidence": r.get("evidence_chars"),
        "tokens": r.get("tokens"),
        "calls": r.get("llm_calls"),
        "latency": r.get("elapsed_s"),
        "entityRecall": sc.get("entity_recall"),
        "entitiesFound": sc.get("entities_found") or [],
        "entitiesMissing": sc.get("entities_missing") or [],
        "forbidden": sc.get("forbidden_present") or [],
        "cited": sc.get("cited") or [],
        "correctCites": sc.get("correct_citations") or [],
        "citePrecision": sc.get("cite_precision"),
        "citeRecall": sc.get("cite_recall"),
    }


def rollup(runs: list[dict], statuses: list[str]) -> dict:
    return {
        "passes": sum(1 for s in statuses if s == "pass"),
        "total": len(statuses),
        "halluc": sum(1 for s in statuses if s == "halluc"),
        "uncited": sum(1 for s in statuses if s == "uncited"),
        "refused": sum(1 for s in statuses if s == "refused"),
    }


def main() -> None:
    src, dst = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    data = json.loads(src.read_text())
    rs = data["results"]
    prev = json.loads(dst.read_text()) if dst.exists() else {}

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))
    from questions import KILLER_ID, QUESTIONS
    meta = {q["id"]: q for q in QUESTIONS}

    order = sorted({r["question_id"] for r in rs},
                   key=lambda i: (i.split("-")[0], int(i.split("-")[1])))
    questions, per_type = [], {}
    for qid in order:
        m = meta[qid]
        q = {
            "id": qid, "type": m["type"], "kind": m["kind"], "expect": m["expect_winner"],
            "question": m["question"],
            "validAccessions": sorted(m["valid_accessions"]),
            "required": m["required_entities"],
            "runs": {},
        }
        for mode in MODES:
            got = sorted([r for r in rs if r["question_id"] == qid and r["mode"] == mode],
                         key=lambda r: r["trial"])
            st = [status(r["score"], q) for r in got]
            q["runs"][mode] = {"passes": st.count("pass"), "trialStatuses": st,
                               "sample": sample(got, q)}
            per_type.setdefault(m["type"], {}).setdefault(mode, []).extend(st)
        questions.append(q)

    types = []
    for tid in sorted(per_type):
        first = next(q for q in questions if q["type"] == tid)
        types.append({
            "id": tid, "kind": first["kind"], "expect": first["expect"],
            "runs": {mode: rollup([], per_type[tid][mode]) for mode in MODES},
        })

    totals = {}
    for mode in MODES:
        got = [r for r in rs if r["mode"] == mode]
        st = [status(r["score"], next(q for q in questions if q["id"] == r["question_id"]))
              for r in got]
        totals[mode] = {
            "passes": st.count("pass"), "runs": len(got),
            "halluc": st.count("halluc"), "refused": st.count("refused"),
            "uncited": st.count("uncited"),
            "tokens": sum(r.get("tokens") or 0 for r in got),
            "latency": round(sum(r.get("elapsed_s") or 0 for r in got) / max(len(got), 1), 1),
            "evidence": round(sum(r.get("evidence_chars") or 0 for r in got) / max(len(got), 1)),
        }

    out = {
        "modes": MODES,
        "types": types,
        "questions": questions,
        "totals": totals,
        "killerId": KILLER_ID,
        "corpus": prev.get("corpus", {}),
        "usage": data.get("usage", prev.get("usage", {})),
    }
    dst.write_text(json.dumps(out, indent=1))
    print(f"wrote {dst} ({dst.stat().st_size:,} bytes)")
    for mode in MODES:
        t = totals[mode]
        print(f"   {mode:12} {t['passes']}/{t['runs']}  halluc={t['halluc']} "
              f"refused={t['refused']} uncited={t['uncited']}")


if __name__ == "__main__":
    main()
