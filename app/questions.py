"""Benchmark question set — 20 questions, 5 of each type, with objective ground truth.

REGENERATE AFTER EVERY LOAD. The questions name specific companies, people and
accession numbers, so they are valid for exactly one corpus window. Use
scripts/discover_ground_truth.py, then verify each claim before pasting.

Corpus: SEC EDGAR monthly XBRL RSS, 2025-09 -> 2026-08.
  167,050 filings · 10,511 companies · 23,649 sections · 508,714 vector chunks
  212,124 subsidiaries · 150,917 ownership edges · 5,835 auditor tags
  Neo4j: 430,257 nodes / 561,144 edges (1,232 RESOLVES_TO)

Four types, deliberately including two the graph should NOT win:
  T1  single-document semantic       -> vector RAG should do well
  T2  structured aggregation         -> text-to-SQL should do well
  T3  multi-hop relational + text    -> the killer query
  T4  cross-document entity resolution -> needs the graph
"""
from __future__ import annotations

# ── T3 subjects ─────────────────────────────────────────────────────────────
# Each was selected on measured evidence, not convenience. Two conditions had to
# hold simultaneously, checked with the GIN index over all 5,210 Item 1A sections:
#
#   (a) the surname appears in ZERO risk-factor sections, so no similarity search
#       can reach the person at all;
#   (b) two or more distinct owner_ciks share that surname, so resolving identity
#       by name is genuinely wrong rather than merely inelegant.
#
# Rejected for (a): harris 32 sections, anderson 49, richards 63, johnson 109.
# Rejected for (b): sengstack, fearon, dugle, saintil, joerres - unique surnames,
# so they cannot demonstrate the identity failure (sengstack is kept anyway as a
# control: same shape, no trap).

WENDLING = {                       # Wendling Brian J, owner_cik 1663090
    "0001158172-26-000009",        # COMSCORE            51 subs
    "0001104659-26-020653",        # Liberty Media       45 subs
    "0001104659-26-013442",        # GCI Liberty         30 subs
    "0001104659-26-020657",        # Liberty Live        15 subs
    "0001104659-26-010397",        # Liberty Broadband   10 subs
}
LOZANO = {                         # LOZANO MONICA C, owner_cik 1179864
    "0000070858-26-000157",        # BANK OF AMERICA     55 subs
    "0000320193-25-000079",        # Apple Inc.          18 subs
    "0000027419-26-000016",        # TARGET CORP          6 subs
}
GILES = {                          # GILES WILLIAM T, owner_cik 1199820
    "0000016918-26-000011",        # CONSTELLATION BRANDS 91 subs
    "0000703351-26-000029",        # BRINKER INTERNATIONAL 40 subs
    "0001628280-26-009770",        # Floor & Decor         8 subs
}
BRUNER = {                         # BRUNER JUDY, owner_cik 1112668
    "0001628280-25-056742",        # APPLIED MATERIALS    72 subs
    "0001137789-26-000159",        # Seagate Technology   53 subs
    "0001628280-26-032873",        # Qorvo                44 subs
}
SENGSTACK = {                      # SENGSTACK GREGG C, owner_cik 1189242
    "0001579241-26-000007",        # Allegion plc        115 subs
    "0001193125-25-296204",        # Woodward             44 subs
    "0001350593-25-000066",        # Mueller Water        40 subs
    "0000038725-26-000009",        # FRANKLIN ELECTRIC    37 subs
}

# ── T1: verified form_type='10-K', item_code='1A', 70k-190k chars ───────────
T1 = [
    ("Moderna, Inc.", "0001682852-26-000033", "Moderna",
     "manufacturing and supply chain",
     ["supply chain", "manufactur", "supplier"]),
    ("Yum China Holdings, Inc.", "0001193125-26-082824", "Yum China",
     "supply chain and food safety",
     ["supply chain", "supplier", "food"]),
    ("ROKU, INC", "0001628280-26-008114", "Roku",
     "cybersecurity and data privacy",
     ["cyber", "privacy", "data"]),
    ("SentinelOne, Inc.", "0001583708-26-000020", "SentinelOne",
     "cybersecurity and competition",
     ["cyber", "competit", "security"]),
    ("Privia Health Group, Inc.", "0001759655-26-000010", "Privia Health",
     "regulatory and reimbursement",
     ["regulat", "reimburs", "healthcare"]),
]

# ── T2: answers computed directly from the loaded tables ───────────────────
T2 = [
    ("Which audit firm appears as the auditor for the most companies in this "
     "dataset, and how many distinct companies does it audit?",
     ["Ernst & Young"], ["737"]),
    ("Which company discloses the most subsidiaries in its EX-21 exhibit, and "
     "how many does it disclose?",
     ["Ventas"], ["2,780", "2780"]),
    ("What is the most common jurisdiction of incorporation among all disclosed "
     "subsidiaries, and how many subsidiaries are incorporated there?",
     ["Delaware"], ["62,504", "62504"]),
    ("Which SEC form type appears most often in this dataset, and how many "
     "filings of that type are there?",
     ["8-K"], ["63,490", "63490"]),
    ("Which issuer has the most distinct insiders filing Forms 3, 4 or 5 against "
     "it, and how many distinct insiders are there?",
     ["Medline"], ["89"]),
]

# ── T3: (subject, display name, accessions, required, forbidden) ────────────
T3 = [
    ("Brian Wendling", WENDLING,
     ["Comscore", "Liberty Media", "GCI Liberty"],
     ["ChoiceOne", "Sidus Space"]),
    ("Monica Lozano", LOZANO,
     ["Apple", "Bank of America", "Target"],
     ["Mondelez", "Ternium"]),
    ("William Giles", GILES,
     ["Constellation Brands", "Brinker", "Floor & Decor"],
     ["Hawthorn", "Mission Produce", "Core & Main", "Scripps"]),
    ("Judy Bruner", BRUNER,
     ["Applied Materials", "Seagate", "Qorvo"],
     ["Smith Midland"]),
    ("Gregg Sengstack", SENGSTACK,
     ["Allegion", "Woodward", "Mueller Water"],
     []),                          # unique surname: the control case
]

# ── T4: verified subsidiary-name -> independent filer pairs ────────────────
# The parent field accepts alternatives separated by "|". Most of these
# subsidiaries are listed by exactly one parent, but AllianceBernstein Holding
# L.P. is listed by three (verified against the loaded subsidiary table), so
# naming any one of them answers the question as asked. Encoding only one was a
# scoring defect: it marked a true answer wrong.
T4 = [
    ("American Airlines, Inc.", "American Airlines Group", 4515),
    ("AEP Texas Inc.", "American Electric Power", 1721781),
    ("Athene Holding Ltd.", "Apollo Global Management", 1527469),
    ("Alexander's, Inc.", "Vornado Realty Trust", 3499),
    ("AllianceBernstein Holding L.P.",
     "AllianceBernstein L.P.|Equitable Holdings|AllianceBernstein Holding", 825313),
]


def _build() -> list[dict]:
    qs: list[dict] = []

    for i, (company, acc, short, topic, kws) in enumerate(T1, 1):
        qs.append({
            "id": f"T1-{i}", "type": "T1",
            "kind": "single-document semantic",
            "expect_winner": "vector RAG or GraphRAG",
            "question": (f"What {topic} risks did {company} disclose in the risk "
                         f"factors of its 10-K?"),
            "valid_accessions": {acc},
            "required_entities": [short],
            "required_any": kws,
            "forbidden_entities": [],
        })

    for i, (q, req, anys) in enumerate(T2, 1):
        qs.append({
            "id": f"T2-{i}", "type": "T2",
            "kind": "structured aggregation",
            "expect_winner": "text-to-SQL",
            "question": q,
            "valid_accessions": set(),      # aggregate; citations optional
            "required_entities": req,
            "required_any": anys,
            "forbidden_entities": [],
        })

    for i, (person, accs, req, forb) in enumerate(T3, 1):
        qs.append({
            "id": f"T3-{i}", "type": "T3",
            "kind": "multi-hop relational + text",
            "expect_winner": "GraphRAG",
            "question": (
                f"{person} holds an insider role at more than one company in this "
                f"dataset. For every company where they are an officer or director, "
                f"list that company's disclosed subsidiaries and summarise the "
                f"cybersecurity risks that company reported in its 10-K risk factors."
            ),
            "valid_accessions": set(accs),
            "required_entities": req,
            "required_any": ["cyber"],       # verified present in all 18 filings
            "forbidden_entities": forb,
        })

    for i, (sub, parent, filer_cik) in enumerate(T4, 1):
        qs.append({
            "id": f"T4-{i}", "type": "T4",
            "kind": "cross-document entity resolution",
            "expect_winner": "GraphRAG",
            "question": (
                f"Is \"{sub}\" listed as a subsidiary in another company's EX-21 "
                f"exhibit while also being an SEC filer in its own right? If so, name "
                f"the parent that lists it and confirm whether it files separately."
            ),
            "valid_accessions": set(),
            "required_entities": [parent],
            "required_any": ["subsidiar", "filer", "files", "yes"],
            "forbidden_entities": [],
        })

    return qs


QUESTIONS = _build()

# The killer query keeps a stable id so the UI and the playground can feature it.
KILLER_ID = "T3-2"     # Monica Lozano -> Apple / Bank of America / Target
