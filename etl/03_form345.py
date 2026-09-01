#!/usr/bin/env python
"""Stage 3 - project the Form 3/4/5 dataset into ownership/role edges.

The RSS feed has no ownership data at all, so the :OFFICER_OF / :DIRECTOR_OF /
:OWNS_SHARES edges come from the SEC's Insider Transactions Data Sets
(2022q1_form345.zip), which is already normalized and CIK-keyed on both ends.

RPTOWNER_RELATIONSHIP is comma-joined and multi-valued: "Director,Officer" means
both edges. Officer titles come from RPTOWNER_TITLE.
"""
from __future__ import annotations
import sys, csv, pathlib, collections, datetime as dt

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import DATA, write_jsonl, read_jsonl, normalize_name   # noqa: E402

F345 = DATA / "form345"
csv.field_size_limit(10_000_000)


def parse_sec_date(s: str | None):
    """SEC TSVs use DD-MON-YYYY, e.g. 31-DEC-2021."""
    if not s or not s.strip():
        return None
    try:
        return dt.datetime.strptime(s.strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def tsv(name: str):
    with (F345 / name).open(newline="", encoding="utf-8", errors="replace") as fh:
        yield from csv.DictReader(fh, delimiter="\t")


def main() -> None:
    rss_ciks = {int(c["cik"]) for c in read_jsonl("companies.jsonl")}
    print(f"RSS company universe: {len(rss_ciks):,}")

    subs = {}
    for r in tsv("SUBMISSION.tsv"):
        icik = (r["ISSUERCIK"] or "").strip()
        if not icik.isdigit():
            continue
        subs[r["ACCESSION_NUMBER"]] = {
            "issuer_cik": int(icik),
            "issuer_name": (r["ISSUERNAME"] or "").strip(),
            "doc_type": (r["DOCUMENT_TYPE"] or "").strip(),
            "filing_date": parse_sec_date(r["FILING_DATE"]),
            "period_of_report": parse_sec_date(r["PERIOD_OF_REPORT"]),
            "ticker": (r["ISSUERTRADINGSYMBOL"] or "").strip() or None,
        }
    print(f"Form 345 submissions: {len(subs):,}")

    owners: dict[str, dict] = {}
    edges: list[dict] = []
    issuers: dict[int, str] = {}
    rel_counter = collections.Counter()
    dropped = 0

    for r in tsv("REPORTINGOWNER.tsv"):
        s = subs.get(r["ACCESSION_NUMBER"])
        ocik = (r["RPTOWNERCIK"] or "").strip()
        if not s or not ocik.isdigit():
            dropped += 1
            continue
        ocik_i = int(ocik)
        rel = (r["RPTOWNER_RELATIONSHIP"] or "").strip()
        rel_counter[rel] += 1
        parts = {p.strip() for p in rel.split(",") if p.strip()}
        issuers[s["issuer_cik"]] = s["issuer_name"]

        owners.setdefault(ocik_i, {
            "owner_cik": ocik_i,
            "owner_name": (r["RPTOWNERNAME"] or "").strip(),
            "name_normalized": normalize_name(r["RPTOWNERNAME"] or ""),
            "city": (r["RPTOWNER_CITY"] or "").strip() or None,
            "state": (r["RPTOWNER_STATE"] or "").strip() or None,
        })
        edges.append({
            "accession_number": r["ACCESSION_NUMBER"],
            "owner_cik": ocik_i,
            "owner_name": (r["RPTOWNERNAME"] or "").strip(),
            "issuer_cik": s["issuer_cik"],
            "issuer_name": s["issuer_name"],
            "relationship": rel or None,
            "is_officer": "Officer" in parts,
            "is_director": "Director" in parts,
            "is_ten_pct_owner": "TenPercentOwner" in parts,
            "is_other": "Other" in parts,
            "officer_title": (r["RPTOWNER_TITLE"] or "").strip() or None,
            "filing_date": s["filing_date"],
            "period_of_report": s["period_of_report"],
            "doc_type": s["doc_type"],
        })

    # de-dup on the table PK (accession, owner_cik)
    seen, uniq = set(), []
    for e in edges:
        k = (e["accession_number"], e["owner_cik"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)

    in_rss = sum(1 for c in issuers if c in rss_ciks)
    print(f"\n  reporting-owner edges : {len(uniq):,} (dropped {dropped}, deduped {len(edges)-len(uniq)})")
    print(f"  distinct insiders     : {len(owners):,}")
    print(f"  distinct issuers      : {len(issuers):,}  ({in_rss:,} also in the RSS slice)")
    print(f"  officer edges         : {sum(1 for e in uniq if e['is_officer']):,}")
    print(f"  director edges        : {sum(1 for e in uniq if e['is_director']):,}")
    print(f"  10%-owner edges       : {sum(1 for e in uniq if e['is_ten_pct_owner']):,}")

    per_owner = collections.defaultdict(set)
    for e in uniq:
        per_owner[e["owner_cik"]].add(e["issuer_cik"])
    multi = {k: v for k, v in per_owner.items() if len(v) > 1}
    print(f"  insiders at >1 issuer : {len(multi):,}   <- the 'previous company' hop")

    write_jsonl("insiders.jsonl", owners.values())
    write_jsonl("owner_edges.jsonl", uniq)
    write_jsonl("form345_issuers.jsonl",
                [{"cik": c, "name": n, "in_rss": c in rss_ciks} for c, n in issuers.items()])


if __name__ == "__main__":
    main()
