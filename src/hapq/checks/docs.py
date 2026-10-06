"""Re-executable documentary checks (evidence=documented).

Fetches public Microsoft Learn pages, converts them to text and evaluates the probes declared in
config/doc_assertions.toml. A documentary PASS means "the documentation states this on the fetch
date"; it never demonstrates anything on Azure.
"""
from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable

from ..config import load_toml
from ..results import CheckResult, Evidence, Status

DOC = Evidence.DOCUMENTED
_BLOCK_TAGS = {"p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "br", "pre", "section", "dd", "dt"}
_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "svg"}


class _TextExtractor(HTMLParser):
    """HTML -> text with one line per block element, so list items stay distinct lines."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    lines = (re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in "".join(parser.parts).splitlines())
    return "\n".join(ln for ln in lines if ln)


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("`", "").replace("\\_", "_")
    return re.sub(r"\s+", " ", s).strip().lower()


@dataclass
class Page:
    url: str
    text: str | None
    error: str | None
    sha256: str | None
    fetched_at: str


Fetcher = Callable[[str], Page]


def http_fetch(url: str, timeout: int = 30) -> Page:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    req = urllib.request.Request(url, headers={"User-Agent": "hapq-doc-check/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return Page(url, None, f"{type(exc).__name__}: {exc}"[:200], None, now)
    text = html_to_text(html)
    return Page(url, text, None, hashlib.sha256(text.encode()).hexdigest()[:16], now)


def section(text: str, start: str | None, end: str | None) -> str | None:
    """Text between the first line equal to/starting with `start` and the next `end` marker."""
    if not start:
        return text
    lines = text.splitlines()
    idx = next((i for i, ln in enumerate(lines) if _norm(ln).startswith(_norm(start))), None)
    if idx is None:
        return None
    out = []
    for ln in lines[idx + 1:]:
        if end and _norm(ln).startswith(_norm(end)):
            break
        out.append(ln)
    return "\n".join(out)


def evaluate_probe(probe: dict, page: Page) -> tuple[bool | None, str, str]:
    """Returns (expectation met?, observed, expected). met is None when the page/section is unusable."""
    expect = probe.get("expect", True)
    if page.text is None:
        return None, f"fetch failed: {page.error}", ""
    body = section(page.text, probe.get("section_start"), probe.get("section_end"))
    if body is None:
        return None, f"section '{probe.get('section_start')}' not found", ""
    nbody = _norm(body)
    if "list_item" in probe:
        target = _norm(probe["list_item"])
        found = any(_norm(ln) == target for ln in body.splitlines())
        what = f"list item '{probe['list_item']}'"
    elif "any_of" in probe:
        hits = [p for p in probe["any_of"] if _norm(p) in nbody]
        found = bool(hits)
        what = f"any of {probe['any_of']}" + (f" (found {hits})" if hits else "")
    else:
        found = _norm(probe["contains"]) in nbody
        what = f"'{probe['contains']}'"
    return found == expect, f"{what} {'present' if found else 'absent'}", f"{what} {'present' if expect else 'absent'}"


def run_assertions(fetch: Fetcher = http_fetch, only: set[str] | None = None,
                   criteria: set[str] | None = None) -> list[CheckResult]:
    catalog = load_toml("doc_assertions.toml")["doc"]
    cache: dict[str, Page] = {}
    results = []
    for a in catalog:
        if only and a["id"] not in only:
            continue
        if criteria and a["criterion"] not in criteria:
            continue
        outcomes, notes, expected, pages = [], [], [], []
        for probe in a["probe"]:
            if probe["url"] not in cache:
                cache[probe["url"]] = fetch(probe["url"])
            page = cache[probe["url"]]
            met, why, exp = evaluate_probe(probe, page)
            outcomes.append(met)
            notes.append(why)
            expected.append(exp)
            pages.append({"url": page.url, "sha256": page.sha256, "fetched_at": page.fetched_at})
        if any(o is None for o in outcomes):
            status, observed = Status.BLOCKED, "source unreadable: " + "; ".join(notes)
        else:
            status = Status(a["if_met"] if all(outcomes) else a["if_unmet"])
            observed = "; ".join(notes)
        results.append(CheckResult(
            check_id=a["id"], criterion=a["criterion"], title=a["title"], status=status, evidence=DOC,
            expected=" AND ".join(e for e in expected if e),
            observed=observed, sources=sorted({p["url"] for p in pages}),
            details={"polarity": a["polarity"], "note": a.get("note", ""), "pages": pages,
                     "contradiction_present": a["polarity"] == "contradiction" and all(o is True for o in outcomes)}))
    return results
