"""Local tests of lab plumbing (limits, tracking, Entra app creation, deploy gate, packaging). Fakes only."""
import json
import zipfile
import io

import pytest

from hapq.azcli import Az, CmdResult, FakeRunner
from hapq.config import Limits
from hapq.ledger import Ledger
from hapq.limits import Guard, LimitExceeded
from hapq.lab import deploy, entra
from hapq.lab.scenarios import AGENTS_DIR, zip_dir
from hapq.tracking import SnapshotError, record_deployment, snapshot

from ..conftest import FAKE_SUB, err, has, ok


def test_guard_enforces_every_limit():
    g = Guard(Limits(max_agent_versions=1, max_calls=2, max_sessions=1, max_total_tokens=100))
    g.version()
    with pytest.raises(LimitExceeded):
        g.version()
    g.call(); g.add_tokens(150)
    with pytest.raises(LimitExceeded):
        g.call()                                  # token budget exhausted
    g.session("s1"); g.session("s1")             # same session counted once
    with pytest.raises(LimitExceeded):
        g.session("s2")


def test_snapshot_read_error_is_raised(cfg):
    az = Az(FAKE_SUB, FakeRunner([(has("resource", "list"), err("AuthorizationFailed"))]))
    with pytest.raises(SnapshotError):
        snapshot(cfg, az)


def test_record_deployment_separates_created_and_preexisting(cfg, tmp_path):
    rg = cfg.rg_scope
    old, new = f"{rg}/providers/X/y/old", f"{rg}/providers/X/y/new"
    after = FakeRunner([(has("resource", "list"), ok([{"id": old}, {"id": new}])),
                        (has("budget", "list"), ok([])), (has("role", "assignment"), ok([]))])
    lg = Ledger(tmp_path / "l.jsonl")
    out = record_deployment(cfg, Az(FAKE_SUB, after), lg, "A", {old.lower()}, [old, new])
    assert out["created"] == [new.lower()] and out["preexisting"] == [old.lower()]
    assert [e.name for e in lg.owned()] == ["new"]
    assert lg.is_preexisting("arm", old)


def test_existing_entra_app_not_created_by_kit_is_not_adopted(tmp_path):
    az = Az(FAKE_SUB, FakeRunner([(has("GET"), ok({"value": [{"id": "x", "appId": "y", "tags": []}]}))]))
    with pytest.raises(entra.EntraError):
        entra.ensure_mcp_app(az, Ledger(tmp_path / "l.jsonl"), "hapq-mcp-lab01", "A")


def test_entra_app_and_sp_are_ledgered_with_marker(tmp_path):
    runner = FakeRunner([
        (lambda t, a: "GET" in a, ok({"value": []})),
        (lambda t, a: "POST" in a and a[a.index("--url") + 1].endswith("/applications"), ok({"id": "app-oid", "appId": "app-id"})),
        (lambda t, a: "PATCH" in a, ok({})),
        (lambda t, a: "POST" in a and a[a.index("--url") + 1].endswith("/servicePrincipals"), ok({"id": "sp-oid"})),
    ])
    lg = Ledger(tmp_path / "l.jsonl")
    out = entra.ensure_mcp_app(Az(FAKE_SUB, runner), lg, "hapq-mcp-lab01", "A")
    assert out["audience"] == "api://app-id"
    assert {(e.kind, e.id) for e in lg.owned()} == {("entra_app", "app-oid"), ("entra_sp", "sp-oid")}
    body = json.loads(runner.calls[1][1][runner.calls[1][1].index("--body") + 1])
    assert "hapq-kit" in body["tags"]


def test_deploy_requires_confirmation_before_any_call(cfg, tmp_path):
    runner = FakeRunner([])
    with pytest.raises(PermissionError):
        deploy.execute_phase_a(cfg, Az(FAKE_SUB, runner), Ledger(tmp_path / "l.jsonl"), "lab01", [], "wrong", "r")
    assert runner.calls == []


def test_deploy_stops_when_gate_fails(cfg, tmp_path):
    runner = FakeRunner([(has("account", "show"), err("Please run az login"))])
    with pytest.raises(RuntimeError, match="preflight gate"):
        deploy.execute_phase_a(cfg, Az(FAKE_SUB, runner), Ledger(tmp_path / "l.jsonl"), "lab01", [],
                               cfg.resource_group, "r")
    assert not any(a[:2] in (["deployment", "group"], ["rest", "--method"]) for _, a in runner.calls)


def test_plan_lists_every_creating_step(cfg):
    names = [s.name for s in deploy.plan_phase_a(cfg, "lab01", ["ops@example.test"])]
    assert names == ["gate", "entra", "infra", "image", "mcp", "profile"]


def test_agent_zip_is_flat_with_entry_point():
    z = zipfile.ZipFile(io.BytesIO(zip_dir(AGENTS_DIR / "minimal")))
    assert sorted(z.namelist()) == ["main.py", "requirements.txt"]
