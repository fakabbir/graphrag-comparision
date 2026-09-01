"""GraphRAG - traverse Neo4j for the relational hops, then pull the exact text
from Postgres using the filing_id carried on every edge.

Pipeline (2 LLM calls, same as the SQL baseline):
  1. LLM -> {cypher, text_terms}          plan the traversal + what text to look for
  2. Neo4j  -> entities + filingIds       structural answer, provenance attached
  3. Postgres -> targeted snippets        exact passages from those filings only
  4. LLM -> answer grounded in both
"""
from __future__ import annotations
import json, re, textwrap

from config import TEXT_BUDGET
from llm import ask
from stores import cypher_rows, fetch_snippets
from rowbalance import raise_limit, balance

GRAPH_SCHEMA = textwrap.dedent("""
    Neo4j 5. Node labels and properties:
      (:Company   {cik INT, name, sic, sicDescription, source})
      (:Person    {cik INT, name})                       -- insiders from Forms 3/4/5
      (:Subsidiary{nameNormalized, name, jurisdiction})   -- from EX-21; has NO cik
      (:AuditFirm {firmKey, name, pcaobFirmId})
      (:Filing    {accessionNumber, formType, filingDate DATE, periodOfReport DATE,
                   hasRiskFactors BOOL, riskFactorChars INT, indexUrl})

    Relationships (directions matter, follow exactly):
      (:Company)-[:FILED]->(:Filing)
      (:Person)-[:OFFICER_OF   {filingId, title, filingDate}]->(:Company)
      (:Person)-[:DIRECTOR_OF  {filingId, filingDate}]->(:Company)
      (:Person)-[:OWNS_SHARES  {filingId, basis, filingDate}]->(:Company)
      (:Subsidiary)-[:SUBSIDIARY_OF {filingId, jurisdiction, fiscalYear}]->(:Company)
      (:Company)-[:AUDITED_BY  {filingId, fiscalYear, auditorLocation}]->(:AuditFirm)
      (:Subsidiary)-[:RESOLVES_TO]->(:Company)   -- name-resolved to a known filer

    Notes:
      - Person names are stored EDGAR-style, surname first, upper/mixed case:
        'POLK DENNIS', 'MCCLURE TERI P'. Match with toLower(p.name) CONTAINS 'polk'.
      - Only :Filing nodes with hasRiskFactors=true have Item 1A text in Postgres.
      - Always RETURN the filingId of the edges you traverse and the
        accessionNumber of any :Filing you touch, so the text can be pulled.
""").strip()

PLAN_SYSTEM = textwrap.dedent("""
    You plan a GraphRAG retrieval over an SEC EDGAR knowledge graph.

    Return ONLY a JSON object, no markdown fences:
      {"cypher": "<one read-only Cypher query>",
       "text_terms": ["<keyword>", ...]}

    Cypher rules - THIS IS A TWO-STAGE PIPELINE, you write stage 1 only:
      - Stage 1 (you): identify WHICH entities matter. Return ONE ROW PER ENTITY
        with its identifier - `companyCik` for companies. Do NOT traverse down to
        children (subsidiaries, individual filings) and do NOT return one row per
        child: the system expands every entity you name, completely, in stage 2.
        A fan-out query wastes the LIMIT on one entity's children and silently
        loses every other entity.
      - Read-only. No CREATE/MERGE/SET/DELETE.
      - LIMIT 25 or fewer (you are returning entities, not rows of detail).
      - If the question asks whether ANY node satisfies a condition, put that
        condition inside the MATCH pattern. Never scan a whole label with a LIMIT
        and infer a negative answer from the sample - that is how you get a
        confidently wrong "none exist".
      - Follow the documented relationship directions exactly.
      - RETURN the accessionNumber of every :Filing you touch, aliased `accessionNumber`
        (or `filingId` for edge provenance), plus the human-readable entity names.
      - filingId identifies WHICH DOCUMENT an edge came from. Different edge types
        come from different documents: an OFFICER_OF edge's filingId is a Form 4,
        a SUBSIDIARY_OF edge's filingId is a 10-K/EX-21. NEVER join one edge's
        filingId to another edge's filingId - that always yields zero rows.
      - Do NOT collect() an unbounded list into one row.
    text_terms: 2-4 short keyword phrases to locate the relevant passage inside the
    risk-factor text of the filings the traversal returns (e.g. "supply chain",
    "semiconductor shortage"). Lowercase.
""").strip()

ANSWER_SYSTEM = textwrap.dedent("""
    You are a financial-disclosure analyst.

    You are given (a) structured graph facts, and (b) verbatim excerpts from the
    specific filings those facts point to. Answer using only these.
    The "Complete per-company expansion" block is authoritative and is NOT truncated.
    The traversal rows may be truncated by LIMIT - never infer "none exist" from them.
    Cite the accession number for every claim. Name the traversal path in one line
    (e.g. Person -> Company -> Subsidiary). If a hop produced nothing, say so plainly.
    Be concise: under 300 words. Name every company found. For long subsidiary lists,
    give the count and 5 examples rather than the full list.
""").strip()

ACC_RE = re.compile(r"\b\d{10}-\d{2}-\d{6}\b")
LIST_CAP = 12          # items shown per list-valued column
ROW_CAP  = 900         # chars per row


def _compact(rows: list[dict]) -> str:
    """Serialise graph rows so EVERY row survives.

    Blindly truncating the JSON blob dropped whole companies off the end - the
    traversal was right but the second company never reached the model.
    """
    out = []
    for r in rows:
        item = {}
        for k, v in r.items():
            if isinstance(v, list):
                shown = [str(x) for x in v[:LIST_CAP]]
                item[k] = shown + ([f"... {len(v) - LIST_CAP} more of {len(v)} total"]
                                   if len(v) > LIST_CAP else [])
            else:
                sv = str(v)
                item[k] = sv if len(sv) <= 200 else sv[:200] + "…"
        blob = json.dumps(item, default=str)
        out.append(blob if len(blob) <= ROW_CAP else blob[:ROW_CAP] + "…}")
    return "[\n" + ",\n".join(out) + "\n]"


def _parse_plan(raw: str) -> tuple[str, list[str]]:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.M)
    try:
        obj = json.loads(raw)
        return obj.get("cypher", "").strip(), [t.lower() for t in obj.get("text_terms", [])][:4]
    except Exception:                                            # noqa: BLE001
        m = re.search(r'"cypher"\s*:\s*"(.+?)"\s*[,}]', raw, re.S)
        cy = m.group(1).encode().decode("unicode_escape") if m else raw
        terms = re.findall(r'"([a-z][a-z \-]{3,30})"', raw)
        return cy.strip(), terms[:4]


def _collect_accessions(rows: list[dict]) -> list[str]:
    found: list[str] = []
    for r in rows:
        for v in r.values():
            for s in ([v] if isinstance(v, str) else (v if isinstance(v, list) else [])):
                if isinstance(s, str):
                    found += ACC_RE.findall(s)
    seen, out = set(), []
    for a in found:
        if a not in seen:
            seen.add(a); out.append(a)
    return out


CIK_KEY = re.compile(r"cik", re.I)
NAME_KEY = re.compile(r"compan(y|ies)", re.I)

# Deterministic expansion. The LLM decides WHICH entities matter; the system then
# retrieves them COMPLETELY. Without this, an LLM-authored `LIMIT 50` on a fan-out
# traversal silently starved the second company (all 50 rows were the alphabetically
# first one), and the model then reported it as "no second company found".
EXPAND_CQL = """
UNWIND $ciks AS wanted
MATCH (c:Company {cik: wanted})
OPTIONAL MATCH (s:Subsidiary)-[so:SUBSIDIARY_OF]->(c)
WITH c, count(DISTINCT s) AS subsidiaryCount,
     collect(DISTINCT s.name)[..12] AS subsidiarySample,
     collect(DISTINCT so.filingId)[..1] AS ex21FilingId
OPTIONAL MATCH (c)-[:FILED]->(f:Filing) WHERE f.hasRiskFactors
WITH c, subsidiaryCount, subsidiarySample, ex21FilingId,
     collect(DISTINCT f.accessionNumber) AS riskFilingAccessions
OPTIONAL MATCH (p:Person)-[:OFFICER_OF|DIRECTOR_OF]->(c)
WITH c, subsidiaryCount, subsidiarySample, ex21FilingId, riskFilingAccessions,
     collect(DISTINCT p.name)[..6] AS someInsiders
// reverse direction: is this filer itself named as somebody's subsidiary?
OPTIONAL MATCH (self:Subsidiary)-[:RESOLVES_TO]->(c)
OPTIONAL MATCH (self)-[selfEdge:SUBSIDIARY_OF]->(parentCo:Company)
RETURN c.cik AS companyCik, c.name AS companyName,
       subsidiaryCount, subsidiarySample, ex21FilingId,
       riskFilingAccessions, someInsiders,
       collect(DISTINCT parentCo.name) AS alsoNamedAsSubsidiaryOf,
       collect(DISTINCT selfEdge.filingId) AS namedAsSubsidiaryInFilings
"""


RESOLVE_NAMES_CQL = """
UNWIND $names AS n
MATCH (c:Company) WHERE toLower(c.name) = toLower(n)
RETURN DISTINCT c.cik AS cik
"""


def _collect_ciks(rows: list[dict]) -> list[int]:
    out: set[int] = set()
    for r in rows:
        for k, v in r.items():
            if not CIK_KEY.search(k):
                continue
            vals = v if isinstance(v, list) else [v]
            for x in vals:
                if isinstance(x, bool):
                    continue
                if isinstance(x, int):
                    out.add(x)
                elif isinstance(x, str) and x.strip().isdigit():
                    out.add(int(x))
    return sorted(out)


def _collect_company_names(rows: list[dict]) -> list[str]:
    out: set[str] = set()
    for r in rows:
        for k, v in r.items():
            if not NAME_KEY.search(k):
                continue
            for x in (v if isinstance(v, list) else [v]):
                if isinstance(x, str) and 2 < len(x) < 120 and not x.isdigit():
                    out.add(x)
    return sorted(out)


def run(question: str, *, verbose: bool = True, repair: bool = True) -> dict:
    plan_raw = ask(PLAN_SYSTEM, f"{GRAPH_SCHEMA}\n\nQuestion: {question}\n\nJSON:",
                   max_tokens=800)
    cypher, terms = _parse_plan(plan_raw)
    if not terms:
        terms = [w for w in re.findall(r"[a-z]{5,}", question.lower())][:3]

    rows, error, attempts = [], None, []
    for attempt in range(2 if repair else 1):
        attempts.append(cypher)
        problem = None
        try:
            exec_cypher, old_limit = raise_limit(cypher, cap=2000)
            rows = cypher_rows(exec_cypher, limit=2000)
            error = None
            if not rows:
                problem = ("The query ran but returned 0 rows. Most likely an "
                           "over-constrained MATCH - e.g. joining one edge's filingId "
                           "to a different edge's filingId, or requiring hops that need "
                           "to be OPTIONAL MATCH.")
            elif not _collect_ciks(rows):
                # Contract violation: stage 2 cannot expand entities it cannot identify.
                # This is what made the Q4 traversal answer a confident "none exist" -
                # it returned 2,000 rows of names with no cik, so nothing was expanded.
                problem = (f"The query returned {len(rows)} rows but NONE of the returned "
                           "columns is a company cik, so stage 2 cannot expand the "
                           "entities. Return `companyCik` (aliased exactly) for every "
                           "company of interest. Also: if you used OPTIONAL MATCH for the "
                           "relationship the question is actually asking about, make it a "
                           "required MATCH so you return only real matches instead of "
                           "scanning the whole label.")
        except Exception as e:                                   # noqa: BLE001
            error = str(e).split("\n")[0][:300]
            problem = f"The query failed with error: {error}"
            rows = []
        if problem is None or attempt == 1 or not repair:
            break
        if verbose:
            print(f"  retrying (attempt {attempt+1}): {problem[:90]}")
        fix = ask(PLAN_SYSTEM,
                  f"{GRAPH_SCHEMA}\n\nQuestion: {question}\n\n"
                  f"This Cypher did not work:\n{cypher}\n\n{problem}\n\n"
                  "Write a corrected query. Split independent hops into separate "
                  "OPTIONAL MATCH clauses instead of one over-joined pattern.\n\nJSON:",
                  max_tokens=800)
        cypher, t2 = _parse_plan(fix)
        terms = t2 or terms

    # ── deterministic entity expansion (see EXPAND_CQL) ────────────────────
    ciks = _collect_ciks(rows)
    if not ciks:
        names = _collect_company_names(rows)
        if names:
            try:
                ciks = sorted({r["cik"] for r in cypher_rows(RESOLVE_NAMES_CQL, names=names)})
            except Exception:                                    # noqa: BLE001
                ciks = []
    expanded: list[dict] = []
    if ciks:
        try:
            expanded = cypher_rows(EXPAND_CQL, ciks=ciks)
        except Exception as e:                                   # noqa: BLE001
            if verbose:
                print(f"  expansion failed: {e}")

    accessions = _collect_accessions(rows)

    if rows:
        cols = list(rows[0].keys())
        tuples = [tuple(r.get(c) for c in cols) for r in rows]
        kept, bstats = balance(cols, tuples, per_entity=6, total=48)
        shown = [dict(zip(cols, t)) for t in kept]
    else:
        shown, bstats = [], {"rows_in": 0, "rows_kept": 0, "entities": 0,
                             "entity_column": None}
    graph_facts = _compact(shown) if shown else "(no rows)"
    if bstats["rows_in"] > bstats["rows_kept"]:
        graph_facts += (f"\n[{bstats['rows_in']} rows matched across "
                        f"{bstats['entities']} distinct {bstats['entity_column']} values; "
                        f"showing {bstats['rows_kept']} balanced across all of them]")
    for r in expanded:
        for a in (r.get("riskFilingAccessions") or []):
            if a and a not in accessions:
                accessions.append(a)

    # ── the payoff: exact text pulled by filing_id, not by similarity search ──
    snippets, used = [], 0
    for acc in accessions:
        for sn in fetch_snippets(acc, "1A", terms):
            block = f"[{acc}] 10-K Item 1A (Risk Factors):\n…{sn}…"
            if used + len(block) > TEXT_BUDGET:
                break
            snippets.append(block); used += len(block)

    if verbose:
        print(f"  Cypher:\n{textwrap.indent(cypher, '    ')}")
        print(f"  -> {len(rows)} graph rows"
              + (f"  (ERROR: {error})" if error else ""))
        if rows:
            print(f"  balanced: {bstats}")
        print(f"  entities expanded: {len(expanded)} companies (cik {ciks[:6]}"
              f"{'…' if len(ciks) > 6 else ''})")
        for r in expanded:
            print(f"      {str(r.get('companyName'))[:34]:34s} subs={r.get('subsidiaryCount')} "
                  f"riskFilings={r.get('riskFilingAccessions')}")
        print(f"  -> {len(accessions)} filings identified")
        print(f"  text terms: {terms}")
        print(f"  -> {len(snippets)} targeted snippets ({used:,} chars)")

    answer = ask(ANSWER_SYSTEM, textwrap.dedent(f"""
        Question: {question}

        Cypher executed:
        {cypher}

        Graph facts from the traversal:
        {graph_facts}

        Complete per-company expansion (authoritative - not truncated by any LIMIT):
        {_compact(expanded) if expanded else "(none)"}

        Verbatim excerpts from the filings the traversal identified:
        {chr(10).join(snippets) if snippets else "(none retrieved)"}
    """).strip(), max_tokens=1500)

    return {
        "mode": "graphrag",
        "answer": answer,
        "cypher": cypher,
        "cypher_attempts": attempts,
        "cypher_error": error,
        "graph_rows": len(rows),
        "row_balance": bstats,
        "companies_expanded": [{"cik": r.get("companyCik"), "name": r.get("companyName"),
                                "subsidiaryCount": r.get("subsidiaryCount"),
                                "riskFilings": r.get("riskFilingAccessions")}
                               for r in expanded],
        "filings_identified": accessions,
        "text_terms": terms,
        "snippets": len(snippets),
        "evidence_chars": used + len(graph_facts),
    }
