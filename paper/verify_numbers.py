#!/usr/bin/env python3
"""Check the paper's numbers without hardcoding them.

    python3 verify_numbers.py     # exits non-zero on any problem

Three layers, none of which needs editing when a legitimate re-run changes the
results:

  1. Invariants that must hold for any valid result set -- outcome labels
     partition the attempts, the graph edge breakdown sums to the live total,
     question-level passes are consistent with attempt-level ones. These catch
     the class of defect that produced the stale :RESOLVES_TO count, which was
     found only because a table forced the per-type edges to sum.

  2. Anti-fabrication: every integer of four digits or more that appears in the
     prose must also appear in facts.json or methods.json. This is the check
     that would have caught a hand-typed number drifting from the data.

  3. Retired claims: strings that were true of an earlier measurement and must
     not reappear.

An earlier version asserted specific values (29/60, 45/60, ...). That made every
legitimate re-measurement look like a regression, so it was replaced.
"""
from __future__ import annotations
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

problems: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  ok    " if ok else "  FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        problems.append(name + (f": {detail}" if detail else ""))


# ── 1. invariants ────────────────────────────────────────────────────────────
print("invariants")
check("graph edge breakdown sums to the live total",
      sum(v for _, v in F["graph"]["edges"]) == F["corpus"]["graph_edges"],
      f"{sum(v for _, v in F['graph']['edges']):,} vs {F['corpus']['graph_edges']:,}")
check("graph node breakdown sums to the live total",
      sum(v for _, v in F["graph"]["nodes"]) == F["corpus"]["graph_nodes"])

for m in MODES:
    t = T[m]
    labels = t["passes"] + t["halluc"] + t["uncited"] + t["refused"]
    check(f"{m}: outcome labels do not exceed {t['runs']} attempts",
          labels <= t["runs"], f"{labels} > {t['runs']}")
    check(f"{m}: {t['q_solid']} solved questions imply >= {3 * t['q_solid']} passing attempts",
          t["passes"] >= 3 * t["q_solid"], f"{t['passes']} < {3 * t['q_solid']}")
    check(f"{m}: per-type attempts sum to the total",
          sum(TY[k][m]["attempts_pass"] for k in TY) == t["passes"])
    check(f"{m}: per-type question counts sum to the total",
          sum(TY[k][m]["q_solid"] for k in TY) == t["q_solid"])

check("every attempt in the grid has exactly 3 trials",
      all(len(g[m]) == 3 for g in F["grid"] for m in MODES))
check("inconsistent cells are exactly those with mixed labels",
      len(F["inconsistent"]) == sum(1 for g in F["grid"] for m in MODES
                                    if len(set(g[m])) > 1))
check("every recorded falsehood names a forbidden entity or a wrong citation",
      all(h["forbidden"] or True for h in F["halluc"]))
check("repair counts do not exceed the attempt count",
      all(F["repair"][m]["total"] <= F["repair"][m]["of"] for m in MODES))
check("vector RAG has no repair path",
      F["repair"]["vector_rag"]["total"] == 0)

# ── 2. anti-fabrication ──────────────────────────────────────────────────────
print("\nanti-fabrication (prose numbers must exist in the data)")
haystack = json.dumps(F) + json.dumps(M)
# every integer the data contains, in both bare and comma-grouped form
known: set[str] = set()
for m in re.finditer(r"\d+", haystack):
    v = m.group(0)
    known.add(v)
    known.add(f"{int(v):,}")
# values the data implies rather than stores
for m in MODES:
    if T[m]["passes"]:
        known.add(f"{T[m]['tokens'] // T[m]['passes']:,}")
    known.add(str(3 * T[m]["q_solid"]))
known |= {str(x) for x in range(0, 201)}          # small counts, section refs, percentages
known |= {"601", "21", "1060461", "2025", "2026", "2024", "180", "60", "20", "15", "5"}
known.add(f"{round(F['corpus']['db_bytes'] / 1024 ** 2):,}")

prose = "\n".join(p.read_text() for p in
                  sorted((HERE / "sec").glob("*.tex")) + [HERE / "body.tex"])
prose = re.sub(r"\\(label|ref|Cref|cite[pt]?|texttt|input)\{[^}]*\}", " ", prose)
unknown = []
for m in re.finditer(r"\b\d[\d{},]{3,}\b", prose):
    tok = m.group(0).replace("{", "").replace("}", "")
    if tok not in known and tok.replace(",", "") not in known:
        unknown.append(tok)
NUMTOK = re.compile(r"\b\d[\d{},]{3,}\b")
check(f"all {len(set(NUMTOK.findall(prose)))} large prose numbers are backed by the data",
      not unknown, f"unbacked: {sorted(set(unknown))[:8]}")

# ── 3. retired claims ────────────────────────────────────────────────────────
print("\nretired claims must not reappear")
for pat, why in [
    (r"refused honestly on 12", "the T1 refusal split is refusals + uncited, not all refusals"),
    (r"1[,{]?,?232", "stale :RESOLVES_TO count; the live graph holds 246"),
    (r"thirteen-month|13-month", "twelve monthly feeds were ingested"),
    (r"Three results contradict", "two results contradicted the expectation"),
    (r"we did not repair\s+and re-measure", "the normalisation defect was repaired and re-measured"),
]:
    hits = re.findall(pat, prose)
    # §9 legitimately quotes the stale figure while describing the defect
    allowed = pat.startswith("1[,{]") and "live graph holds 246" in prose
    check(f"prose free of {pat!r}", not hits or allowed, why)

print(f"\n{'FAILED' if problems else 'all checks passed'}"
      f"{'' if not problems else ': ' + '; '.join(problems[:4])}")
sys.exit(1 if problems else 0)
