#!/usr/bin/env python
"""Stage 2 - fetch the documents the RSS manifest points at.

We deliberately fetch a narrow slice rather than mirroring the archive:
  * 10-K / 10-K/A primary documents  -> Item 1A risk-factor text + dei:Auditor* tags
  * EX-21* exhibits                  -> subsidiary lists
Resumable: anything already on disk is skipped.
"""
from __future__ import annotations
import sys, pathlib, concurrent.futures as cf, collections

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import RAW, read_jsonl, sec_download, write_jsonl   # noqa: E402

TENK_FORMS = {"10-K", "10-K/A"}


def target_list() -> list[dict]:
    filings = {f["accession_number"]: f for f in read_jsonl("filings.jsonl")}
    tenk = {a for a, f in filings.items() if f["form_type"] in TENK_FORMS}

    targets, seen = [], set()
    for d in read_jsonl("documents.jsonl"):
        acc, dtype = d["accession_number"], (d["doc_type"] or "").upper()
        is_ex21 = dtype.startswith("EX-21")
        is_primary_tenk = acc in tenk and d["sequence"] == 1
        if not (is_ex21 or is_primary_tenk):
            continue
        if not d["url"] or not d["filename"]:
            continue
        # skip binary/graphic assets and oversized files
        if pathlib.Path(d["filename"]).suffix.lower() in {".jpg", ".png", ".gif", ".zip", ".pdf"}:
            continue
        key = (acc, d["filename"])
        if key in seen:
            continue
        seen.add(key)
        targets.append({
            "accession_number": acc,
            "filename": d["filename"],
            "doc_type": d["doc_type"],
            "url": d["url"],
            "kind": "EX21" if is_ex21 else "TENK",
            "form_type": filings[acc]["form_type"] if acc in filings else None,
            "company_cik": filings[acc]["company_cik"] if acc in filings else None,
            "size_bytes": d.get("size_bytes"),
        })
    return targets


def fetch_one(t: dict) -> tuple[str, str, int]:
    out = RAW / t["accession_number"] / t["filename"]
    if out.exists() and out.stat().st_size > 0:
        return ("cached", t["url"], out.stat().st_size)
    try:
        n = sec_download(t["url"], out)
    except Exception as e:                        # noqa: BLE001
        return ("error", f"{t['url']} :: {e}", 0)
    return ("ok", t["url"], n)


def main() -> None:
    targets = target_list()
    kinds = collections.Counter(t["kind"] for t in targets)
    total_declared = sum(t["size_bytes"] or 0 for t in targets)
    print(f"targets: {len(targets)}  ({dict(kinds)})   declared size ~{total_declared/1e6:.0f} MB")

    write_jsonl("fetch_manifest.jsonl", targets)

    stats = collections.Counter()
    got = 0
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for status, info, nbytes in ex.map(fetch_one, targets):
            stats[status] += 1
            got += nbytes
            if status == "error":
                print(f"  ! {info}")
            done = sum(stats.values())
            if done % 25 == 0:
                print(f"  {done}/{len(targets)}  ok={stats['ok']} cached={stats['cached']} err={stats['error']}")
    print(f"\ndone: {dict(stats)}   {got/1e6:.1f} MB fetched")


if __name__ == "__main__":
    main()
