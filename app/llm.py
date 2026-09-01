"""One shared LLM handle + token accounting, so all three modes are comparable."""
from __future__ import annotations
import os, threading
from dataclasses import dataclass, field

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from config import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, require_api_key


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, resp) -> None:
        md = getattr(resp, "response_metadata", {}) or {}
        u = md.get("token_usage") or (getattr(resp, "usage_metadata", {}) or {})
        p = u.get("prompt_tokens") or u.get("input_tokens") or 0
        c = u.get("completion_tokens") or u.get("output_tokens") or 0
        det = (u.get("completion_tokens_details") or {})
        r = det.get("reasoning_tokens") or 0
        with self._lock:
            self.calls += 1
            self.prompt_tokens += p
            self.completion_tokens += c
            self.reasoning_tokens += r
        # also tally against the calling thread, if it opted in
        if hasattr(_tl, "calls"):
            _tl.calls += 1
            _tl.prompt += p
            _tl.completion += c

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def snapshot(self) -> dict:
        return {"calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "reasoning_tokens": self.reasoning_tokens, "total_tokens": self.total}


USAGE = Usage()

# Per-thread accounting. The local server answers one question with three modes in
# three threads; a global before/after delta attributed every thread's tokens to
# whichever one happened to read the counter, so each column reported the sum.
_tl = threading.local()


def begin_request() -> None:
    _tl.calls = 0
    _tl.prompt = 0
    _tl.completion = 0


def request_usage() -> dict:
    return {"calls": getattr(_tl, "calls", 0),
            "prompt_tokens": getattr(_tl, "prompt", 0),
            "completion_tokens": getattr(_tl, "completion", 0),
            "total_tokens": getattr(_tl, "prompt", 0) + getattr(_tl, "completion", 0)}


_clients: dict[tuple, ChatOpenAI] = {}


# DeepSeek v4 thinks by default: an uncapped reasoning trace burned ~33k completion
# tokens per call (max_tokens only bounds the visible answer). "none" disables it.
# Same setting for every mode, so the comparison isolates retrieval architecture.
REASONING = os.environ.get("REASONING_EFFORT", "none")


def llm(temperature: float = 0.0, max_tokens: int = 900,
        reasoning: str | None = None) -> ChatOpenAI:
    """One client per (temperature, max_tokens, reasoning). A single cached client
    silently reuses the FIRST call's max_tokens, which let answers run to 20k+."""
    reasoning = REASONING if reasoning is None else reasoning
    key = (temperature, max_tokens, reasoning)
    if key not in _clients:
        kw = {}
        if reasoning not in ("", "default"):
            kw["reasoning_effort"] = reasoning     # first-class param in langchain_openai
        _clients[key] = ChatOpenAI(
            model=DEEPSEEK_MODEL,
            api_key=require_api_key(),
            base_url=DEEPSEEK_BASE_URL,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=180,
            max_retries=2,
            **kw,
        )
    return _clients[key]


def ask(system: str, user: str, *, max_tokens: int = 900,
        reasoning: str | None = None) -> str:
    m = llm(max_tokens=max_tokens, reasoning=reasoning)
    resp = m.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    USAGE.add(resp)
    return (resp.content or "").strip()
