"""Central config. Secrets come from the environment only - never committed."""
from __future__ import annotations
import os, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

PG_DSN     = os.environ.get("PG_DSN",     "postgresql://sec:secdemo@localhost:55432/secedgar")
NEO4J_URI  = os.environ.get("NEO4J_URI",  "bolt://localhost:57687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASS = os.environ.get("NEO4J_PASS", "secdemo123")

# DeepSeek is OpenAI-API-compatible, so langchain_openai talks to it directly.
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL    = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
EMBED_MODEL       = os.environ.get("EMBED_MODEL", str(ROOT / "data/models/all-MiniLM-L6-v2"))

TOP_K       = int(os.environ.get("TOP_K", "8"))       # vector-RAG chunks
TEXT_BUDGET = int(os.environ.get("TEXT_BUDGET", "9000"))  # chars of evidence per answer


def require_api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "DEEPSEEK_API_KEY is not set.\n"
            "  export DEEPSEEK_API_KEY='sk-...'\n"
        )
    return key
