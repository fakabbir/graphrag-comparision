#!/usr/bin/env python
"""Re-score a saved benchmark result set against the current ground truth.

The model answers are already recorded, so scoring is pure and deterministic -
no LLM calls, no database reads. Use this when a ground-truth defect is found
after a run, instead of paying for 180 fresh attempts.

    python scripts/rescore.py results/bench20.json
"""
from __future__ import annotations
import json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))
import benchmark                                                  # noqa: E402
from questions import QUESTIONS                                   # noqa: E402

path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text())
byid = {q["id"]: q for q in QUESTIONS}

changed = []
for r in data["results"]:
    q = byid.get(r["question_id"])
    if q is None:
        print(f"  ! {r['question_id']} is no longer in the question set; left as-is")
        continue
    old, new = r["score"], benchmark.score(q, r.get("answer") or "")
    if (old.get("correct"), old.get("hallucinated")) != (new["correct"], new["hallucinated"]):
        changed.append((r["question_id"], r["mode"], r["trial"],
                        old.get("correct"), new["correct"]))
    r["score"] = new

print(f"{len(changed)} of {len(data['results'])} attempts changed verdict")
for qid, mode, trial, was, now in changed:
    print(f"   {qid:6} {mode:12} trial {trial}: correct {was} -> {now}")

tot = {}
for r in data["results"]:
    t = tot.setdefault(r["mode"], {"passes": 0, "runs": 0})
    t["runs"] += 1
    t["passes"] += bool(r["score"]["correct"])
print("\ntotals after re-scoring:")
for m, t in tot.items():
    print(f"   {m:12} {t['passes']}/{t['runs']}")

path.write_text(json.dumps(data))
print(f"\nwrote {path}")
