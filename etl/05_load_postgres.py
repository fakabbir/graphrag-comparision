#!/usr/bin/env python
"""Stage 5 - load the staged JSONL into Postgres via COPY."""
from __future__ import annotations
import sys, pathlib, json

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import PG_DSN, read_jsonl        # noqa: E402

import psycopg


def copy_rows(cur, table: str, cols: list[str], rows, *, page: int = 5000) -> int:
    n = 0
    with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row([r.get(c) for c in cols])
            n += 1
    return n


def main() -> None:
    with psycopg.connect(PG_DSN, autocommit=False) as conn:
        cur = conn.cursor()

        print("truncating…")
        cur.execute("""TRUNCATE section_chunk, filing_section, filing_auditor, subsidiary,
                                filing_document, reporting_owner, filing, company RESTART IDENTITY CASCADE;""")

        # ── companies: RSS spine first, then Form 345-only issuers ─────────
        rss = list(read_jsonl("companies.jsonl"))
        for c in rss:
            c["source"] = "rss"
        n = copy_rows(cur, "company",
                      ["cik", "name", "sic", "sic_description", "fiscal_year_end",
                       "assistant_director", "source"], rss)
        print(f"  company (rss)          : {n:,}")

        rss_ciks = {c["cik"] for c in rss}
        extra = [{"cik": i["cik"], "name": i["name"], "sic": None,
                  "sic_description": None, "fiscal_year_end": None,
                  "assistant_director": None, "source": "form345"}
                 for i in read_jsonl("form345_issuers.jsonl") if i["cik"] not in rss_ciks]
        n = copy_rows(cur, "company",
                      ["cik", "name", "sic", "sic_description", "fiscal_year_end",
                       "assistant_director", "source"], extra)
        print(f"  company (form345 only) : {n:,}")

        # insiders are people/entities that may not be issuers - add as companies? no.
        # they live only in reporting_owner + Neo4j :Person nodes.

        n = copy_rows(cur, "filing",
                      ["accession_number", "company_cik", "form_type", "filing_date",
                       "period_of_report", "acceptance_dt", "file_number",
                       "index_url", "primary_doc_url"], read_jsonl("filings.jsonl"))
        print(f"  filing                 : {n:,}")

        n = copy_rows(cur, "filing_document",
                      ["accession_number", "sequence", "doc_type", "filename",
                       "description", "size_bytes", "inline_xbrl", "url"],
                      read_jsonl("documents.jsonl"))
        print(f"  filing_document        : {n:,}")

        n = copy_rows(cur, "filing_section",
                      ["accession_number", "item_code", "item_title", "section_text",
                       "char_len", "company_cik", "filing_date"],
                      read_jsonl("sections.jsonl"))
        print(f"  filing_section         : {n:,}")

        n = copy_rows(cur, "subsidiary",
                      ["accession_number", "parent_cik", "subsidiary_name",
                       "name_normalized", "jurisdiction"],
                      read_jsonl("subsidiaries.jsonl"))
        print(f"  subsidiary             : {n:,}")

        n = copy_rows(cur, "filing_auditor",
                      ["accession_number", "company_cik", "auditor_name",
                       "auditor_location", "pcaob_firm_id", "fiscal_year"],
                      read_jsonl("auditors.jsonl"))
        print(f"  filing_auditor         : {n:,}")

        n = copy_rows(cur, "reporting_owner",
                      ["accession_number", "owner_cik", "owner_name", "issuer_cik",
                       "issuer_name", "relationship", "is_officer", "is_director",
                       "is_ten_pct_owner", "is_other", "officer_title",
                       "filing_date", "period_of_report"],
                      read_jsonl("owner_edges.jsonl"))
        print(f"  reporting_owner        : {n:,}")

        conn.commit()
        print("\nanalyzing…")
        cur.execute("ANALYZE;")
        conn.commit()

        print("\n--- row counts ---")
        for t in ("company", "filing", "filing_document", "filing_section",
                  "subsidiary", "filing_auditor", "reporting_owner"):
            cur.execute(f"SELECT count(*) FROM {t}")
            print(f"  {t:18s} {cur.fetchone()[0]:>9,}")
        cur.execute("SELECT pg_size_pretty(pg_database_size('secedgar'))")
        print(f"\n  database size: {cur.fetchone()[0]}")


if __name__ == "__main__":
    main()
