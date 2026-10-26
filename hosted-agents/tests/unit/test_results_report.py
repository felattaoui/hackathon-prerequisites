import json
from datetime import datetime, timedelta, timezone

import pytest

from hapq import report
from hapq.config import load_toml
from hapq.results import CheckResult, Evidence, ExecutionContext, RunRecorder, Status

from ..conftest import FAKE_SUB


def _result(check_id="PF-RG", status=Status.PASS, observed=f"/subscriptions/{FAKE_SUB}", environment="subscription",
            evidence=Evidence.OBSERVED):
    return CheckResult(check_id, "C00", "t", status, evidence, "e", observed, region="westeurope",
                       environment=environment)


def _write(cfg, results, runner="real", when=None, point=None):
    ctx = ExecutionContext.capture(cfg, runner=runner)
    if point:
        ctx.execution_point = point
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S%fZ")
    rec = RunRecorder(cfg.results_dir, ctx, "unit", run_id=f"{stamp}-{len(results)}{point or ''}")
    rec.extend(results)
    path = rec.write()
    if when:
        lines = [json.loads(l) for l in (path / "results.jsonl").read_text().splitlines()]
        for l in lines:
            l["timestamp"] = when.isoformat(timespec="seconds")
        (path / "results.jsonl").write_text("\n".join(json.dumps(l) for l in lines))
    return path


def test_status_environment_and_evidence_are_strict():
    with pytest.raises(ValueError):
        CheckResult("X", "C", "t", "OK", Evidence.OBSERVED, "e", "o")
    with pytest.raises(ValueError):
        CheckResult("X", "C", "t", Status.PASS, "mock", "e", "o")
    with pytest.raises(ValueError):
        CheckResult("X", "C", "t", Status.PASS, Evidence.OBSERVED, "e", "o", environment="C")


def test_written_results_are_redacted(cfg):
    path = _write(cfg, [_result()])
    assert FAKE_SUB not in (path / "results.jsonl").read_text() + (path / "context.json").read_text()


def test_fake_runner_cannot_write_into_evidence_dir(cfg):
    with pytest.raises(PermissionError):
        RunRecorder(cfg.results_dir, ExecutionContext.capture(cfg, runner="fake"), "unit", protected_dir=cfg.results_dir)


def test_report_ignores_fake_runs_and_tracks_supersession(cfg):
    _write(cfg, [_result("PF-AUTH")], runner="fake")
    _write(cfg, [_result("PF-RG", Status.FAIL)], when=datetime.now(timezone.utc) - timedelta(days=30))
    _write(cfg, [_result("PF-RG", Status.PASS)])
    latest = report.latest_results(cfg)
    assert not any(k[0] == "PF-AUTH" for k in latest)
    rg = latest[("PF-RG", "westeurope", "subscription", "workstation-public")]
    assert rg.status == "PASS" and rg.superseded == 1 and rg.stale == []


def test_old_result_is_flagged_stale(cfg):
    _write(cfg, [_result("PF-RG")], when=datetime.now(timezone.utc) - timedelta(days=30))
    assert "older than" in next(iter(report.latest_results(cfg).values())).stale[0]


def test_environments_and_execution_points_are_kept_apart(cfg):
    _write(cfg, [_result("S02-INVOKE", Status.PASS, environment="A")])
    _write(cfg, [_result("S02-INVOKE", Status.FAIL, environment="B")], point="in-vnet-runner")
    latest = report.latest_results(cfg)
    assert latest[("S02-INVOKE", "westeurope", "A", "workstation-public")].status == "PASS"
    assert latest[("S02-INVOKE", "westeurope", "B", "in-vnet-runner")].status == "FAIL"


def test_a_success_never_qualifies_b(cfg):
    for sid in ("S03-A2A-CARD", "S04-A2A-MESSAGE", "S05-A2A-NO-TOKEN-401", "S06-A2A-NO-ROLE-403"):
        _write(cfg, [_result(sid, Status.PASS, environment="A")])
    c03 = next(c for c in report.build_matrix(cfg)[0] if c["id"] == "C03")
    assert c03["verdict_A"] == "QUALIFIED"
    assert c03["verdict_B"] == "NOT DEMONSTRATED"


def test_partial_coverage_caps_the_verdict(cfg):
    _write(cfg, [_result("S01-GA-CREATE", Status.PASS, environment="A")])
    c01 = next(c for c in report.build_matrix(cfg)[0] if c["id"] == "C01")
    assert c01["verdict_A"] == "PARTIAL (see limit)"     # deployment without preview != proof of GA status
    assert "documentary" in c01["coverage_limit"]


def test_documentary_pass_never_enters_a_verdict(cfg):
    _write(cfg, [_result("DOC-C05-WESTEUROPE", Status.PASS, environment="docs", evidence=Evidence.DOCUMENTED)])
    c = next(c for c in report.build_matrix(cfg)[0] if c["id"] == "C05.a")
    assert c["docs"][0].status == "PASS"
    assert c["verdict_A"] == "NOT DEMONSTRATED" and c["verdict_B"] == "NOT DEMONSTRATED"


def test_unimplemented_and_blocked_runs_are_explicit(cfg):
    table, meta = report.build_matrix(cfg)
    cells = {(r.check_id, r.run_label): r for c in table for r in c["cells"]}
    assert cells[("S12-XPROJECT-TOOL", "B@in-vnet-runner")].observed.startswith("not implemented")
    assert cells[("S27-3P-REGISTRY", "B@in-vnet-runner")].status == "BLOCKED"
    assert cells[("S01-GA-CREATE", "A@workstation-public")].observed.startswith("implemented, not run")


def test_catalog_integrity():
    cat = load_toml("criteria.toml")
    scenarios = {s["id"]: s for s in cat["scenario"]}
    linked = {sid for c in cat["criterion"] for sid in c.get("scenarios", [])}
    assert linked == set(scenarios), set(scenarios) ^ linked
    for s in scenarios.values():
        assert set(s.get("implemented", [])) <= set(s.get("runs", [])), s["id"]
        assert s.get("pass_if"), s["id"]
        for run in s["runs"]:
            assert run.split("@")[0] in ("A", "B"), run
    for c in cat["criterion"]:
        assert c["coverage"] in ("full", "partial")
        assert c["coverage"] == "full" or c.get("coverage_limit"), c["id"]


def test_network_criteria_are_never_judged_on_the_public_lab(cfg):
    _write(cfg, [_result("S02-INVOKE", Status.PASS, environment="A")])
    c = next(c for c in report.build_matrix(cfg)[0] if c["id"] == "C02.a")
    assert c["verdict_A"].startswith("n/a (qualified in B only)")
