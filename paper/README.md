# Structural Retrieval versus Similarity Search

LaTeX source for the paper reporting the SEC EDGAR GraphRAG benchmark:
180 scored retrieval attempts over 167,050 filings, comparing text-to-SQL,
vector RAG and a two-stage GraphRAG design.

    make        # regenerate assets, build both layouts, verify the numbers

Two layouts share one body:

| target        | output                  | layout                        |
|---------------|-------------------------|-------------------------------|
| `make onecol` | `build/main.pdf`        | 11pt, single column, 21 pages |
| `make twocol` | `build/main-2col.pdf`   | 10pt, two columns, 16 pages   |

Citations are numeric (`[1]`), numbered in order of first citation
(`unsrtnat`), with runs compressed to `[4-7]`.

## Layout

| path              | what it is                                                        |
|-------------------|-------------------------------------------------------------------|
| `main.tex`        | one-column entry point                                            |
| `main-2col.tex`   | two-column entry point                                            |
| `body.tex`        | **shared** section order, float placement and captions            |
| `titleblock.tex`  | title and abstract, spanned across columns in the 2-col build     |
| `preamble.tex`    | packages, palette, glyphs, and the one/two-column switch          |
| `sec/*.tex`       | prose, one file per section                                       |
| `gen/*.tex`       | **generated** — every table and data-driven chart                 |
| `build_assets.py` | generates `gen/` from `facts.json`                                |
| `facts.json`      | measured results, derived from the stored 180 per-attempt records |
| `methods.json`    | configuration, fairness controls, harness defects                 |
| `refs.bib`        | bibliography                                                      |
| `verify_numbers.py` | asserts the paper's headline numbers against `facts.json`       |

## Why the tables are generated

Nothing in `gen/` is hand-edited. The first version of the results table was
typed by hand and immediately drifted from the data — a "max latency" row said
5.0 and 21.9 where the measurements say 5.2 and 21.8. Numbers in prose are
cross-checked by `make verify`.

Two exceptions: `gen/fig_arch.tex` and `gen/fig_containers.tex` are hand-authored
TikZ layout whose only numbers are labels, and are not regenerated.

## Provenance of the data

`facts.json` is derived from `graphrag-comparision/results/bench20.json`, the
stored answer and score for each of the 180 attempts. Because the answers are
retained, a ground-truth correction found after the first analysis was applied by
re-scoring offline, with no new inference — see §9 of the paper.

## How one body serves two layouts

`main.tex` and `main-2col.tex` differ only in class options and a
`\newif\iftwocol` flag set before `\input{preamble}`. The preamble resolves that
flag once to define `widefig` / `widetab`, which expand to `figure*` / `table*`
in two-column mode and to `figure` / `table` otherwise. Floats too wide for an
8cm column — the architecture and container diagrams, the results tables, the
paired charts, the question set — are declared with those environments in
`body.tex`, so neither layout needs its own copy of the body.

`make check` fails the build if any reference resolves to `??` or if the document
emits more than one reference list. Both conditions were real defects during
development: extracting `body.tex` initially left the bibliography inside it, so
every build emitted the references and the appendix twice while still exiting 0.
