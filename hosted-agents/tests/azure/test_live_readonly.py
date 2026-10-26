"""Live, read-only checks (opt-in: `pytest -m azure` / `pytest -m docs`).

These tests assert on observable results from the real subscription or the public Learn pages and
record them as evidence. They never create, modify or delete Azure resources.
"""
import pytest

from hapq import api
from hapq.results import Evidence, Status


@pytest.fixture(scope="module")
def preflight(real_cfg):
    results, run_dir = api.run_preflight(real_cfg, record=True)
    return {r.check_id: r for r in results}, run_dir


@pytest.mark.azure
def test_identity_and_subscription(preflight):
    by_id, _ = preflight
    assert by_id["PF-AUTH"].status is Status.PASS, by_id["PF-AUTH"].observed
    assert by_id["PF-SUB"].status is Status.PASS


@pytest.mark.azure
def test_resource_group_is_the_kit_one(preflight, real_cfg):
    rg = preflight[0]["PF-RG"]
    assert rg.status is Status.PASS, rg.observed
    # every resource in the RG must carry an explicit location (RG region is metadata only)
    assert all(r["location"] for r in rg.details["resources"])


@pytest.mark.azure
def test_lab_region_supports_model_and_quota(preflight, real_cfg):
    by_id, _ = preflight
    for cid in ("PF-MODEL-AVAILABLE", "PF-MODEL-QUOTA", "PF-ARM-LOCATIONS", "PF-POLICIES", "PF-PROVIDERS"):
        assert by_id[cid].status is Status.PASS, f"{cid}: {by_id[cid].observed}"
        assert by_id[cid].evidence is Evidence.OBSERVED


@pytest.mark.azure
def test_required_region_is_not_claimed_from_arm_metadata(preflight):
    assert preflight[0]["PF-ARM-LOCATIONS-REQUIRED"].status is Status.NOT_TESTED


@pytest.mark.azure
def test_results_recorded_without_subscription_id(preflight, real_cfg):
    _, run_dir = preflight
    text = (run_dir / "results.jsonl").read_text(encoding="utf-8") + (run_dir / "context.json").read_text(encoding="utf-8")
    assert real_cfg.subscription_id not in text


@pytest.fixture(scope="module")
def doc_results(real_cfg):
    results, _ = api.run_docs(real_cfg, record=True)
    return {r.check_id: r for r in results}


@pytest.mark.docs
def test_all_documentary_sources_readable(doc_results):
    blocked = [k for k, r in doc_results.items() if r.status is Status.BLOCKED]
    assert not blocked, blocked


@pytest.mark.docs
def test_region_claims_are_definitive(doc_results):
    for cid in ("DOC-C05-WESTEUROPE", "DOC-C05-NORTHEUROPE", "DOC-C05-EASTUS2", "DOC-C05-WESTUS"):
        assert doc_results[cid].status in (Status.PASS, Status.FAIL)
        assert doc_results[cid].evidence is Evidence.DOCUMENTED
    assert doc_results["DOC-C05-WESTEUROPE"].status is Status.PASS


@pytest.mark.docs
def test_contradictions_are_reported_not_resolved(doc_results):
    for r in doc_results.values():
        if r.details["polarity"] == "contradiction":
            assert r.status in (Status.NOT_TESTED, Status.PASS)  # never FAIL/PASS-as-support by inference
