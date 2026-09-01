"""Baseline A - direct database access via text-to-SQL (no knowledge graph).

Deliberately a STRONG baseline, not a strawman: the model is given the complete
schema including the `subsidiary` and `reporting_owner` tables, so the multi-hop
answer *is* reachable by joins. What it has to do unaided is pick the join path,
decode the multi-valued `relationship` string, and know that traversing an
ownership chain of unknown depth needs a recursive CTE.
"""
from __future__ import annotations
import re, textwrap

from llm import ask
from stores import sql_rows
from rowbalance import raise_limit, balance

SCHEMA = textwrap.dedent("""
    -- PostgreSQL 16. Data slice: SEC EDGAR filings from xbrlrss-2022-01 (Jan 2022)
    -- plus the Form 3/4/5 insider dataset for 2022Q1.

    company(cik PK, name, sic, sic_description, fiscal_year_end, source)
      -- source='rss' means the company has filings/text; 'form345' means ownership edges only.

    filing(accession_number PK CHAR(20), company_cik FK->company.cik, form_type,
           filing_date DATE, period_of_report DATE, index_url, primary_doc_url)

    filing_document(accession_number FK, sequence, doc_type, filename, description,
                    size_bytes, inline_xbrl, url)
      -- doc_type like '10-K', 'EX-21.1', 'EX-99.1'.

    filing_section(accession_number FK, item_code, item_title, section_text TEXT,
                   char_len, company_cik, filing_date)
      -- item_code IN ('1','1A','1B','2','3','7','7A'). '1A' = Risk Factors.
      -- section_text is the full narrative text; use ILIKE for keyword search.

    subsidiary(accession_number FK, parent_cik, subsidiary_name, name_normalized,
               jurisdiction)
      -- parsed from EX-21 exhibits. Subsidiaries have NO cik.

    reporting_owner(accession_number, owner_cik, owner_name, issuer_cik, issuer_name,
                    relationship, is_officer BOOL, is_director BOOL,
                    is_ten_pct_owner BOOL, is_other BOOL, officer_title,
                    filing_date, period_of_report)
      -- one row per (Form 3/4/5 filing, insider). relationship is a comma-joined
      -- string like 'Director,Officer'. Use the boolean columns instead.
      -- owner_name uses the EDGAR convention SURNAME FIRST, e.g. 'POLK DENNIS',
      -- 'MCCLURE TERI P'. Do NOT assume 'Firstname Lastname': match a single
      -- surname token, e.g. owner_name ILIKE '%polk%'.
      -- issuer_name is the company name as filed, e.g. 'TD SYNNEX CORP'.

    filing_auditor(accession_number PK, company_cik, auditor_name, auditor_location,
                   pcaob_firm_id, fiscal_year)

    v_risk_factors(accession_number, company_cik, company_name, sic, sic_description,
                   filing_date, char_len, section_text)   -- filing_section WHERE item_code='1A'
""").strip()

GEN_SYSTEM = textwrap.dedent("""
    You write a single read-only PostgreSQL query answering the user's question.

    Rules:
      - Output ONLY the SQL. No prose, no markdown fences.
      - SELECT or WITH only. Never write data.
      - Always LIMIT to at most 50 rows.
      - section_text columns are very large (avg 75,000 chars): never SELECT them raw,
        and note that left(section_text, 400) returns only the section HEADER, not the
        relevant passage. To pull the passage around a keyword, use either:
            substring(section_text
                      from greatest(1, position('supply chain' in lower(section_text)) - 400)
                      for 1200)
        or:
            ts_headline('english', section_text, plainto_tsquery('english','supply chain'),
                        'MaxFragments=3, MaxWords=60, MinWords=25')
      - Prefer the boolean flags (is_officer, is_director, is_ten_pct_owner)
        over parsing the `relationship` string.
      - Beware the LIMIT trap: if the question covers several entities and each can
        have hundreds of child rows, one entity will consume the whole LIMIT and the
        others vanish. Aggregate per entity instead - count(*) plus a handful of
        examples via string_agg/array_agg - so every entity appears in the result.
      - If the question asks whether ANY row satisfies a condition, put the condition
        in the WHERE clause. Never infer a negative from a LIMITed sample.
""").strip()

ANSWER_SYSTEM = textwrap.dedent("""
    You are a financial-disclosure analyst. Answer using ONLY the query result rows.
    Cite accession numbers for factual claims. If the rows are empty or do not
    support an answer, say exactly that and name what is missing - do not speculate.
    Be concise: under 300 words.
""").strip()


def _clean_sql(s: str) -> str:
    s = re.sub(r"^```(?:sql)?\s*|\s*```$", "", s.strip(), flags=re.I | re.M)
    return s.strip().rstrip(";")


def run(question: str, *, verbose: bool = True, repair: bool = True) -> dict:
    prompt = f"{SCHEMA}\n\nQuestion: {question}\n\nSQL:"
    sql = _clean_sql(ask(GEN_SYSTEM, prompt, max_tokens=700))
    attempts, error = [], None

    cols, rows = [], []
    for attempt in range(2 if repair else 1):
        attempts.append(sql)
        problem = None
        try:
            exec_sql, old_limit = raise_limit(sql, cap=2000)
            cols, rows = sql_rows(exec_sql, limit=2000)
            error = None
            if not rows:
                problem = ("The query executed but returned 0 rows. Likely an "
                           "over-restrictive predicate or a wrong join path.")
        except Exception as e:                                   # noqa: BLE001
            error = str(e).split("\n")[0][:300]
            problem = f"The query failed with error: {error}"
            cols, rows = [], []
        if problem is None or attempt == 1 or not repair:
            break
        if verbose:
            print(f"  retrying (attempt {attempt+1}): {problem[:90]}")
        sql = _clean_sql(ask(
            GEN_SYSTEM,
            f"{SCHEMA}\n\nQuestion: {question}\n\n"
            f"This query did not work:\n{sql}\n\n{problem}\n\n"
            "Re-read the schema comments (especially name conventions) and write a "
            "corrected query. Relax predicates that may be too strict.\n\nCorrected SQL:",
            max_tokens=700))

    kept, bstats = balance(cols, rows, per_entity=6, total=48)

    if verbose:
        print(f"  SQL:\n{textwrap.indent(sql, '    ')}")
        print(f"  -> {len(rows)} rows" + (f"  (ERROR: {error})" if error else ""))
        if rows:
            print(f"  balanced: {bstats}")


    def fmt(v):
        s = str(v)
        return s if len(s) <= 300 else s[:300] + "…"

    table = (" | ".join(cols) + "\n" + "\n".join(
        " | ".join(fmt(v) for v in r) for r in kept)) if kept else "(no rows)"
    if bstats["rows_in"] > bstats["rows_kept"]:
        table += (f"\n\n[{bstats['rows_in']} rows matched across "
                  f"{bstats['entities']} distinct {bstats['entity_column']} values; "
                  f"showing {bstats['rows_kept']} balanced across all of them]")

    answer = ask(ANSWER_SYSTEM,
                 f"Question: {question}\n\nSQL executed:\n{sql}\n\nResult:\n{table}",
                 max_tokens=1500)
    return {
        "mode": "text_to_sql",
        "answer": answer,
        "sql": sql,
        "sql_attempts": attempts,
        "sql_error": error,
        "row_count": len(rows),
        "row_balance": bstats,
        "evidence_chars": len(table),
    }
