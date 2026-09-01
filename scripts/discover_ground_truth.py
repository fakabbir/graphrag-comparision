#!/usr/bin/env python
"""Find benchmark questions that the CURRENTLY LOADED data can actually answer.

The question set cannot be hardcoded: it names specific companies, people and
accession numbers, and those change completely when the loaded window changes.
Re-run this after every load, then paste the emitted block into app/questions.py.

    python scripts/discover_ground_truth.py            # against $PG_DSN / $NEO4J_URI

Each candidate is verified, not assumed:
  * the executive's surname must appear in ZERO narrative sections, otherwise the
    "vector RAG cannot reach it" claim is false for that subject
  * same-surname collisions are counted, because the graph's entity-resolution
    win depends on them existing
  * every accession offered as ground truth is confirmed to have Item 1A text
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import textwrap

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from stores import cypher_rows, sql_rows  # noqa: E402


def q(sql: str, *params):
    cols, rows = sql_rows(sql if not params else sql, limit=500)
    return cols, rows


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> None:
    section("0. corpus shape")
    _c, rows = sql_rows("""
        SELECT (SELECT count(*) FROM filing),
               (SELECT count(*) FROM company),
               (SELECT count(*) FROM filing_section),
               (SELECT count(*) FROM filing_section WHERE item_code='1A'),
               (SELECT count(*) FROM section_chunk),
               (SELECT count(*) FROM subsidiary),
               (SELECT count(*) FROM reporting_owner),
               (SELECT count(*) FROM filing_auditor),
               (SELECT min(filing_date) FROM filing),
               (SELECT max(filing_date) FROM filing)
    """)
    f, co, sec, rf, ch, sub, own, aud, lo, hi = rows[0]
    print(f"  filings {f:,} | companies {co:,} | sections {sec:,} (Item 1A: {rf:,})")
    print(f"  chunks {ch:,} | subsidiaries {sub:,} | ownership {own:,} | auditors {aud:,}")
    print(f"  window {lo} -> {hi}")

    # ── Q1: a single company whose Item 1A is rich and specific ─────────────
    section("1. single-document semantic  (vector RAG should win)")
    _c, rows = sql_rows("""
        SELECT c.name, c.cik, fs.accession_number, fs.char_len,
               (fs.section_text ILIKE '%supply chain%') AS supply,
               (fs.section_text ILIKE '%cyber%')        AS cyber
        FROM filing_section fs
        JOIN company c ON c.cik = fs.company_cik
        WHERE fs.item_code = '1A' AND fs.char_len BETWEEN 40000 AND 160000
          AND fs.section_text ILIKE '%supply chain%'
        ORDER BY fs.char_len DESC
        LIMIT 12
    """)
    for r in rows[:12]:
        print(f"  {r[0][:38]:38s} cik={r[1]:<9} {r[2]}  {r[3]:>7,} chars  supply={r[4]}")

    # ── Q2: auditor concentration ───────────────────────────────────────────
    section("2. structured aggregation  (text-to-SQL should win)")
    _c, rows = sql_rows("""
        SELECT auditor_name, count(DISTINCT company_cik) AS n
        FROM filing_auditor GROUP BY 1 ORDER BY n DESC, 1 LIMIT 8
    """)
    for r in rows:
        print(f"  {r[0][:46]:46s} {r[1]:>4} companies")
    top_auditor = rows[0] if rows else None

    # ── Q3: the multi-hop killer query ──────────────────────────────────────
    section("3. multi-hop relational + text  (THE KILLER QUERY)")
    cand = cypher_rows("""
        MATCH (p:Person)-[:OFFICER_OF|DIRECTOR_OF]->(b:Company)
        MATCH (sub:Subsidiary)-[:SUBSIDIARY_OF]->(b)
        MATCH (b)-[:FILED]->(f:Filing) WHERE f.hasRiskFactors
        WITH p, b, count(DISTINCT sub) AS subs, max(f.riskFactorChars) AS rf,
             collect(DISTINCT f.accessionNumber)[0] AS acc
        WITH p, collect({name:b.name, cik:b.cik, subs:subs, rf:rf, acc:acc}) AS cos
        WHERE size(cos) >= 2
        RETURN p.name AS person, p.cik AS pcik, cos
        ORDER BY reduce(s=0, x IN cos | s + x.subs) DESC
        LIMIT 12
    """)
    print(f"  {len(cand)} people are insiders at 2+ companies that BOTH have"
          f" subsidiaries and Item 1A text\n")
    for r in cand[:12]:
        print(f"  {r['person'][:30]:30s} cik={r['pcik']}")
        for x in r["cos"][:4]:
            print(f"      {x['name'][:36]:36s} cik={x['cik']:<9} subs={x['subs']:<5}"
                  f" rf={x['rf']:<7} {x['acc']}")

    # verify the disjointness claim for each candidate surname
    print("\n  --- verifying the 'not in the prose' claim ---")
    for r in cand[:12]:
        surname = r["person"].split()[0].lower()
        if len(surname) < 4:
            continue
        _c, hit = sql_rows(
            "SELECT count(*) FROM filing_section WHERE section_text ILIKE '%"
            + surname.replace("'", "") + "%'")
        _c, same = sql_rows(
            "SELECT count(DISTINCT owner_cik) FROM reporting_owner WHERE owner_name ILIKE '%"
            + surname.replace("'", "") + "%'")
        flag = "OK  " if hit[0][0] == 0 else "BAD "
        print(f"  {flag}{r['person'][:28]:28s} surname '{surname}': "
              f"{hit[0][0]} sections mention it, {same[0][0]} distinct people share it")

    # ── Q4: subsidiaries that are themselves filers ─────────────────────────
    section("4. cross-document entity resolution  (needs the graph)")
    res = cypher_rows("""
        MATCH (s:Subsidiary)-[:RESOLVES_TO]->(c:Company)
        OPTIONAL MATCH (s)-[:SUBSIDIARY_OF]->(parent:Company)
        RETURN s.name AS sub, c.cik AS cik, collect(DISTINCT parent.name) AS parents
        ORDER BY sub LIMIT 25
    """)
    print(f"  {len(res)} subsidiaries resolve to a company that is itself a filer")
    for r in res[:25]:
        print(f"    {r['sub'][:44]:44s} cik={r['cik']:<9} parent={r['parents'][:2]}")

    section("summary")
    print(textwrap.dedent(f"""
        Pick from the candidates above and paste into app/questions.py.

        Q2 answer is deterministic from this data:
            top auditor = {top_auditor[0] if top_auditor else 'n/a'}
            companies   = {top_auditor[1] if top_auditor else 'n/a'}

        Q3 requires a person whose surname appears in 0 sections (marked OK above)
        AND has same-surname collisions > 1, so the entity-resolution trap is real.

        Q4 requires len(res) > 0 above; if it is 0, drop Q4 or widen resolution.
    """).strip())


if __name__ == "__main__":
    main()
