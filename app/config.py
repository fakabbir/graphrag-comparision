"""Central config. Secrets come from the environment only - never committed."""
from __future__ import annotations
import os, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

PG_DSN     = os.environ.get("PG_DSN",     "postgresql://sec:secdemo@localhost:55432/secedgar")
NEO4J_URI  = os.environ.get("NEO4J_URI",  "bolt://localhost:57687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASS = os.environ.get("NEO4J_PASS", "secdemo123")

# Every mode talks to Bifrost, an OpenAI-compatible LLM gateway, rather than to a
# provider SDK. That keeps the model a request parameter: all three modes are
# guaranteed to use the same one, and switching providers is a string change.
# Set GATEWAY_BASE_URL="" to bypass the gateway and hit DeepSeek directly.
GATEWAY_BASE_URL = os.environ.get("GATEWAY_BASE_URL", "http://127.0.0.1:8080/v1")

# DeepSeek is OpenAI-API-compatible, so langchain_openai can also talk to it direct.
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL    = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
EMBED_MODEL       = os.environ.get("EMBED_MODEL", str(ROOT / "data/models/all-MiniLM-L6-v2"))

TOP_K       = int(os.environ.get("TOP_K", "8"))       # vector-RAG chunks
TEXT_BUDGET = int(os.environ.get("TEXT_BUDGET", "9000"))  # chars of evidence per answer


def require_api_key() -> str:
    """The key the *client* presents.

    Bifrost holds the real provider credentials, and Bedrock needs none at all
    because it authenticates with the instance profile. When the gateway is in
    front, this is only a placeholder that satisfies the OpenAI client.
    """
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    if GATEWAY_BASE_URL:
        return "gateway"
    raise SystemExit(
        "DEEPSEEK_API_KEY is not set and no gateway is configured.\n"
        "  export DEEPSEEK_API_KEY='sk-...'\n"
        "  or point GATEWAY_BASE_URL at a Bifrost instance.\n"
    )
