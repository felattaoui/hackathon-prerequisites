"""Documentary probe logic on synthetic HTML. Not documentary evidence."""
import pytest

from hapq import api
from hapq.checks import docs
from hapq.config import load_toml
from hapq.results import Status

HTML = """<html><body><nav>West Europe</nav>
<h3>Region availability</h3><p>Hosted agents are available in:</p>
<ul><li>West Europe</li><li>West US 3</li><li>East US 2</li></ul>
<p>Note</p><p>This list will be updated as regions become available.</p>
<h2>Other</h2><p>North Europe is mentioned elsewhere.</p></body></html>"""


def page(html=HTML, url="https://example.test"):
    text = docs.html_to_text(html)
    return docs.Page(url, text, None, "x", "2026-01-01T00:00:00+00:00")


def probe(**kw):
    return {"section_start": "Region availability", "section_end": "This list will be updated", **kw}


def test_list_item_is_exact_and_section_bounded():
    p = page()
    assert docs.evaluate_probe(probe(list_item="West Europe"), p)[0] is True
    assert docs.evaluate_probe(probe(list_item="West US"), p)[0] is False      # "West US 3" must not match
    assert docs.evaluate_probe(probe(list_item="North Europe"), p)[0] is False  # outside the section


def test_nav_text_is_ignored():
    assert "West Europe" not in docs.html_to_text("<nav>West Europe</nav><p>x</p>")


def test_missing_section_and_fetch_failure_are_unusable():
    assert docs.evaluate_probe({"section_start": "Nope", "contains": "x"}, page())[0] is None
    failed = docs.Page("u", None, "URLError", None, "t")
    assert docs.evaluate_probe({"contains": "x"}, failed)[0] is None


def test_expect_false_and_normalisation():
    p = page("<p>Cross‑tenant token exchange isn’t supported</p>")
    assert docs.evaluate_probe({"contains": "isn't supported"}, p)[0] is True
    assert docs.evaluate_probe({"contains": "hosted", "expect": False}, p)[0] is True


def test_run_assertions_blocked_when_source_unreadable():
    results = docs.run_assertions(fetch=lambda url: docs.Page(url, None, "offline", None, "t"), criteria={"C05.b"})
    assert results and all(r.status is Status.BLOCKED for r in results)


def test_contradiction_flag_only_when_both_statements_present():
    html = "<p>stays publicly addressable</p><p>These rules apply to all protocols, including REST and WebSocket</p>"
    results = docs.run_assertions(fetch=lambda url: page(html, url), only={"DOC-C02-ENDPOINT-PRIVACY"})
    assert results[0].details["contradiction_present"] is True
    assert results[0].status is Status.NOT_TESTED


def test_catalog_is_consistent_with_criteria():
    criteria_ids = {c["id"] for c in load_toml("criteria.toml")["criterion"]}
    linked = {cid for c in load_toml("criteria.toml")["criterion"] for cid in c.get("docs", [])}
    for a in load_toml("doc_assertions.toml")["doc"]:
        assert a["criterion"] in criteria_ids, a["id"]
        assert a["id"] in linked, f"{a['id']} not linked from criteria.toml"
        assert a["if_met"] in Status.__members__ and a["if_unmet"] in Status.__members__
        assert a["probe"] and all(p["url"].startswith("https://learn.microsoft.com/") for p in a["probe"])


def test_substituted_fetcher_cannot_be_recorded(cfg):
    with pytest.raises(PermissionError):
        api.run_docs(cfg, criteria={"C05.b"}, record=True, fetch=lambda url: page(url=url))
