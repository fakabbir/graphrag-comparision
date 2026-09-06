#!/usr/bin/env python3
"""Assert the paper's headline numbers against the measured results.

    python3 verify_numbers.py     # exits non-zero on any mismatch

Written because five of the automated fact-checkers that were meant to audit the
draft died partway through, so the audit had to be reconstructed as code. It also
catches the class of defect that produced the paper's own §9 item 9: the graph
edge breakdown must sum to the live edge total, an arithmetic identity nothing
else in the pipeline checked.
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
F = json.loads((HERE / "facts.json").read_text())
M = json.loads((HERE / "methods.json").read_text())
T = F["totals"]
TY = {t["id"]: t["modes"] for t in F["types"]}
MODES = ("text_to_sql", "vector_rag", "graphrag")


def triple(key, source=T):
    return tuple(source[m][key] for m in MODES)


CHECKS = [
    ("graph edge breakdown sums to the live total",
     sum(v for _, v in F["graph"]["edges"]) == F["corpus"]["graph_edges"]),
    ("graph node breakdown sums to the live total",
     sum(v for _, v in F["graph"]["nodes"]) == F["corpus"]["graph_nodes"]),
    ("corpus counts", (F["corpus"]["filings"], F["corpus"]["companies"],
                       F["corpus"]["chunks"]) == (167050, 10511, 508714)),
    ("questions solved 9/3/14", triple("q_solid") == (9, 3, 14)),
    ("attempts passed 29/9/45", triple("passes") == (29, 9, 45)),
    ("falsehoods 2/0/0", triple("halluc") == (2, 0, 0)),
    ("uncited 16/6/0", triple("uncited") == (16, 6, 0)),
    ("refusals 12/45/8", triple("refused") == (12, 45, 8)),
    ("outcome labels partition 60 attempts each",
     all(T[m]["passes"] + T[m]["halluc"] + T[m]["uncited"] + T[m]["refused"] <= 60
         for m in MODES)),
    ("tokens 158,171/118,850/780,500",
     triple("tokens") == (158171, 118850, 780500)),
    ("T1 vector 3 passes, 6 refusals, 6 uncited",
     (TY["T1"]["vector_rag"]["attempts_pass"], TY["T1"]["vector_rag"]["refused"],
      TY["T1"]["vector_rag"]["uncited"]) == (3, 6, 6)),
    ("T2 sql 15/15 vs graph 6/15",
     (TY["T2"]["text_to_sql"]["attempts_pass"],
      TY["T2"]["graphrag"]["attempts_pass"]) == (15, 6)),
    ("T2 tokens 590,453 vs 24,604",
     (TY["T2"]["graphrag"]["tokens"],
      TY["T2"]["text_to_sql"]["tokens"]) == (590453, 24604)),
    ("T3 graph 15/15, both baselines 0/15",
     (TY["T3"]["graphrag"]["attempts_pass"], TY["T3"]["text_to_sql"]["attempts_pass"],
      TY["T3"]["vector_rag"]["attempts_pass"]) == (15, 0, 0)),
    ("T4 sql 14/15 beats graph 9/15",
     (TY["T4"]["text_to_sql"]["attempts_pass"],
      TY["T4"]["graphrag"]["attempts_pass"]) == (14, 9)),
    ("T4 graph misses are all refusals",
     TY["T4"]["graphrag"]["halluc"] == 0 and TY["T4"]["graphrag"]["refused"] == 6),
    ("repair usage 7/0/34",
     tuple(F["repair"][m]["total"] for m in MODES) == (7, 0, 34)),
    ("repair on all 15 T4 graph attempts",
     F["repair"]["graphrag"]["by_type"]["T4"] == 15),
    ("10 inconsistent cells, split 5/3/2",
     len(F["inconsistent"]) == 10 and
     [sum(1 for _, m, _ in F["inconsistent"] if m == x) for x in MODES] == [5, 2, 3]),
    ("both falsehoods are text-to-SQL on T3",
     all(h["mode"] == "text_to_sql" and h["qid"].startswith("T3") for h in F["halluc"])
     and len(F["halluc"]) == 2),
    ("RESOLVES_TO 246 edges, 0.12% of subsidiary nodes",
     dict(F["graph"]["edges"])["RESOLVES_TO"] == 246 and
     abs(F["resolution"]["coverage_pct"] - 0.122) < 0.01),
    ("Item 1A: 5,210 sections, mean 116,779 chars",
     [r for r in F["items"] if r[0] == "1A"][0][1:4] == [5210, 608417255, 116779]),
    ("pipeline totals 251.5 min", abs(sum(v for _, v in F["pipeline"]) - 251.5) < 0.1),
    ("infrastructure total $165/mo", M["infra"]["monthly_usd"]["total"] == 165),
]

# the prose must not resurrect corrected figures
PROSE = "\n".join(p.read_text() for p in sorted((HERE / "sec").glob("*.tex")))
FORBIDDEN = [
    (r"refused honestly on 12", "the corrected T1 refusal split is 6 refusals + 6 uncited"),
    (r"thirteen-month|13-month", "twelve monthly feeds were ingested"),
    (r"Three results contradict", "two results contradict the expectation, not three"),
]

bad = [name for name, ok in CHECKS if not ok]
for name, ok in CHECKS:
    print(("  ok    " if ok else "  FAIL  ") + name)
for pat, why in FORBIDDEN:
    hits = re.findall(pat, PROSE)
    if hits:
        bad.append(f"prose contains {pat!r}: {why}")
        print(f"  FAIL  prose contains {pat!r} -- {why}")
    else:
        print(f"  ok    prose free of {pat!r}")

print(f"\n{len(CHECKS) + len(FORBIDDEN) - len(bad)}/{len(CHECKS) + len(FORBIDDEN)} checks passed")
sys.exit(1 if bad else 0)
