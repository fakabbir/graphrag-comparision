#!/usr/bin/env python
"""Stage 1 - parse every downloaded monthly XBRL RSS feed into
companies / filings / documents.

Reads all data/xbrlrss-*.xml written by stage 0, so the same code handles one month
or sixty. Filings are deduped on accession number across feeds (an accession can
appear in two months when a filing is amended near a month boundary).

Each feed is the *filing spine*: submission metadata plus a complete per-filing
document manifest, which is how we locate EX-21 exhibits without guessing filenames.
It contains no ownership edges and no narrative text - those come from stage 2 and
from the Form 345 dataset in stage 3.
"""
from __future__ import annotations
import sys, re, datetime as dt, collections
import xml.etree.ElementTree as ET

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from common import DATA, write_jsonl, sec_get, STAGING       # noqa: E402

NS = {"edgar": "https://www.sec.gov/Archives/edgar"}
E  = "{https://www.sec.gov/Archives/edgar}"
FEED_GLOB = "xbrlrss-*.xml"

SIC_LIST_URL = "https://www.sec.gov/search-filings/standard-industrial-classification-sic-code-list"


def load_sic_map() -> dict[str, str]:
    """Scrape the SEC's official SIC code -> description table (cached)."""
    cache = STAGING / "sic_map.json"
    if cache.exists():
        import json
        return json.loads(cache.read_text())
    import json, html as htmllib
    try:
        raw = sec_get(SIC_LIST_URL).decode("utf-8", "ignore")
    except Exception as e:                                    # noqa: BLE001
        print(f"  ! SIC list unavailable ({e}); descriptions will be NULL")
        return {}
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", raw, re.S | re.I)
    out: dict[str, str] = {}
    for row in rows:
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
        cells = [htmllib.unescape(c) for c in cells]
        if len(cells) >= 3 and re.fullmatch(r"\d{2,4}", cells[0]):
            out[cells[0].zfill(4)] = cells[-1]
    cache.write_text(json.dumps(out, indent=1))
    print(f"  SIC map: {len(out)} codes")
    return out


def parse_date(s: str | None):
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def parse_dt(s: str | None):
    if not s or not s.strip().isdigit():
        return None
    try:
        return dt.datetime.strptime(s.strip(), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def main() -> None:
    feeds = sorted(DATA.glob(FEED_GLOB))
    if not feeds:
        raise SystemExit(
            f"no feeds found in {DATA}. Run: python etl/00_fetch_feeds.py --months 12")
    total_mb = sum(f.stat().st_size for f in feeds) / 1e6
    print(f"Parsing {len(feeds)} feed(s), {total_mb:.1f} MB "
          f"({feeds[0].stem.replace('xbrlrss-','')} -> "
          f"{feeds[-1].stem.replace('xbrlrss-','')})")
    sic_map = load_sic_map()

    companies: dict[int, dict] = {}
    filings: dict[str, dict] = {}
    documents: list[dict] = []
    form_counts = collections.Counter()
    skipped = 0
    n_items = 0

    for feed in feeds:
        root = ET.parse(feed).getroot()
        feed_items = root.findall("./channel/item")
        n_items += len(feed_items)
        before = len(filings)
        _ingest(feed_items, companies, filings, documents, form_counts, sic_map)
        print(f"    {feed.name}: {len(feed_items):>6,} items  "
              f"+{len(filings) - before:>6,} new filings")
        root.clear()
        del root, feed_items

    _finish(n_items, companies, filings, documents, form_counts, feeds)


def _ingest(items, companies, filings, documents, form_counts, sic_map) -> None:
    skipped = 0
    for it in items:
        f = it.find(".//edgar:xbrlFiling", NS)
        if f is None:
            skipped += 1
            continue
        cik_raw = (f.findtext("edgar:cikNumber", "", NS) or "").strip()
        acc     = (f.findtext("edgar:accessionNumber", "", NS) or "").strip()
        if not cik_raw.isdigit() or len(acc) != 20:
            skipped += 1
            continue
        cik  = int(cik_raw)
        form = (f.findtext("edgar:formType", "", NS) or "").strip()
        sic  = (f.findtext("edgar:assignedSic", "", NS) or "").strip() or None
        sic  = sic.zfill(4) if sic else None
        form_counts[form] += 1

        companies.setdefault(cik, {
            "cik": cik,
            "name": (f.findtext("edgar:companyName", "", NS) or "").strip(),
            "sic": sic,
            "sic_description": sic_map.get(sic) if sic else None,
            "fiscal_year_end": (f.findtext("edgar:fiscalYearEnd", "", NS) or "").strip() or None,
            "assistant_director": (f.findtext("edgar:assistantDirector", "", NS) or "").strip() or None,
        })

        # One accession can appear once per co-filer; keep the first, dedupe by accession.
        if acc not in filings:
            link = (it.findtext("link") or "").strip()
            docs = f.findall(".//edgar:xbrlFile", NS)
            primary = None
            for d in docs:
                if (d.get(f"{E}sequence") or "") == "1":
                    primary = d.get(f"{E}url")
                    break
            filings[acc] = {
                "accession_number": acc,
                "company_cik": cik,
                "form_type": form,
                "filing_date": parse_date(f.findtext("edgar:filingDate", "", NS)),
                "period_of_report": parse_date(f.findtext("edgar:period", "", NS)),
                "acceptance_dt": parse_dt(f.findtext("edgar:acceptanceDatetime", "", NS)),
                "file_number": (f.findtext("edgar:fileNumber", "", NS) or "").strip() or None,
                "index_url": link or None,
                "primary_doc_url": primary,
            }
            for d in docs:
                seq = d.get(f"{E}sequence")
                documents.append({
                    "accession_number": acc,
                    "sequence": int(seq) if seq and seq.isdigit() else 0,
                    "doc_type": d.get(f"{E}type"),
                    "filename": d.get(f"{E}file"),
                    "description": d.get(f"{E}description") or None,
                    "size_bytes": int(d.get(f"{E}size") or 0) or None,
                    "inline_xbrl": (d.get(f"{E}inlineXBRL") or "").lower() == "true",
                    "url": d.get(f"{E}url"),
                })


def _finish(n_items, companies, filings, documents, form_counts, feeds) -> None:
    # de-dup documents on (accession, sequence) - the PK
    seen, deduped = set(), []
    for d in documents:
        k = (d["accession_number"], d["sequence"])
        if k in seen:
            continue
        seen.add(k)
        deduped.append(d)

    print(f"\n  feed items          : {n_items:,}")
    print(f"  feeds parsed        : {len(feeds)}")
    print(f"  unique companies    : {len(companies):,}")
    print(f"  unique filings      : {len(filings):,}")
    print(f"  documents (manifest): {len(deduped):,}  (dropped {len(documents)-len(deduped)} dup seq)")
    print(f"  distinct form types : {len(form_counts)}")
    for k, v in form_counts.most_common(8):
        print(f"      {k:12s} {v:>5,}")

    ex21 = [d for d in deduped if (d["doc_type"] or "").upper().startswith("EX-21")]
    tenk = [f for f in filings.values() if f["form_type"] in ("10-K", "10-K/A")]
    print(f"\n  10-K / 10-K/A       : {len(tenk)}")
    print(f"  EX-21 documents     : {len(ex21)}")

    write_jsonl("companies.jsonl", companies.values())
    write_jsonl("filings.jsonl",   filings.values())
    write_jsonl("documents.jsonl", deduped)


if __name__ == "__main__":
    main()
