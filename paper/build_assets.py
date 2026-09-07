#!/usr/bin/env python3
"""Generate every table and figure in the paper from the measured results.

    python3 build_assets.py          # rewrites gen/*.tex

Nothing in gen/ is hand-edited. The prose in sec/ cites numbers, but the numbers
that appear in tables and plots are produced here from facts.json, which is
itself derived from the stored per-attempt results. This exists because the first
version of the paper's tables was written by hand and immediately drifted: a
hand-typed "max latency" row said 5.0 and 21.9 where the data says 5.2 and 21.8.

The two TikZ diagrams (fig_arch, fig_containers) are hand-authored layout with no
numeric content beyond labels, and are not regenerated here.
"""
from __future__ import annotations
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
GEN = HERE / "gen"
GEN.mkdir(exist_ok=True)

F = json.loads((HERE / "facts.json").read_text())
M = json.loads((HERE / "methods.json").read_text())

MODES = ["text_to_sql", "vector_rag", "graphrag"]
NAME = {"text_to_sql": "Text-to-SQL", "vector_rag": "Vector RAG", "graphrag": "GraphRAG"}
COL = {"text_to_sql": "sqlcol", "vector_rag": "veccol", "graphrag": "graphcol"}
T = F["totals"]


def w(name: str, body: str) -> None:
    (GEN / name).write_text(body.rstrip() + "\n")
    print(f"  gen/{name}")


def n(x) -> str:
    return f"{x:,}"


# ── Table: corpus ────────────────────────────────────────────────────────────
c = F["corpus"]
w("tab_corpus.tex", r"""\begin{tabular}{@{}lr@{}}
\toprule
\textbf{Entity} & \textbf{Count} \\
\midrule
Filings (all form types)          & %s \\
Distinct filers (companies)       & %s \\
Documents in filing manifests     & %s \\
Documents fetched and parsed      & %s \\
10-K narrative sections extracted & %s \\
Item~1A chunks embedded           & %s \\
EX-21 subsidiary rows             & %s \\
Form 3/4/5 ownership rows         & %s \\
Distinct insiders                 & %s \\
Auditor attestations              & %s \\
\midrule
Graph nodes                       & %s \\
Graph edges                       & %s \\
\midrule
Relational store on disk          & %s\,MiB \\
HNSW index                        & %.1f\,GB \\
\bottomrule
\end{tabular}""" % (
    n(c["filings"]), n(c["companies"]), n(c["documents"]), n(c["docs_fetched"]),
    n(c["sections"]), n(c["chunks"]), n(c["subsidiaries"]), n(c["ownerRows"]),
    n(c["insiders"]), n(c["auditors"]), n(c["graph_nodes"]), n(c["graph_edges"]),
    n(round(c["db_bytes"] / 1024 ** 2)), c["hnsw_bytes"] / 1e9,
))

# ── Table: 10-K items ────────────────────────────────────────────────────────
LBL = {"1": "Business", "1A": "Risk Factors", "1B": "Unresolved Staff Comments",
       "2": "Properties", "3": "Legal Proceedings",
       "7": "Management's Discussion \\& Analysis", "7A": "Market Risk"}
w("tab_items.tex", r"""\begin{tabular}{@{}llrrrr@{}}
\toprule
\textbf{Item} & \textbf{Section} & \textbf{Filings} & \textbf{Total chars} & \textbf{Mean} & \textbf{Max} \\
\midrule
%s
\bottomrule
\end{tabular}""" % "\n".join(
    r"%s & %s & %s & %s & %s & %s \\" % (
        (r"\textbf{%s}" % k) if k == "1A" else k, LBL[k], n(fl), n(ch), n(av), n(mx))
    for k, fl, ch, av, mx in F["items"]))


# ── Table: aggregate results ─────────────────────────────────────────────────
def row(label, get, fmt, better="max"):
    """Bold marks the best value in the row; better='none' disables it."""
    vals = {m: get(T[m]) for m in MODES}
    pick = (max if better == "max" else min)(vals.values())
    cells = []
    for m in MODES:
        s = fmt(vals[m])
        cells.append(r"\textbf{%s}" % s if better != "none" and vals[m] == pick else s)
    return r"%s & %s \\" % (label, " & ".join(cells))


w("tab_main.tex", r"""\begin{tabular}{@{}lccc@{}}
\toprule
& \textbf{Text-to-SQL} & \textbf{Vector RAG} & \textbf{GraphRAG} \\
\midrule
%s
\bottomrule
\end{tabular}""" % "\n".join([
    row("Questions solved on all 3 attempts", lambda x: x["q_solid"], lambda v: "%d/20" % v),
    row("Questions solved inconsistently", lambda x: x["q_partial"], lambda v: "%d" % v, "none"),
    row("Attempts passed (of 60)", lambda x: x["passes"], lambda v: "%d" % v),
    r"\midrule",
    row("Confident falsehoods", lambda x: x["halluc"],
        lambda v: (r"\textcolor{badcol}{%d}" % v) if v else "%d" % v, "min"),
    row("Correct but uncited", lambda x: x["uncited"], lambda v: "%d" % v, "min"),
    row("Explicit refusals", lambda x: x["refused"], lambda v: "%d" % v, "none"),
    r"\midrule",
    row("Mean evidence supplied (chars)", lambda x: x["evidence"], n, "none"),
    row("Mean latency (s)", lambda x: x["latency"], lambda v: "%.1f" % v, "min"),
    row("Max latency (s)", lambda x: x["lat_max"], lambda v: "%.1f" % v, "none"),
    row("Total tokens", lambda x: x["tokens"], n, "min"),
    row("Tokens per passing attempt",
        lambda x: x["tokens"] // x["passes"] if x["passes"] else 0, n, "min"),
    row("LLM calls", lambda x: x["calls"], n, "none"),
]))

# ── Table: per category ──────────────────────────────────────────────────────
lines = []
for ty in F["types"]:
    best = max(ty["modes"][m]["q_solid"] for m in MODES)
    cells = []
    for m in MODES:
        d = ty["modes"][m]
        q = (r"\textbf{%d}" % d["q_solid"]) if d["q_solid"] == best and best else "%d" % d["q_solid"]
        cells.append("%s/5 & %d/15" % (q, d["attempts_pass"]))
    lines.append(r"%s & %s & %s \\" % (ty["id"], ty["kind"], " & ".join(cells)))
w("tab_bytype.tex", r"""\begin{tabular}{@{}ll rr rr rr@{}}
\toprule
& & \multicolumn{2}{c}{\textbf{Text-to-SQL}} & \multicolumn{2}{c}{\textbf{Vector RAG}} & \multicolumn{2}{c}{\textbf{GraphRAG}} \\
\cmidrule(lr){3-4}\cmidrule(lr){5-6}\cmidrule(lr){7-8}
\textbf{Type} & \textbf{Question class} & Q & A & Q & A & Q & A \\
\midrule
%s
\bottomrule
\end{tabular}""" % "\n".join(lines))

# ── Table: every attempt ─────────────────────────────────────────────────────
GLY = {"pass": r"\gpass", "halluc": r"\ghall", "uncited": r"\gunc",
       "refused": r"\gref", "fail": r"\gfail"}
rows, prev = [], None
for g in F["grid"]:
    if prev and g["type"] != prev:
        rows.append(r"\addlinespace[2pt]")
    prev = g["type"]
    star = r"\,$\star$" if g["id"] == F["killer"] else ""
    rows.append(r"\texttt{%s}%s & %s \\" % (
        g["id"], star, " & ".join("".join(GLY[s] for s in g[m]) for m in MODES)))
w("tab_grid.tex", r"""\begin{tabular}{@{}l ccc@{}}
\toprule
\textbf{Q} & \textbf{Text-to-SQL} & \textbf{Vector RAG} & \textbf{GraphRAG} \\
\midrule
%s
\bottomrule
\end{tabular}""" % "\n".join(rows))

# ── Table: repair usage ──────────────────────────────────────────────────────
w("tab_repair.tex", r"""\begin{tabular}{@{}lccccc@{}}
\toprule
\textbf{Architecture} & \textbf{Overall} & \textbf{T1} & \textbf{T2} & \textbf{T3} & \textbf{T4} \\
\midrule
%s
\bottomrule
\end{tabular}""" % "\n".join(
    r"%s & %d/%d & %s \\" % (NAME[m], F["repair"][m]["total"], F["repair"][m]["of"],
                             " & ".join("%d/15" % F["repair"][m]["by_type"][t]
                                        for t in ("T1", "T2", "T3", "T4")))
    for m in MODES))

# ── Table: cross-model probe ─────────────────────────────────────────────────
w("tab_crossmodel.tex", r"""\begin{tabular}{@{}llp{0.44\linewidth}@{}}
\toprule
\textbf{Model} & \textbf{Verdict} & \textbf{Observed behaviour} \\
\midrule
DeepSeek V4 Flash (benchmarked) & pass    & correct three-company traversal \\
Claude Haiku 4.5 (Bedrock)      & fail    & stage-1 Cypher returned \textsc{target corp} alone; Apple and Bank of America missing \\
Amazon Nova 2 Lite (Bedrock)    & refused & produced no usable traversal \\
\bottomrule
\end{tabular}""")

# ── Table: cost ──────────────────────────────────────────────────────────────
i = M["infra"]["monthly_usd"]
w("tab_cost.tex", r"""\begin{tabular}{@{}llr@{}}
\toprule
\textbf{Resource} & \textbf{Specification} & \textbf{USD/mo} \\
\midrule
EC2 (app, graph, gateway) & m7g.xlarge, 4 vCPU / 16\,GB & %.2f \\
EC2 storage               & 100\,GB gp3                 & %.2f \\
RDS PostgreSQL            & db.t4g.medium               & %.2f \\
RDS storage               & 100\,GB gp3                 & %.2f \\
S3 + CloudFront           & demo volume                 & %.2f \\
NAT gateway               & not used (S3 VPC endpoint)  & 0.00 \\
\midrule
\textbf{Total}            &                             & \textbf{%.0f} \\
\bottomrule
\end{tabular}""" % (i["ec2"], i["ec2_storage"], i["rds"], i["rds_storage"],
                    i["s3_cloudfront"], i["total"]))

# ── Table: question set ──────────────────────────────────────────────────────
qrows = []
for q in F["questions"]:
    t = q["question"].replace("&", r"\&").replace('"', "''").replace("_", r"\_")
    qrows.append(r"\texttt{%s} & %s \\" % (q["id"], t if len(t) <= 190 else t[:187] + "..."))
w("tab_questions.tex", r"""\begin{tabular}{@{}lp{0.86\linewidth}@{}}
\toprule
\textbf{ID} & \textbf{Question} \\
\midrule
%s
\bottomrule
\end{tabular}""" % "\n".join(qrows))

# ── Figure: attempts passed by category ──────────────────────────────────────
w("fig_bytype.tex", r"""\begin{tikzpicture}
\begin{axis}[
  ybar, bar width=8pt, width=\linewidth, height=5.2cm,
  symbolic x coords={T1,T2,T3,T4}, xtick=data,
  ymin=0, ymax=16, ytick={0,5,10,15},
  ylabel={Attempts passed (of 15)}, ylabel near ticks,
  legend style={at={(0.5,1.22)}, anchor=north, legend columns=3, draw=none, font=\footnotesize},
  nodes near coords, nodes near coords style={font=\tiny},
  axis lines*=left, tick style={draw=none}, ymajorgrids, grid style={gray!20},
  enlarge x limits=0.15,
]
%s
\legend{Text-to-SQL, Vector RAG, GraphRAG}
\end{axis}
\end{tikzpicture}""" % "\n".join(
    r"\addplot[fill=%s] coordinates {%s};" % (
        COL[m], " ".join("(%s,%d)" % (t["id"], t["modes"][m]["attempts_pass"]) for t in F["types"]))
    for m in MODES))

# ── Figure: outcome composition ──────────────────────────────────────────────
resid = {m: 60 - (T[m]["passes"] + T[m]["halluc"] + T[m]["uncited"] + T[m]["refused"])
         for m in MODES}
LAYERS = [("pass", "okcol", lambda m: T[m]["passes"]),
          ("falsehood", "badcol", lambda m: T[m]["halluc"]),
          ("uncited", "warncol", lambda m: T[m]["uncited"]),
          ("refused", "gray!45", lambda m: T[m]["refused"]),
          ("other fail", "gray!20", lambda m: resid[m])]
w("fig_outcomes.tex", r"""\begin{tikzpicture}
\begin{axis}[
  ybar stacked, bar width=26pt, width=\linewidth, height=5.2cm,
  symbolic x coords={{Text-to-SQL},{Vector RAG},{GraphRAG}}, xtick=data,
  ymin=0, ymax=61, ylabel={Attempts (of 60)}, ylabel near ticks,
  legend style={at={(0.5,1.22)}, anchor=north, legend columns=5, draw=none, font=\tiny},
  axis lines*=left, tick style={draw=none}, ymajorgrids, grid style={gray!20},
  enlarge x limits=0.28, x tick label style={font=\footnotesize},
]
%s
\legend{%s}
\end{axis}
\end{tikzpicture}""" % (
    "\n".join(r"\addplot[fill=%s] coordinates {%s};" % (
        col, " ".join("({%s},%d)" % (NAME[m], fn(m)) for m in MODES))
        for _, col, fn in LAYERS),
    ", ".join(lab for lab, _, _ in LAYERS)))

# ── Figure: cost effectiveness ───────────────────────────────────────────────
ANC = {"text_to_sql": "north west", "vector_rag": "south west", "graphrag": "north east"}
w("fig_cost.tex", r"""\begin{tikzpicture}
\begin{axis}[
  width=\linewidth, height=5.4cm,
  xlabel={Total tokens consumed (thousands)}, ylabel={Attempts passed (of 60)},
  ylabel near ticks, xlabel near ticks,
  xmin=0, xmax=900, ymin=0, ymax=56,
  axis lines*=left, tick style={draw=none}, grid=both, grid style={gray!20},
]
%s
%s
\end{axis}
\end{tikzpicture}""" % (
    "\n".join(r"\addplot[only marks, mark=*, mark size=2.9pt, %s] coordinates {(%.1f,%d)};"
              % (COL[m], T[m]["tokens"] / 1000, T[m]["passes"]) for m in MODES),
    "\n".join(r"\node[anchor=%s, align=%s, font=\scriptsize, inner sep=1pt, xshift=%s]"
              "\n  at (axis cs:%.1f,%d) {\\textbf{%s}\\\\\\tiny %s tok/pass};"
              % (ANC[m], "right" if m == "graphrag" else "left",
                 "-3mm" if m == "graphrag" else "3mm",
                 T[m]["tokens"] / 1000, T[m]["passes"], NAME[m],
                 n(T[m]["tokens"] // T[m]["passes"] if T[m]["passes"] else 0))
              for m in MODES)))

# ── Figure: repair frequency ─────────────────────────────────────────────────
w("fig_repair.tex", r"""\begin{tikzpicture}
\begin{axis}[
  ybar, bar width=10pt, width=\linewidth, height=4.6cm,
  symbolic x coords={T1,T2,T3,T4}, xtick=data,
  ymin=0, ymax=16, ytick={0,5,10,15},
  ylabel={Attempts needing repair (of 15)}, ylabel near ticks,
  legend style={at={(0.5,1.25)}, anchor=north, legend columns=2, draw=none, font=\footnotesize},
  nodes near coords, nodes near coords style={font=\tiny},
  axis lines*=left, tick style={draw=none}, ymajorgrids, grid style={gray!20},
  enlarge x limits=0.18,
]
%s
\legend{Text-to-SQL, GraphRAG}
\end{axis}
\end{tikzpicture}""" % "\n".join(
    r"\addplot[fill=%s] coordinates {%s};" % (
        COL[m], " ".join("(%s,%d)" % (t, F["repair"][m]["by_type"][t])
                         for t in ("T1", "T2", "T3", "T4")))
    for m in ("text_to_sql", "graphrag")))

# ── Figure: latency ─────────────────────────────────────────────────────────
w("fig_latency.tex", r"""\begin{tikzpicture}
\begin{semilogyaxis}[
  ybar, bar width=8pt, width=\linewidth, height=4.8cm,
  symbolic x coords={T1,T2,T3,T4}, xtick=data,
  ymin=0.6, ymax=400, ylabel={Mean latency (s, log)}, ylabel near ticks,
  legend style={at={(0.5,1.25)}, anchor=north, legend columns=3, draw=none, font=\footnotesize},
  nodes near coords, nodes near coords style={font=\tiny},
  point meta=explicit symbolic,
  axis lines*=left, tick style={draw=none}, ymajorgrids, grid style={gray!20},
  enlarge x limits=0.15, log origin=infty,
]
%s
\legend{Text-to-SQL, Vector RAG, GraphRAG}
\end{semilogyaxis}
\end{tikzpicture}""" % "\n".join(
    r"\addplot[fill=%s] coordinates {%s};" % (
        COL[m], " ".join("(%s,%.2f) [%.1f]" % (t["id"],
                                               max(t["modes"][m]["lat_mean"], 0.1),
                                               t["modes"][m]["lat_mean"])
                         for t in F["types"]))
    for m in MODES))

# ── Figure: ingestion pipeline ──────────────────────────────────────────────
pl = F["pipeline"]
w("fig_pipeline.tex", r"""\begin{tikzpicture}
\begin{axis}[
  xbar, bar width=7pt, width=0.74\linewidth, height=6.0cm,
  symbolic y coords={%s}, ytick=data,
  xmin=0, xmax=132, xlabel={Wall-clock minutes}, xlabel near ticks,
  axis lines*=left, tick style={draw=none}, xmajorgrids, grid style={gray!20},
  nodes near coords, nodes near coords style={font=\tiny}, enlarge y limits=0.09,
  y tick label style={font=\footnotesize},
]
\addplot[fill=graphcol] coordinates {%s};
\end{axis}
\end{tikzpicture}""" % (
    ",".join("{%s}" % k.replace("_", " ") for k, _ in reversed(pl)),
    " ".join("(%.2f,{%s})" % (v, k.replace("_", " ")) for k, v in reversed(pl))))

# ── Figure: EX-21 skew ──────────────────────────────────────────────────────
w("fig_subdist.tex", r"""\begin{tikzpicture}
\begin{axis}[
  ybar, bar width=15pt, width=\linewidth, height=4.4cm,
  symbolic x coords={%s}, xtick=data,
  ymin=0, ylabel={Filers}, ylabel near ticks,
  xlabel={Subsidiaries disclosed in EX-21}, xlabel near ticks,
  axis lines*=left, tick style={draw=none}, ymajorgrids, grid style={gray!20},
  nodes near coords, nodes near coords style={font=\tiny}, enlarge x limits=0.13,
]
\addplot[fill=graphcol] coordinates {%s};
\end{axis}
\end{tikzpicture}""" % (
    ",".join("{%s}" % b for b, _ in F["subDist"]),
    " ".join("({%s},%d)" % (b, v) for b, v in F["subDist"])))

# ── Figure: filings per month ───────────────────────────────────────────────
w("fig_months.tex", r"""\begin{tikzpicture}
\begin{axis}[
  ybar, bar width=7pt, width=\linewidth, height=4.2cm,
  symbolic x coords={%s}, xtick=data,
  x tick label style={rotate=45, anchor=east, font=\tiny},
  ymin=0, ylabel={Filings with parsed documents}, ylabel near ticks,
  axis lines*=left, tick style={draw=none}, ymajorgrids, grid style={gray!20},
  enlarge x limits=0.05,
]
\addplot[fill=veccol] coordinates {%s};
\end{axis}
\end{tikzpicture}""" % (
    ",".join("{%s}" % k[2:] for k, _ in F["months"]),
    " ".join("({%s},%d)" % (k[2:], v) for k, v in F["months"])))

print(f"\npipeline total: {sum(v for _, v in pl):.1f} min")
print(f"graph edges: breakdown sums to {sum(v for _, v in F['graph']['edges']):,} "
      f"vs live {F['corpus']['graph_edges']:,}")
assert sum(v for _, v in F["graph"]["edges"]) == F["corpus"]["graph_edges"], \
    "edge breakdown must sum to the live total"

# ── Table: before / after the schema-documentation repair ───────────────────
# Present only when facts_before.json exists, i.e. when a repaired re-run has
# been done. The published measurement is shown alongside the repaired one
# because the repair moves a headline in the author's favour.
BEFORE = HERE / "facts_before.json"
if BEFORE.exists():
    B = json.loads(BEFORE.read_text())
    tyB = {t["id"]: t["modes"] for t in B["types"]}
    tyA = {t["id"]: t["modes"] for t in F["types"]}

    def pair(before, after, higher_is_better=True):
        """Colour by whether the movement is an improvement, not by its sign.
        A rise in confident falsehoods is a regression, not progress."""
        if before == after:
            return r"%s & \textcolor{gray!60}{--}" % before
        better = (after > before) if higher_is_better else (after < before)
        arrow = (r"\textcolor{okcol}{$\uparrow$}" if after > before
                 else r"\textcolor{badcol}{$\downarrow$}")
        if not higher_is_better:
            arrow = (r"\textcolor{badcol}{$\uparrow$}" if after > before
                     else r"\textcolor{okcol}{$\downarrow$}")
        return r"%s & \textbf{%s}~%s" % (before, after, arrow)

    body = []
    for label, getb, geta, up_good in [
        ("Questions solved (of 20)",
         lambda m: B["totals"][m]["q_solid"], lambda m: T[m]["q_solid"], True),
        ("Attempts passed (of 60)",
         lambda m: B["totals"][m]["passes"], lambda m: T[m]["passes"], True),
        ("Confident falsehoods",
         lambda m: B["totals"][m]["halluc"], lambda m: T[m]["halluc"], False),
        ("Correct but uncited",
         lambda m: B["totals"][m]["uncited"], lambda m: T[m]["uncited"], False),
        ("Total tokens (thousands)",
         lambda m: round(B["totals"][m]["tokens"] / 1000),
         lambda m: round(T[m]["tokens"] / 1000), False),
    ]:
        cells = " & ".join(pair(getb(m), geta(m), up_good) for m in MODES)
        body.append(r"%s & %s \\" % (label, cells))
    body.append(r"\midrule")
    for tid in ("T1", "T2", "T3", "T4"):
        cells = " & ".join(pair(tyB[tid][m]["attempts_pass"], tyA[tid][m]["attempts_pass"])
                           for m in MODES)
        body.append(r"%s attempts (of 15) & %s \\" % (tid, cells))

    w("tab_beforeafter.tex", r"""\begin{tabular}{@{}l cc cc cc@{}}
\toprule
& \multicolumn{2}{c}{\textbf{Text-to-SQL}} & \multicolumn{2}{c}{\textbf{Vector RAG}} & \multicolumn{2}{c}{\textbf{GraphRAG}} \\
\cmidrule(lr){2-3}\cmidrule(lr){4-5}\cmidrule(lr){6-7}
& pub. & rep. & pub. & rep. & pub. & rep. \\
\midrule
%s
\bottomrule
\end{tabular}""" % "\n".join(body))
