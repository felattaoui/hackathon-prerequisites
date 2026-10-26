"""Safety of the first Azure trial: partial creations, existing objects, limits. Fakes only (not evidence)."""
import json

import pytest

from hapq.azcli import Az, CmdResult
from hapq.config import Limits
from hapq.ledger import Ledger
from hapq.limits import Guard, LimitExceeded
from hapq.lab import classify
from hapq.results import Status
from hapq import tracking

from ..conftest import FAKE_SUB

RG = f"/subscriptions/{FAKE_SUB}/resourceGroups/rg-hackathon-prerequisites-eaf/providers"
ACC = f"{RG}/Microsoft.CognitiveServices/accounts/hapq-a-lab01"
LOG = f"{RG}/Microsoft.OperationalInsights/workspaces/log-hapq-a-lab01"
OTHER = f"{RG}/Microsoft.Storage/storageAccounts/someoneelse"


class FakeArm:
    """Deployments create resources; can fail midway; what-if reports changes."""

    def __init__(self, existing=(), fail_after=None, whatif=None):
        self.res = set(existing)
        self.fail_after, self.whatif, self.calls = fail_after, whatif or [], []
        self.name = "fake"

    def __call__(self, tool, args, timeout=120):
        self.calls.append(args)
        if args[:2] == ["resource", "list"]:
            return CmdResult(0, json.dumps([{"id": i} for i in sorted(self.res)]), "")
        if args[:3] == ["consumption", "budget", "list"] or args[:3] == ["role", "assignment", "list"]:
            return CmdResult(0, "[]", "")
        if args[:3] == ["deployment", "group", "what-if"]:
            return CmdResult(0, json.dumps({"changes": self.whatif}), "")
        if args[:3] == ["deployment", "group", "create"]:
            planned = [ACC, LOG]
            if self.fail_after is not None:
                self.res.update(planned[: self.fail_after])
                return CmdResult(124, "", "timed out")
            self.res.update(planned)
            return CmdResult(0, json.dumps({"properties": {"outputs": {"x": {"value": 1}},
                                                           "outputResources": [{"id": i} for i in planned]}}), "")
        if args[:3] == ["deployment", "group", "show"]:
            return CmdResult(0, json.dumps({"properties": {"outputResources": [{"id": i} for i in sorted(self.res - {OTHER})]}}), "")
        return CmdResult(1, "", "unexpected")


def test_partial_deployment_is_ledgered_and_not_reclassified_on_retry(cfg, tmp_path):
    lg = Ledger(tmp_path / "l.jsonl")
    fake = FakeArm(existing={OTHER}, fail_after=1)
    az = Az(FAKE_SUB, fake)
    with pytest.raises(tracking.DeploymentError):
        tracking.tracked_deploy(cfg, az, lg, "A", "dep1", tmp_path / "t.bicep", {}, "r1")
    assert {e.name for e in lg.owned()} == {"hapq-a-lab01"}          # created before the failure: ledgered
    assert lg.is_preexisting("arm", OTHER) is False                    # untouched foreign object not claimed
    fake.fail_after = None                                              # retry succeeds
    tracking.tracked_deploy(cfg, az, lg, "A", "dep1", tmp_path / "t.bicep", {}, "r2")
    assert {e.name for e in lg.owned()} == {"hapq-a-lab01", "log-hapq-a-lab01"}
    assert not lg.is_preexisting("arm", ACC)                            # NOT reclassified as pre-existing
    assert not (cfg.results_dir / "pending" / "dep1.json").exists()    # baseline cleared after success


def test_whatif_refuses_changes_to_objects_the_kit_did_not_create(cfg, tmp_path):
    lg = Ledger(tmp_path / "l.jsonl")
    fake = FakeArm(existing={ACC}, whatif=[{"resourceId": ACC, "changeType": "Modify"}])
    with pytest.raises(tracking.ExistingObjectError):
        tracking.tracked_deploy(cfg, Az(FAKE_SUB, fake), lg, "A", "dep", tmp_path / "t.bicep", {}, "r")
    assert not any(a[:3] == ["deployment", "group", "create"] for a in fake.calls)
    lg.created("arm", ACC, "hapq-a-lab01", "A")                        # once kit-owned, updating it is allowed
    tracking.whatif_guard(cfg, Az(FAKE_SUB, fake), lg, tmp_path / "t.bicep", {})


def test_whatif_allows_creation_and_untouched_resources(cfg, tmp_path):
    fake = FakeArm(whatif=[{"resourceId": ACC, "changeType": "Create"}, {"resourceId": OTHER, "changeType": "Ignore"}])
    tracking.whatif_guard(cfg, Az(FAKE_SUB, fake), Ledger(tmp_path / "l.jsonl"), tmp_path / "t.bicep", {})


def test_unknown_usage_is_reported_not_zero():
    g = Guard(Limits(max_polls=2))
    g.add_tokens(None)
    g.add_tokens(40)
    assert g.tokens == 40 and g.unknown_usage_calls == 1
    assert "unknown" in str(g.summary()["tokens"])
    g.poll(); g.poll()
    with pytest.raises(LimitExceeded):
        g.poll()


class _Agents:
    def __init__(self, get_exc):
        self.get_exc, self.created = get_exc, False

    def get(self, agent_name):
        raise self.get_exc

    def create_version_from_code(self, **kw):
        self.created = True
        raise TimeoutError("client-side timeout")


def _runtime(cfg, tmp_path, agents):
    from hapq.lab.profile import LabProfile
    from hapq.lab.scenarios import Runtime
    rt = object.__new__(Runtime)
    rt.cfg, rt.ledger, rt.run_id, rt.guard = cfg, Ledger(tmp_path / "l.jsonl"), "r", Guard(cfg.limits)
    rt.profile = LabProfile("A", "westeurope", "acc", ACC, ["proj1"], ["https://x/api/projects/proj1"], [""],
                            "gpt-5-mini", "", "", "", "", "")
    rt.endpoints = {"proj1": "https://x/api/projects/proj1"}
    rt.projects = {"proj1": type("C", (), {"agents": agents})()}
    return rt


def test_only_explicit_404_means_absent(cfg, tmp_path):
    from azure.core.exceptions import HttpResponseError
    from hapq.lab.scenarios import AGENTS_DIR
    agents = _Agents(HttpResponseError(message="Forbidden"))
    rt = _runtime(cfg, tmp_path, agents)
    with pytest.raises(HttpResponseError):
        rt.deploy("proj1", "hapq-min", AGENTS_DIR / "minimal", {})
    assert not agents.created and rt.ledger.owned() == []


def test_agent_ledgered_before_create_even_if_create_times_out(cfg, tmp_path):
    from azure.core.exceptions import ResourceNotFoundError
    from hapq.lab.scenarios import AGENTS_DIR
    rt = _runtime(cfg, tmp_path, _Agents(ResourceNotFoundError(message="not found")))
    with pytest.raises(TimeoutError):
        rt.deploy("proj1", "hapq-min", AGENTS_DIR / "minimal", {})
    assert [e.name for e in rt.ledger.owned()] == ["hapq-min"]


def test_identity_requires_exact_expected_principal():
    calls = lambda *oids: [{"claims": {"oid": o}} for o in oids]
    assert classify.agent_identity(calls("agent"), "agent")[0] is Status.PASS
    assert classify.agent_identity(calls("someone"), "agent")[0] is Status.FAIL
    assert classify.agent_identity(calls(None), "agent")[0] is Status.FAIL
    assert classify.agent_identity(calls("agent"), None)[0] is Status.NOT_TESTED   # unknown expectation
    assert classify.agent_identity([], "agent")[0] is Status.FAIL


def test_a2a_text_comes_only_from_agent_output():
    echo = {"result": {"task": {"history": [{"role": "ROLE_USER", "parts": [{"text": "NONCE:abc"}]}],
                                "artifacts": [{"parts": [{"text": "hello"}]}]}}}
    assert "abc" not in classify.a2a_agent_text(echo)
    agent = {"result": {"message": {"role": "ROLE_AGENT", "parts": [{"text": "abc hello"}]}}}
    assert "abc" in classify.a2a_agent_text(agent)
    user_msg = {"result": {"message": {"role": "ROLE_USER", "parts": [{"text": "abc"}]}}}
    assert classify.a2a_agent_text(user_msg) == ""


def test_cross_project_layers_are_separate():
    st, _, layers = classify.cross_project_tool("active", [{"claims": {}}], [])
    assert st is Status.PASS and layers["observed"] == "consumed" and layers["matches_documentation"] is False
    refusals = classify.technical_refusals(["GET https://x/api/projects/proj1/toolboxes/hapq-tools/mcp 403 Forbidden",
                                            "model said: 403"], "/toolboxes/hapq-tools/")
    assert len(refusals) == 1                                                       # model text ignored
    st, _, layers = classify.cross_project_tool("active", [], refusals)
    assert st is Status.FAIL and layers["matches_documentation"] is True
    assert classify.cross_project_tool("active", [], [])[0] is Status.NOT_TESTED


def test_traces_must_be_correlated():
    assert classify.trace_correlation(0, 50)[0] is Status.NOT_TESTED
    assert classify.trace_correlation(2, 50)[0] is Status.PASS


def test_424_with_container_crash_is_a_kit_defect_not_a_platform_failure():
    crash = ['{"stream":"stderr","message":"Traceback (most recent call last):"}',
             '{"stream":"stderr","message":"RuntimeError: history_source requires a RawAgent"}']
    st, msg = classify.session_not_ready(crash)
    assert st is Status.NOT_TESTED and msg.startswith("kit defect")
    assert classify.session_not_ready(['{"stream":"status","message":"No logs since last 20 seconds"}'])[0] is Status.FAIL
    assert classify.session_id_from_error("Session 'abc123' did not become ready") == "abc123"


def test_empty_answer_with_exhausted_budget_is_a_kit_limit():
    assert classify.answer_check("", "n", {"output": 256, "reasoning": 256}, 300)[0] is Status.NOT_TESTED
    assert classify.answer_check("", "n", {"output": 10}, 300)[0] is Status.FAIL
    assert classify.answer_check("n hello", "n", None, 300)[0] is Status.PASS
    assert classify.answer_check("hello", "n", {"output": 10}, 300)[0] is Status.FAIL


def test_mcp_scenarios_blocked_without_any_call_when_tool_not_ready(cfg, tmp_path):
    from hapq.azcli import FakeRunner
    from hapq.lab import mcp_step
    from hapq.lab.profile import LabProfile
    from hapq.lab.scenarios import Runtime

    rt = object.__new__(Runtime)
    rt.cfg, rt.ledger, rt.run_id, rt.guard, rt.results, rt.state = cfg, Ledger(tmp_path / "l.jsonl"), "r", Guard(cfg.limits), [], {}
    rt.profile = LabProfile("A", "westeurope", "acc", ACC, ["proj1"], ["https://x/api/projects/proj1"], [""], "gpt-5-mini",
                            "", "", "https://ca-hapq-a-mcp-lab01.env.westeurope.azurecontainerapps.io", "api://x", "x")
    rt.az = Az(FAKE_SUB, FakeRunner([
        (lambda t, a: a[:2] == ["containerapp", "show"], CmdResult(0, json.dumps({"latest": "rev1", "ready": None}), "")),
        (lambda t, a: "revision" in a, CmdResult(0, json.dumps({"health": "Unhealthy", "running": "ActivationFailed"}), ""))]))

    class NoHttp:
        def __getattr__(self, name):
            raise AssertionError("no HTTP call may be made to a tool that is not ready")
    rt.http = NoHttp()
    results = mcp_step.run(rt)
    assert {r.check_id for r in results} == {"S14-MCP-NO-TOKEN-401", "S15-MCP-WRONG-AUDIENCE-401",
                                              "S06-A2A-NO-ROLE-403", "S07-TOOL-AGENT-IDENTITY"}
    assert all(r.status is Status.BLOCKED and "not ready" in r.observed for r in results)


def test_gate_failure_records_only_requested_scenarios(cfg, tmp_path):
    from hapq.azcli import FakeRunner
    from hapq.lab import mcp_step
    from hapq.lab.profile import LabProfile
    from hapq.lab.scenarios import Runtime
    rt = object.__new__(Runtime)
    rt.cfg, rt.ledger, rt.run_id, rt.guard, rt.results = cfg, Ledger(tmp_path / "l.jsonl"), "r", Guard(cfg.limits), []
    rt.state = {"skip": ("S07", "S14", "S15")}
    rt.profile = LabProfile("A", "westeurope", "acc", ACC, ["proj1"], ["https://x/api/projects/proj1"], [""], "gpt-5-mini",
                            "", "", "https://ca-x.env.westeurope.azurecontainerapps.io", "api://x", "x")
    rt.az = Az(FAKE_SUB, FakeRunner([
        (lambda t, a: a[:2] == ["containerapp", "show"], CmdResult(0, json.dumps({"latest": "rev2", "ready": None}), "")),
        (lambda t, a: "revision" in a, CmdResult(0, json.dumps({"health": "Unhealthy", "running": "Failed"}), ""))]))
    assert [r.check_id for r in mcp_step.run(rt)] == ["S06-A2A-NO-ROLE-403"]


def test_whatif_accepts_unresolved_ids_but_not_resolved_foreign_ones(cfg, tmp_path):
    unresolved = "[format('{0}/x', reference('cae', '2024-03-01').defaultDomain)]"
    fake = FakeArm(whatif=[{"resourceId": unresolved, "changeType": "Unsupported"}])
    tracking.whatif_guard(cfg, Az(FAKE_SUB, fake), Ledger(tmp_path / "l.jsonl"), tmp_path / "t.bicep", {})
    fake = FakeArm(whatif=[{"resourceId": OTHER, "changeType": "Unsupported"}])
    with pytest.raises(tracking.ExistingObjectError):
        tracking.whatif_guard(cfg, Az(FAKE_SUB, fake), Ledger(tmp_path / "l2.jsonl"), tmp_path / "t.bicep", {})


def test_recovery_adopts_only_kit_tagged_objects_created_after_the_baseline(cfg, tmp_path):
    import json as _json
    lg = Ledger(tmp_path / "l.jsonl")
    (cfg.results_dir / "pending").mkdir(parents=True, exist_ok=True)
    (cfg.results_dir / "pending" / "dep.json").write_text(_json.dumps([OTHER.lower()]))
    tagged, untagged = ACC, f"{RG}/Microsoft.Storage/storageAccounts/newforeign"

    class Fake(FakeArm):
        def __call__(self, tool, args, timeout=120):
            if args[:2] == ["resource", "list"]:
                return CmdResult(0, _json.dumps([{"id": OTHER, "tags": None}, {"id": tagged, "tags": {"kit": "hapq"}},
                                                 {"id": untagged, "tags": {}}]), "")
            return super().__call__(tool, args, timeout)
    adopted = tracking.recover_pending(cfg, Az(FAKE_SUB, Fake()), lg, "B", "dep", "r")
    assert adopted == [tagged.lower()]
    assert {e.name for e in lg.owned()} == {"hapq-a-lab01"}


def test_code_error_is_a_kit_defect_and_zip_entries_are_regular_files():
    import io, stat, zipfile
    from hapq.lab.scenarios import AGENTS_DIR, zip_dir
    st, msg = classify.version_outcome(["creating", "failed"], "failed", '{"code": "CodeError", "message": "x"}')
    assert st is Status.NOT_TESTED and msg.startswith("kit defect")
    z = zipfile.ZipFile(io.BytesIO(zip_dir(AGENTS_DIR / "minimal")))
    assert all(stat.S_ISREG(i.external_attr >> 16) for i in z.infolist())
