#!/usr/bin/env python
"""Stage 4 - extract structure from the fetched documents.

  10-K primary doc -> filing_section rows (Item 1, 1A, 3, 7, 7A)  [SQL text store]
                   -> dei:Auditor* cover-page tags                [:AUDITED_BY edge]
  EX-21 exhibit    -> subsidiary name + jurisdiction              [:SUBSIDIARY_OF edge]

Section splitting heuristic: find every "ITEM <n><letter>" heading in the flattened
text, slice between consecutive headings, and for each item code keep the occurrence
with the LONGEST body. That discards table-of-contents hits (which sit a few
characters apart) without needing to locate the TOC itself.
"""
from __future__ import annotations
import sys, re, pathlib, collections, json

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from common import RAW, read_jsonl, write_jsonl, normalize_name   # noqa: E402

from lxml import html as LH

# ── HTML -> block-aware plain text ──────────────────────────────────────────
_BLOCK = {"p", "div", "br", "tr", "td", "th", "li", "h1", "h2", "h3", "h4", "h5",
          "h6", "table", "section", "article"}


def html_to_text(raw: bytes) -> str:
    try:
        doc = LH.fromstring(raw)
    except Exception:                                             # noqa: BLE001
        return ""
    for bad in doc.xpath("//script|//style|//noscript"):
        bad.getparent().remove(bad)
    parts: list[str] = []

    def walk(el):
        if el.tag in _BLOCK:
            parts.append("\n")
        if el.text:
            parts.append(el.text)
        for child in el:
            walk(child)
            if child.tail:
                parts.append(child.tail)
        if el.tag in _BLOCK:
            parts.append("\n")

    walk(doc)
    txt = "".join(parts)
    txt = txt.replace("\xa0", " ").replace("​", "")
    txt = re.sub(r"[ \t\r\f\v]+", " ", txt)
    txt = re.sub(r" *\n *", "\n", txt)
    txt = re.sub(r"\n{2,}", "\n", txt)
    return txt.strip()


# ── section splitting ───────────────────────────────────────────────────────
ITEM_RE = re.compile(
    r"(?:^|\n)\s*"
    r"(?:PART\s+[IVX]+\s*[\.\-–—:]?\s*)?"
    r"ITEM\s*(?P<num>\d{1,2})\s*(?P<let>[A-C])?\s*"
    r"[\.\)\-–—:]*\s*",
    re.I)

WANTED = {
    "1":  "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "2":  "Properties",
    "3":  "Legal Proceedings",
    "7":  "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
}


def split_items(text: str) -> dict[str, str]:
    marks = []
    for m in ITEM_RE.finditer(text):
        code = m.group("num").lstrip("0") or "0"
        if m.group("let"):
            code += m.group("let").upper()
        marks.append((m.start(), m.end(), code))
    if not marks:
        return {}

    best: dict[str, str] = {}
    for i, (_s, e, code) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[e:end].strip()
        if len(body) > len(best.get(code, "")):
            best[code] = body
    # A real section has substance; TOC/cross-reference hits do not.
    return {c: b for c, b in best.items() if c in WANTED and len(b) >= 800}


# ── auditor cover-page tags ─────────────────────────────────────────────────
AUDITOR_TAGS = {"dei:AuditorName": "auditor_name",
                "dei:AuditorLocation": "auditor_location",
                "dei:AuditorFirmId": "pcaob_firm_id"}


def extract_auditor(raw: bytes) -> dict:
    try:
        doc = LH.fromstring(raw)
    except Exception:                                             # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for el in doc.xpath("//*[@name]"):
        key = AUDITOR_TAGS.get((el.get("name") or "").strip())
        if key and key not in out:
            val = re.sub(r"\s+", " ", el.text_content()).strip()
            if val:
                out[key] = val
    return out


# ── Exhibit 21 -> subsidiaries ──────────────────────────────────────────────
JURIS_HINT = re.compile(
    r"\b(delaware|nevada|california|texas|new york|florida|illinois|ohio|georgia|"
    r"virginia|washington|colorado|arizona|michigan|minnesota|missouri|maryland|"
    r"massachusetts|pennsylvania|north carolina|south carolina|tennessee|indiana|"
    r"wisconsin|oregon|utah|iowa|kansas|kentucky|louisiana|oklahoma|connecticut|"
    r"new jersey|alabama|arkansas|mississippi|nebraska|new mexico|nevada|idaho|"
    r"montana|wyoming|alaska|hawaii|maine|new hampshire|vermont|rhode island|"
    r"west virginia|north dakota|south dakota|puerto rico|"
    r"united states|u\.s\.|usa|england|wales|scotland|united kingdom|u\.k\.|ireland|"
    r"netherlands|luxembourg|germany|france|switzerland|spain|italy|belgium|sweden|"
    r"norway|denmark|finland|austria|poland|portugal|greece|turkey|israel|"
    r"canada|ontario|quebec|british columbia|alberta|mexico|brazil|argentina|chile|"
    r"colombia|peru|panama|bermuda|cayman|bahamas|barbados|british virgin islands|"
    r"jersey|guernsey|isle of man|gibraltar|malta|cyprus|"
    r"china|hong kong|taiwan|japan|south korea|korea|singapore|malaysia|thailand|"
    r"vietnam|indonesia|philippines|india|australia|new zealand|"
    r"south africa|nigeria|kenya|egypt|morocco|uae|united arab emirates|saudi arabia|"
    r"qatar|russia|ukraine|czech|hungary|romania|slovakia|slovenia|croatia|bulgaria)\b",
    re.I)

NOISE = re.compile(
    r"^(subsidiar|name|entity|jurisdiction|state|country|organization|incorporat|"
    r"exhibit|list of|percentage|owned|note|pursuant|the following|as of|omitted|"
    r"registrant|parent|\(|\*|\d+\s*$|table of)", re.I)


def parse_exhibit21(raw: bytes) -> list[dict]:
    try:
        doc = LH.fromstring(raw)
    except Exception:                                             # noqa: BLE001
        return []
    rows: list[list[str]] = []
    for tr in doc.xpath("//tr"):
        cells = [re.sub(r"\s+", " ", td.text_content()).strip()
                 for td in tr.xpath("./td|./th")]
        cells = [c for c in cells if c]
        if cells:
            rows.append(cells)

    out: list[dict] = []
    if rows:
        for cells in rows:
            name = cells[0].strip(" .·-–—")
            if len(name) < 3 or len(name) > 200 or NOISE.match(name):
                continue
            juris = None
            for c in cells[1:]:
                if JURIS_HINT.search(c):
                    juris = c.strip(" .")
                    break
            if juris is None and len(cells) >= 2 and len(cells[1]) <= 60:
                juris = cells[1].strip(" .") or None
            out.append({"subsidiary_name": name, "jurisdiction": juris})

    if not out:                                    # no usable table: fall back to lines
        for line in html_to_text(raw).split("\n"):
            line = line.strip()
            if len(line) < 3 or len(line) > 200 or NOISE.match(line):
                continue
            m = JURIS_HINT.search(line)
            if m and m.start() > 2:
                out.append({"subsidiary_name": line[:m.start()].strip(" .\t-–—,"),
                            "jurisdiction": line[m.start():].strip(" .")})
            elif len(line.split()) >= 2:
                out.append({"subsidiary_name": line, "jurisdiction": None})

    # dedupe within the exhibit
    seen, uniq = set(), []
    for r in out:
        k = normalize_name(r["subsidiary_name"])
        if not k or k in seen:
            continue
        seen.add(k)
        r["name_normalized"] = k
        uniq.append(r)
    return uniq


def main() -> None:
    manifest = list(read_jsonl("fetch_manifest.jsonl"))
    filings = {f["accession_number"]: f for f in read_jsonl("filings.jsonl")}
    print(f"manifest entries: {len(manifest)}")

    sections, subs, auditors = [], [], []
    stats = collections.Counter()
    item_hist = collections.Counter()

    for t in manifest:
        path = RAW / t["accession_number"] / t["filename"]
        if not path.exists() or path.stat().st_size == 0:
            stats["missing"] += 1
            continue
        raw = path.read_bytes()
        acc = t["accession_number"]
        f = filings.get(acc)
        if not f:
            stats["no_filing"] += 1
            continue

        if t["kind"] == "TENK":
            stats["tenk_docs"] += 1
            items = split_items(html_to_text(raw))
            for code, body in items.items():
                item_hist[code] += 1
                sections.append({
                    "accession_number": acc, "item_code": code,
                    "item_title": WANTED[code], "section_text": body,
                    "char_len": len(body), "company_cik": f["company_cik"],
                    "filing_date": f["filing_date"],
                })
            if items:
                stats["tenk_with_sections"] += 1
            a = extract_auditor(raw)
            if a.get("auditor_name"):
                fy = None
                if f["period_of_report"]:
                    fy = int(str(f["period_of_report"])[:4])
                pid = a.get("pcaob_firm_id", "")
                auditors.append({
                    "accession_number": acc, "company_cik": f["company_cik"],
                    "auditor_name": a["auditor_name"],
                    "auditor_location": a.get("auditor_location"),
                    "pcaob_firm_id": int(pid) if str(pid).strip().isdigit() else None,
                    "fiscal_year": fy,
                })
                stats["auditors"] += 1
        else:
            stats["ex21_docs"] += 1
            found = parse_exhibit21(raw)
            for s in found:
                subs.append({
                    "accession_number": acc, "parent_cik": f["company_cik"],
                    "subsidiary_name": s["subsidiary_name"],
                    "name_normalized": s["name_normalized"],
                    "jurisdiction": s["jurisdiction"],
                })
            if found:
                stats["ex21_with_subs"] += 1

    # global dedupe on the table PKs
    def dedupe(rows, keyfn):
        seen, out = set(), []
        for r in rows:
            k = keyfn(r)
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
        return out

    sections = dedupe(sections, lambda r: (r["accession_number"], r["item_code"]))
    subs     = dedupe(subs,     lambda r: (r["accession_number"], r["name_normalized"]))
    auditors = dedupe(auditors, lambda r: r["accession_number"])

    print(f"\n  {dict(stats)}")
    print(f"\n  sections    : {len(sections):,}")
    for c, n in sorted(item_hist.items()):
        avg = sum(s['char_len'] for s in sections if s['item_code'] == c) / max(n, 1)
        print(f"      Item {c:<3s} {n:>4} filings   avg {avg:>9,.0f} chars")
    print(f"  subsidiaries: {len(subs):,} across {len({s['accession_number'] for s in subs})} exhibits")
    print(f"  auditors    : {len(auditors):,}")
    if auditors:
        top = collections.Counter(a["auditor_name"] for a in auditors).most_common(6)
        for n, c in top:
            print(f"      {n[:46]:46s} {c}")

    write_jsonl("sections.jsonl", sections)
    write_jsonl("subsidiaries.jsonl", subs)
    write_jsonl("auditors.jsonl", auditors)


if __name__ == "__main__":
    main()
