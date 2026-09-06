# Structural Retrieval versus Similarity Search

LaTeX source for the paper reporting the SEC EDGAR GraphRAG benchmark:
180 scored retrieval attempts over 167,050 filings, comparing text-to-SQL,
vector RAG and a two-stage GraphRAG design.

    make        # regenerate tables/figures from data, then compile to build/main.pdf

## Layout

| path              | what it is                                                        |
|-------------------|-------------------------------------------------------------------|
| `main.tex`        | document structure, float placement, captions                     |
| `preamble.tex`    | packages, palette, outcome glyphs                                 |
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
