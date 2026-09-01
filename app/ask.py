#!/usr/bin/env python
"""Ask an arbitrary question through one or all three retrieval architectures.

  python app/ask.py "who audits Boeing?"                 # all three, side by side
  python app/ask.py --mode graphrag "..."                # just one
  python app/ask.py --repl                               # interactive
"""
from __future__ import annotations
import os, sys, argparse, pathlib, textwrap, time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import mode_sql, mode_vector, mode_graphrag        # noqa: E402
from llm import USAGE                              # noqa: E402

MODES = {"sql": mode_sql, "vector": mode_vector, "graphrag": mode_graphrag}


def one(question: str, mode: str, verbose: bool) -> None:
    print(f"\n{'='*94}\n{mode.upper()}\n{'='*94}")
    t0 = time.time()
    before = USAGE.snapshot()
    try:
        out = MODES[mode].run(question, verbose=verbose)
    except Exception as e:                          # noqa: BLE001
        print(f"  crashed: {type(e).__name__}: {e}")
        return
    after = USAGE.snapshot()
    print("\n" + textwrap.indent(textwrap.fill(out["answer"], 90), "  "))
    print(f"\n  [{time.time()-t0:.1f}s, {after['total_tokens']-before['total_tokens']:,} tokens]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*")
    ap.add_argument("--mode", choices=[*MODES, "all"], default="all")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--repl", action="store_true")
    a = ap.parse_args()

    modes = list(MODES) if a.mode == "all" else [a.mode]

    if a.repl:
        print("SEC EDGAR GraphRAG demo. Ctrl-D to exit. '/mode <sql|vector|graphrag|all>' to switch.")
        while True:
            try:
                q = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print(); break
            if not q:
                continue
            if q.startswith("/mode"):
                m = q.split(maxsplit=1)[-1].strip()
                modes = list(MODES) if m == "all" else ([m] if m in MODES else modes)
                print(f"  modes = {modes}")
                continue
            for m in modes:
                one(q, m, not a.quiet)
        print(f"session usage: {USAGE.snapshot()}")
        return

    q = " ".join(a.question).strip()
    if not q:
        ap.error("give a question, or use --repl")
    for m in modes:
        one(q, m, not a.quiet)
    print(f"\ntotal usage: {USAGE.snapshot()}")


if __name__ == "__main__":
    main()
