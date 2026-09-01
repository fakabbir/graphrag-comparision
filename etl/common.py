"""Shared helpers: SEC-compliant fetching, paths, DB connections."""
from __future__ import annotations
import os, time, threading, pathlib, json, re

ROOT     = pathlib.Path(__file__).resolve().parent.parent
DATA     = ROOT / "data"
RAW      = DATA / "raw"
STAGING  = DATA / "staging"
for p in (RAW, STAGING):
    p.mkdir(parents=True, exist_ok=True)

# SEC fair-access policy: declare a User-Agent, stay under 10 req/s.
USER_AGENT = os.environ.get("SEC_USER_AGENT", "SECGraphDemo fakabbir.amin@aman.om")
MAX_RPS    = 8.0

PG_DSN   = os.environ.get("PG_DSN",   "postgresql://sec:secdemo@localhost:55432/secedgar")
NEO4J_URI = os.environ.get("NEO4J_URI",  "bolt://localhost:57687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASS = os.environ.get("NEO4J_PASS", "secdemo123")


class RateLimiter:
    def __init__(self, rps: float):
        self.interval = 1.0 / rps
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                time.sleep(self.next_at - now)
                now = time.monotonic()
            self.next_at = now + self.interval


_limiter = RateLimiter(MAX_RPS)


def _curl(url: str, out_path: str | None, timeout: int) -> tuple[int, bytes]:
    """Run curl and return (http_status, body). Body is empty when out_path is given."""
    import subprocess
    cmd = [
        "curl", "-sS", "--compressed", "--location",
        "-A", USER_AGENT,
        "-H", "Accept-Encoding: gzip, deflate",
        "--max-time", str(timeout),
        "-w", "%{http_code}",
        "-o", out_path if out_path else "-",
        url,
    ]
    if out_path:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 15)
        code = (r.stdout or b"").strip()[-3:]
        return (int(code) if code.isdigit() else 0, b"")
    # body to stdout: -w output is appended, so split the trailing 3 status bytes
    r = subprocess.run(cmd, capture_output=True, timeout=timeout + 15)
    blob = r.stdout or b""
    code, body = blob[-3:], blob[:-3]
    return (int(code) if code.isdigit() else 0, body)


def sec_get(url: str, *, retries: int = 4, timeout: int = 90) -> bytes:
    """GET an SEC URL with the required headers and a global rate limit.

    Uses curl rather than `requests`: urllib3 in this environment is ~70x slower on
    sec.gov responses (100s+ vs 1.4s for the same gzipped 8 MB document).
    """
    last = None
    for attempt in range(retries):
        _limiter.wait()
        try:
            status, body = _curl(url, None, timeout)
            if status == 200:
                return body
            last = f"HTTP {status}"
        except Exception as e:                       # noqa: BLE001
            last = repr(e)
        time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"failed to fetch {url}: {last}")


def sec_download(url: str, dest, *, retries: int = 4, timeout: int = 120) -> int:
    """Stream an SEC URL straight to disk. Returns bytes written."""
    dest = pathlib.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last = None
    for attempt in range(retries):
        _limiter.wait()
        try:
            status, _ = _curl(url, str(tmp), timeout)
            if status == 200 and tmp.exists() and tmp.stat().st_size > 0:
                tmp.replace(dest)
                return dest.stat().st_size
            last = f"HTTP {status}"
        except Exception as e:                       # noqa: BLE001
            last = repr(e)
        time.sleep(min(2 ** attempt, 8))
    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"failed to download {url}: {last}")


def write_jsonl(name: str, rows) -> int:
    path = STAGING / name
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            n += 1
    print(f"  wrote {path.relative_to(ROOT)}  ({n:,} rows)")
    return n


def read_jsonl(name: str):
    path = STAGING / name
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


_WS = re.compile(r"\s+")
# Applied AFTER punctuation is folded to spaces, so "L.P." arrives as "l p".
_CORP_SUFFIX = re.compile(
    r"\s\b("
    r"incorporated|inc|corporation|corp|company|co|limited|ltd|"
    r"llc|l l c|lp|l p|llp|l l p|plc|p l c|"
    r"gmbh|sa|s a|sarl|s a r l|bv|b v|nv|n v|pty|ag|kk|k k|"
    r"sdn|bhd|oy|ab|as|a s|spa|s p a"
    r")$", re.I)
_LEADING_THE = re.compile(r"^the\s+", re.I)


def normalize_name(name: str) -> str:
    """Fold a company name to a comparison key. Deliberately conservative."""
    s = _WS.sub(" ", (name or "")).strip().lower()
    s = s.replace("&", "and")
    s = re.sub(r"[^a-z0-9 /]", " ", s)
    s = _WS.sub(" ", s).strip()
    s = _LEADING_THE.sub("", s)
    for _ in range(4):                      # strip stacked suffixes: "Foo Holdings Inc. Ltd."
        new = _CORP_SUFFIX.sub("", s).strip()
        if new == s or not new:
            break
        s = new
    return _WS.sub(" ", s).strip()
