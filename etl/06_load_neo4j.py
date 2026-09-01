#!/usr/bin/env python
"""Stage 6 - project the relational store into the Neo4j ownership graph.

Nodes : Company, Person, Subsidiary, AuditFirm, Filing
Edges : FILED, OFFICER_OF, DIRECTOR_OF, OWNS_SHARES, SUBSIDIARY_OF, AUDITED_BY
        - every edge carries filingId, so any traversal result can be traced back
          to a document and the exact text pulled from Postgres.

Filing nodes carry hasRiskFactors / riskFactorChars so a Cypher traversal can find
which filings actually have Item 1A text before handing filing_ids to SQL.
"""
from __future__ import annotations
import sys, pathlib, itertools

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import PG_DSN, NEO4J_URI, NEO4J_USER, NEO4J_PASS, normalize_name  # noqa: E402

import psycopg
from neo4j import GraphDatabase

BATCH = 5000


def chunks(it, n):
    it = iter(it)
    while True:
        blk = list(itertools.islice(it, n))
        if not blk:
            return
        yield blk


DDL = [
    "CREATE CONSTRAINT company_cik IF NOT EXISTS FOR (c:Company) REQUIRE c.cik IS UNIQUE",
    "CREATE CONSTRAINT person_cik IF NOT EXISTS FOR (p:Person) REQUIRE p.cik IS UNIQUE",
    "CREATE CONSTRAINT filing_acc IF NOT EXISTS FOR (f:Filing) REQUIRE f.accessionNumber IS UNIQUE",
    "CREATE CONSTRAINT sub_name IF NOT EXISTS FOR (s:Subsidiary) REQUIRE s.nameNormalized IS UNIQUE",
    "CREATE CONSTRAINT audit_key IF NOT EXISTS FOR (a:AuditFirm) REQUIRE a.firmKey IS UNIQUE",
    "CREATE INDEX company_name IF NOT EXISTS FOR (c:Company) ON (c.name)",
    "CREATE INDEX company_sic IF NOT EXISTS FOR (c:Company) ON (c.sic)",
    "CREATE INDEX person_name IF NOT EXISTS FOR (p:Person) ON (p.name)",
    "CREATE INDEX sub_display IF NOT EXISTS FOR (s:Subsidiary) ON (s.name)",
    "CREATE INDEX filing_form IF NOT EXISTS FOR (f:Filing) ON (f.formType)",
]


def main() -> None:
    pg = psycopg.connect(PG_DSN)
    drv = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

    with drv.session() as s:
        print("wiping graph…")
        while True:
            r = s.run("MATCH (n) WITH n LIMIT 20000 DETACH DELETE n RETURN count(n) AS c").single()
            if not r or r["c"] == 0:
                break
        for q in DDL:
            s.run(q)
        print(f"  {len(DDL)} constraints/indexes ensured")

    def run_batches(label, cypher, rows, key="rows"):
        total = 0
        with drv.session() as s:
            for blk in chunks(rows, BATCH):
                s.run(cypher, **{key: blk})
                total += len(blk)
        print(f"  {label:24s} {total:>9,}")
        return total

    cur = pg.cursor()

    # ── Company ────────────────────────────────────────────────────────────
    cur.execute("SELECT cik, name, sic, sic_description, source FROM company")
    run_batches("Company", """
        UNWIND $rows AS r
        MERGE (c:Company {cik: r[0]})
        SET c.name = r[1], c.sic = r[2], c.sicDescription = r[3], c.source = r[4]
    """, cur)

    # ── Filing (+ risk-factor availability) ────────────────────────────────
    cur.execute("""
        SELECT f.accession_number, f.company_cik, f.form_type,
               f.filing_date::text, f.period_of_report::text,
               (s.accession_number IS NOT NULL) AS has_rf,
               COALESCE(s.char_len, 0), f.index_url
        FROM filing f
        LEFT JOIN filing_section s
               ON s.accession_number = f.accession_number AND s.item_code = '1A'
    """)
    run_batches("Filing + :FILED", """
        UNWIND $rows AS r
        MATCH (c:Company {cik: r[1]})
        MERGE (f:Filing {accessionNumber: r[0]})
        SET f.formType = r[2], f.filingDate = date(r[3]),
            f.periodOfReport = CASE WHEN r[4] IS NULL THEN null ELSE date(r[4]) END,
            f.hasRiskFactors = r[5], f.riskFactorChars = r[6], f.indexUrl = r[7]
        MERGE (c)-[:FILED]->(f)
    """, cur)

    # ── Person ─────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT DISTINCT owner_cik, min(owner_name) AS nm
        FROM reporting_owner GROUP BY owner_cik
    """)
    run_batches("Person", """
        UNWIND $rows AS r
        MERGE (p:Person {cik: r[0]})
        SET p.name = r[1]
    """, cur)

    # ── role / ownership edges ─────────────────────────────────────────────
    cur.execute("""
        SELECT accession_number, owner_cik, issuer_cik, officer_title,
               filing_date::text, period_of_report::text
        FROM reporting_owner WHERE is_officer
    """)
    run_batches("OFFICER_OF", """
        UNWIND $rows AS r
        MATCH (p:Person {cik: r[1]}), (c:Company {cik: r[2]})
        MERGE (p)-[e:OFFICER_OF {filingId: r[0]}]->(c)
        SET e.title = r[3], e.filingDate = date(r[4]),
            e.asOfDate = CASE WHEN r[5] IS NULL THEN null ELSE date(r[5]) END
    """, cur)

    cur.execute("""
        SELECT accession_number, owner_cik, issuer_cik, filing_date::text, period_of_report::text
        FROM reporting_owner WHERE is_director
    """)
    run_batches("DIRECTOR_OF", """
        UNWIND $rows AS r
        MATCH (p:Person {cik: r[1]}), (c:Company {cik: r[2]})
        MERGE (p)-[e:DIRECTOR_OF {filingId: r[0]}]->(c)
        SET e.filingDate = date(r[3]),
            e.asOfDate = CASE WHEN r[4] IS NULL THEN null ELSE date(r[4]) END
    """, cur)

    cur.execute("""
        SELECT accession_number, owner_cik, issuer_cik, filing_date::text
        FROM reporting_owner WHERE is_ten_pct_owner
    """)
    run_batches("OWNS_SHARES", """
        UNWIND $rows AS r
        MATCH (p:Person {cik: r[1]}), (c:Company {cik: r[2]})
        MERGE (p)-[e:OWNS_SHARES {filingId: r[0]}]->(c)
        SET e.basis = 'TenPercentOwner', e.filingDate = date(r[3])
    """, cur)

    # ── Subsidiary ─────────────────────────────────────────────────────────
    cur.execute("""
        SELECT s.accession_number, s.parent_cik, s.subsidiary_name,
               s.name_normalized, s.jurisdiction,
               EXTRACT(YEAR FROM f.period_of_report)::int
        FROM subsidiary s JOIN filing f USING (accession_number)
    """)
    run_batches("Subsidiary + :SUBSIDIARY_OF", """
        UNWIND $rows AS r
        MATCH (parent:Company {cik: r[1]})
        MERGE (s:Subsidiary {nameNormalized: r[3]})
        SET s.name = r[2], s.jurisdiction = r[4]
        MERGE (s)-[e:SUBSIDIARY_OF {filingId: r[0]}]->(parent)
        SET e.jurisdiction = r[4], e.fiscalYear = r[5]
    """, cur)

    # ── AuditFirm ──────────────────────────────────────────────────────────
    cur.execute("""
        SELECT accession_number, company_cik, auditor_name, auditor_location,
               pcaob_firm_id, fiscal_year,
               COALESCE(pcaob_firm_id::text, lower(auditor_name)) AS firm_key
        FROM filing_auditor
    """)
    run_batches("AuditFirm + :AUDITED_BY", """
        UNWIND $rows AS r
        MATCH (c:Company {cik: r[1]})
        MERGE (a:AuditFirm {firmKey: r[6]})
        SET a.name = r[2], a.pcaobFirmId = r[4]
        MERGE (c)-[e:AUDITED_BY {filingId: r[0]}]->(a)
        SET e.fiscalYear = r[5], e.auditorLocation = r[3]
    """, cur)

    # ── entity resolution: subsidiary name -> known Company ────────────────
    cur.execute("SELECT cik, name FROM company")
    comp_by_norm: dict[str, list[int]] = {}
    for cik, name in cur:
        comp_by_norm.setdefault(normalize_name(name), []).append(cik)
    # only unambiguous matches
    resolvable = {k: v[0] for k, v in comp_by_norm.items() if len(v) == 1}

    cur.execute("SELECT DISTINCT name_normalized FROM subsidiary")
    links = [[n, resolvable[n]] for (n,) in cur if n in resolvable]
    run_batches("RESOLVES_TO (sub->Company)", """
        UNWIND $rows AS r
        MATCH (s:Subsidiary {nameNormalized: r[0]}), (c:Company {cik: r[1]})
        WHERE NOT (s)-[:SUBSIDIARY_OF]->(c)
        MERGE (s)-[:RESOLVES_TO]->(c)
    """, iter(links))

    # ── summary ────────────────────────────────────────────────────────────
    with drv.session() as s:
        print("\n--- graph contents ---")
        for lab in ("Company", "Person", "Subsidiary", "AuditFirm", "Filing"):
            n = s.run(f"MATCH (n:{lab}) RETURN count(n) AS c").single()["c"]
            print(f"  (:{lab:<11}) {n:>9,}")
        print()
        for rel in ("FILED", "OFFICER_OF", "DIRECTOR_OF", "OWNS_SHARES",
                    "SUBSIDIARY_OF", "AUDITED_BY", "RESOLVES_TO"):
            n = s.run(f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c").single()["c"]
            print(f"  [:{rel:<15}] {n:>9,}")
        tot = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        print(f"\n  total edges: {tot:,}")

    pg.close(); drv.close()


if __name__ == "__main__":
    main()
