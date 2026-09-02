#!/usr/bin/env python
"""Run every question through all three retrieval architectures and score them.

Scoring is programmatic against verified ground truth - no LLM judge - so the
comparison is reproducible and cannot flatter the approach we are advocating.
"""
from __future__ import annotations
import os, sys, re, json, time, argparse, pathlib, textwrap

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import mode_sql, mode_vector, mode_graphrag                       # noqa: E402
from questions import QUESTIONS                                    # noqa: E402
from llm import USAGE                                              # noqa: E402

MODES = {"text_to_sql": mode_sql, "vector_rag": mode_vector, "graphrag": mode_graphrag}
ACC_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")

REFUSAL = re.compile(
    r"(do(es)? not contain|not (?:present|available|found|included)|no (?:rows|results|"
    r"information|evidence|data)|cannot (?:be )?(?:answer|determin|find)|insufficient|"
    r"unable to|missing|not possible to answer|excerpts do not)", re.I)


def _entity_present(entity: str, low: str) -> str | None:
    """Return the alternative that matched, or None.

    An entity may list acceptable alternatives separated by "|", used where a
    question has more than one true answer - three separate filers list
    AllianceBernstein Holding L.P. in their EX-21, so naming any one of them
    answers "name the parent that lists it". Returning the matched alternative
    rather than True keeps the reported entity readable.
    """
    for alt in entity.split("|"):
        alt = alt.strip()
        if alt and alt.lower() in low:
            return alt
    return None


def _no_provenance(q: dict, sc: dict) -> bool:
    """Right entities, right keywords, no factual error - but cited no filing."""
    return bool(q["valid_accessions"]) and sc["entity_recall"] == 1.0 \
        and not sc["forbidden_present"] and bool(sc["keywords_found"]) \
        and not sc["correct_citations"]


def score(q: dict, answer: str) -> dict:
    a = answer or ""
    low = a.lower()
    ents = [m for m in (_entity_present(e, low) for e in q["required_entities"]) if m]
    anys = [t for t in q["required_any"] if t.lower() in low]
    bad  = [m for m in (_entity_present(e, low) for e in q["forbidden_entities"]) if m]

    cited = set(ACC_RE.findall(a))
    valid = q["valid_accessions"]
    correct_cites = cited & valid
    # With no ground-truth citation set (aggregate questions), any accession the model
    # cites is legitimate provenance - counting it as "wrong" mis-flagged Q4 as a
    # hallucination when the citations were in fact correct.
    wrong_cites = (cited - valid) if valid else set()

    entity_recall = len(ents) / max(len(q["required_entities"]), 1)
    cite_precision = (len(correct_cites) / len(cited)) if cited else None
    cite_recall = (len(correct_cites) / len(valid)) if valid else None

    refused = bool(REFUSAL.search(a))
    # A pass needs: every required entity, a relevant keyword, no factually wrong
    # entity, AND - where ground-truth citations exist - at least one correct citation.
    # Without the provenance requirement a model that named the right companies but
    # cited nothing scored the same as one that cited the exact filings.
    correct = (entity_recall == 1.0 and bool(anys) and not bad
               and (not valid or bool(correct_cites)))
    return {
        "correct": correct,
        "entity_recall": round(entity_recall, 2),
        "entities_found": ents,
        "entities_missing": [e.split("|")[0].strip() for e in q["required_entities"]
                             if _entity_present(e, low) is None],
        "keywords_found": anys,
        "forbidden_present": bad,
        "cited": sorted(cited),
        "correct_citations": sorted(correct_cites),
        "wrong_citations": sorted(wrong_cites),
        "cite_precision": None if cite_precision is None else round(cite_precision, 2),
        "cite_recall": None if cite_recall is None else round(cite_recall, 2),
        "refused": refused,
        # a wrong answer stated confidently is worse than an admitted failure
        "hallucinated": (not correct) and (not refused) and (bool(bad) or bool(wrong_cites)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated question ids, e.g. Q3")
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--out", default="results/benchmark.json")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--trials", type=int, default=1,
                    help="repeat each (question, mode) N times; LLM output is not "
                         "deterministic even at temperature 0, so single runs are noise")
    args = ap.parse_args()

    qs = QUESTIONS
    if args.only:
        want = {s.strip().upper() for s in args.only.split(",")}
        qs = [q for q in qs if q["id"] in want]
    modes = [m.strip() for m in args.modes.split(",") if m.strip() in MODES]

    results = []
    for q in qs:
        print("\n" + "=" * 100)
        print(f"{q['id']}  [{q['kind']}]   expected winner: {q['expect_winner']}")
        print("=" * 100)
        print(textwrap.fill(q["question"], 96, initial_indent="  ", subsequent_indent="  "))
        for name, trial in ((m, t) for m in modes for t in range(args.trials)):
            mod = MODES[name]
            tag = f"{name}" + (f"  trial {trial+1}/{args.trials}" if args.trials > 1 else "")
            print(f"\n--- {tag} " + "-" * max(4, 94 - len(tag)))
            before = USAGE.snapshot()
            t0 = time.time()
            try:
                out = mod.run(q["question"], verbose=not args.quiet)
                err = None
            except Exception as e:                                # noqa: BLE001
                out, err = {"answer": ""}, f"{type(e).__name__}: {e}"
                print(f"  !! mode crashed: {err}")
            elapsed = time.time() - t0
            after = USAGE.snapshot()
            sc = score(q, out.get("answer", ""))

            print(f"\n  ANSWER:\n{textwrap.indent(textwrap.fill(out.get('answer','')[:1400], 92), '    ')}")
            flag = "PASS" if sc["correct"] else ("REFUSED" if sc["refused"] else "FAIL")
            if not sc["correct"] and q["valid_accessions"] and sc["entity_recall"] == 1.0 \
                    and not sc["forbidden_present"] and not sc["correct_citations"]:
                flag += "(no-provenance)"
            print(f"\n  SCORE: {flag}  entity_recall={sc['entity_recall']} "
                  f"cite_prec={sc['cite_precision']} cite_rec={sc['cite_recall']} "
                  f"hallucinated={sc['hallucinated']}")
            if sc["entities_missing"]:
                print(f"    missing entities : {sc['entities_missing']}")
            if sc["forbidden_present"]:
                print(f"    WRONG entities   : {sc['forbidden_present']}")
            print(f"    {elapsed:.1f}s, {after['total_tokens']-before['total_tokens']:,} tokens, "
                  f"{after['calls']-before['calls']} LLM calls, "
                  f"{out.get('evidence_chars',0):,} evidence chars")

            results.append({"question_id": q["id"], "type": q.get("type"),
                            "kind": q["kind"], "mode": name, "trial": trial,
                            "error": err, "score": sc, "elapsed_s": round(elapsed, 2),
                            "tokens": after["total_tokens"] - before["total_tokens"],
                            "llm_calls": after["calls"] - before["calls"],
                            **{k: v for k, v in out.items() if k != "answer"},
                            "answer": out.get("answer", "")})

    # ── summary matrix ──────────────────────────────────────────────────────
    print("\n\n" + "=" * 100)
    print("SUMMARY".center(100))
    print("=" * 100)
    hdr = f"{'question':6s} {'kind':38s} " + " ".join(f"{m:>13s}" for m in modes)
    print(hdr); print("-" * len(hdr))
    for q in qs:
        cells = []
        for m in modes:
            rs = [x for x in results if x["question_id"] == q["id"] and x["mode"] == m]
            if not rs:
                cells.append("-")
            elif args.trials == 1:
                r = rs[0]
                cells.append("PASS" if r["score"]["correct"]
                             else "FAIL(halluc)" if r["score"]["hallucinated"]
                             else "REFUSED" if r["score"]["refused"] else "FAIL")
            else:
                p_ = sum(1 for r in rs if r["score"]["correct"])
                h_ = sum(1 for r in rs if r["score"]["hallucinated"])
                np_ = sum(1 for r in rs if _no_provenance(q, r["score"]))
                extra = (f" h{h_}" if h_ else "") + (f" p{np_}" if np_ else "")
                cells.append(f"{p_}/{len(rs)}{extra}")
        print(f"{q['id']:6s} {q['kind'][:38]:38s} " + " ".join(f"{c:>13s}" for c in cells))

    # per-type rollup: with 5 questions per type the interesting number is the
    # rate within a type, not the individual question.
    types = sorted({q.get("type", q["id"]) for q in qs})
    if len(types) > 1:
        print("\n" + "BY QUESTION TYPE".center(100))
        print("-" * 100)
        hdr = f"{'type':6s} {'kind':38s} " + " ".join(f"{m:>13s}" for m in modes)
        print(hdr)
        for t in types:
            tq = [q for q in qs if q.get("type") == t]
            kind = tq[0]["kind"][:38] if tq else ""
            cells = []
            for m in modes:
                rs = [r for r in results if r["mode"] == m
                      and any(r["question_id"] == q["id"] for q in tq)]
                p_ = sum(1 for r in rs if r["score"]["correct"])
                h_ = sum(1 for r in rs if r["score"]["hallucinated"])
                cells.append(f"{p_}/{len(rs)}" + (f" h{h_}" if h_ else ""))
            print(f"{t:6s} {kind:38s} " + " ".join(f"{c:>13s}" for c in cells))

    print("\n  key: N/3 = passes;  hN = hallucinations;  pN = right answer, no citation\n")
    print(f"{'':44s} " + " ".join(f"{m:>13s}" for m in modes))
    for label, fn in [
        ("passes", lambda rs: f"{sum(1 for r in rs if r['score']['correct'])}/{len(rs)}"),
        ("hallucinations", lambda rs: str(sum(1 for r in rs if r['score']['hallucinated']))),
        ("honest refusals", lambda rs: str(sum(1 for r in rs if r['score']['refused'] and not r['score']['correct']))),
        ("right but uncited", lambda rs: str(sum(1 for r in rs
                                                 if _no_provenance(next(q for q in QUESTIONS
                                                                        if q['id'] == r['question_id']),
                                                                   r['score'])))),
        ("tokens (total)", lambda rs: f"{sum(r['tokens'] for r in rs):,}"),
        ("avg latency s", lambda rs: f"{sum(r['elapsed_s'] for r in rs)/max(len(rs),1):.1f}"),
        ("avg evidence chars", lambda rs: f"{sum(r.get('evidence_chars',0) for r in rs)//max(len(rs),1):,}"),
    ]:
        row = []
        for m in modes:
            rs = [r for r in results if r["mode"] == m]
            row.append(fn(rs))
        print(f"{label:44s} " + " ".join(f"{c:>13s}" for c in row))

    print(f"\nTOTAL LLM USAGE: {USAGE.snapshot()}")

    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"usage": USAGE.snapshot(), "results": results}, indent=2, default=str))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
