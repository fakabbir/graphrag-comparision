#!/usr/bin/env python
"""Stage 0 - download the monthly XBRL RSS feeds.

    python etl/00_fetch_feeds.py --months 12          # the 12 most recent
    python etl/00_fetch_feeds.py --from 2025-09 --to 2026-08
    python etl/00_fetch_feeds.py --list               # what the archive offers

Source: https://www.sec.gov/Archives/edgar/monthly/
Each feed is the filing *spine* for one month: submission metadata plus a complete
per-filing document manifest. It carries no ownership edges and no narrative text -
those come from the Form 345 dataset (stage 3) and the fetched documents (stage 2).
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import DATA, sec_download, sec_get  # noqa: E402

INDEX_URL = "https://www.sec.gov/Archives/edgar/monthly/"
FEED_URL = INDEX_URL + "xbrlrss-{year}-{month:02d}.xml"
FEED_RE = re.compile(r"xbrlrss-(\d{4})-(\d{2})\.xml")


def available() -> list[tuple[int, int]]:
    html = sec_get(INDEX_URL).decode("utf-8", "ignore")
    return sorted({(int(y), int(m)) for y, m in FEED_RE.findall(html)})


def parse_ym(s: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM, got {s!r}")
    return int(m.group(1)), int(m.group(2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, help="how many of the most recent feeds")
    ap.add_argument("--from", dest="frm", type=parse_ym, help="YYYY-MM inclusive")
    ap.add_argument("--to", dest="to", type=parse_ym, help="YYYY-MM inclusive")
    ap.add_argument("--list", action="store_true", help="list what the archive offers")
    args = ap.parse_args()

    avail = available()
    if args.list:
        print(f"{len(avail)} monthly feeds: {avail[0][0]}-{avail[0][1]:02d} "
              f"-> {avail[-1][0]}-{avail[-1][1]:02d}")
        for y, m in avail[-24:]:
            print(f"  {y}-{m:02d}")
        return

    if args.frm or args.to:
        lo = args.frm or avail[0]
        hi = args.to or avail[-1]
        want = [ym for ym in avail if lo <= ym <= hi]
    elif args.months:
        want = avail[-args.months:]
    else:
        ap.error("give --months N, or --from/--to, or --list")

    print(f"downloading {len(want)} feeds "
          f"({want[0][0]}-{want[0][1]:02d} -> {want[-1][0]}-{want[-1][1]:02d})")

    total = 0
    for year, month in want:
        dest = DATA / f"xbrlrss-{year}-{month:02d}.xml"
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  cached  {dest.name}  {dest.stat().st_size/1e6:.1f} MB")
            total += dest.stat().st_size
            continue
        n = sec_download(FEED_URL.format(year=year, month=month), dest)
        total += n
        print(f"  got     {dest.name}  {n/1e6:.1f} MB")

    print(f"\n{len(want)} feeds, {total/1e6:.0f} MB total in {DATA}")


if __name__ == "__main__":
    main()
