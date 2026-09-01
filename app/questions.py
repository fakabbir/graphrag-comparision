"""Benchmark question set with objective ground truth.

Deliberately a SPECTRUM, not four copies of the query GraphRAG wins:
  Q1  single-document semantic lookup   -> vector RAG should do well (fair control)
  Q2  aggregation over structured cols  -> text-to-SQL should do well
  Q3  multi-hop relational + text       -> the "killer query"
  Q4  entity resolution across doc types-> needs the graph's RESOLVES_TO edge

Ground truth was verified directly against the loaded databases, not assumed.
"""
from __future__ import annotations

# accessions that legitimately answer each question
TD_SYNNEX_10K  = "0001564590-22-003003"     # 220 subsidiaries, 76,118 chars Item 1A
CONCENTRIX_10K = "0001803599-22-000025"     # 163 subsidiaries, 64,804 chars Item 1A

QUESTIONS = [
    {
        "id": "Q1",
        "kind": "single-document semantic",
        "expect_winner": "vector RAG or GraphRAG",
        "question": (
            "What supply chain and component shortage risks did TD SYNNEX disclose "
            "in the risk factors of its 10-K?"
        ),
        "valid_accessions": {TD_SYNNEX_10K},
        "required_entities": ["TD SYNNEX"],
        "required_any": ["supply chain", "shortage", "logistics", "component"],
        "forbidden_entities": [],
    },
    {
        "id": "Q2",
        "kind": "structured aggregation",
        "expect_winner": "text-to-SQL",
        "question": (
            "Which audit firm appears as the auditor for the most companies in this "
            "dataset, and how many companies does it audit?"
        ),
        "valid_accessions": set(),          # aggregate answer, citations optional
        "required_entities": ["Ernst & Young"],
        "required_any": ["5", "five"],
        "forbidden_entities": [],
    },
    {
        "id": "Q3",
        "kind": "multi-hop relational + text  (THE KILLER QUERY)",
        "expect_winner": "GraphRAG",
        "question": (
            "Dennis Polk holds an insider role at more than one company in this dataset. "
            "For every company where he is an officer or director, list that company's "
            "disclosed subsidiaries and summarise the supply chain risks that company "
            "reported in its 10-K risk factors."
        ),
        "valid_accessions": {TD_SYNNEX_10K, CONCENTRIX_10K},
        "required_entities": ["TD SYNNEX", "Concentrix"],
        "required_any": ["supply chain"],
        # Verified against reporting_owner: four DIFFERENT people are named Polk, with
        # four distinct owner_ciks. Naming any of their companies as Dennis Polk's is
        # a factual error, not a near-miss:
        #   1266254 POLK BENJAMIN  -> Monster Beverage
        #   1270710 POLK DENNIS    -> Concentrix + TD SYNNEX   (the subject)
        #   1678762 Polk James C   -> Bank of Hawaii
        #   1192505 POLK MICHAEL B -> Colgate Palmolive
        "forbidden_entities": ["Monster Beverage", "Bank of Hawaii", "Colgate",
                               # what a similarity-driven retriever grabbed instead
                               "Cannagistics", "Concrete Pumping",
                               "Executive Network Partnering", "Dongfang"],
    },
    {
        "id": "Q4",
        "kind": "cross-document entity resolution",
        "expect_winner": "GraphRAG",
        "question": (
            "Are any companies listed as subsidiaries in an EX-21 exhibit also SEC "
            "filers in their own right in this dataset? Name them and their parent."
        ),
        "valid_accessions": set(),
        "required_entities": ["CCO Holdings"],
        "required_any": ["Charter", "Jefferies", "filer", "subsidiar"],
        "forbidden_entities": [],
    },
]
