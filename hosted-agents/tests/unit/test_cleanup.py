"""Ledger-based cleanup: only kit-created objects, dependency order, explicit purge, honest failure statuses.

Uses a stateful fake of Azure (not evidence).
"""
import json

import pytest

from hapq import cleanup
from hapq.azcli import Az, CmdResult
from hapq.ledger import Ledger
from hapq.results import Status

from ..conftest import FAKE_SUB

RG = f"/subscriptions/{FAKE_SUB}/resourceGroups/rg-hackathon-prerequisites-eaf/providers"
ACC = f"{RG}/Microsoft.CognitiveServices/accounts/hapq-a-lab01"
APP = f"{RG}/Microsoft.App/containerApps/ca-mcp"
ENV = f"{RG}/Microsoft.App/managedEnvironments/cae"
VNET = f"{RG}/Microsoft.Network/virtualNetworks/vnet"
ROLE = f"{RG}/Microsoft.ContainerRegistry/registries/cr/providers/Microsoft.Authorization/roleAssignments/r1"
FOREIGN = f"{RG}/Microsoft.Storage/storageAccounts/foreign"
TAG = {"kit": "hapq"}


class FakeAzure:
    def __init__(self, resources, fail_delete=(), entra=None, unreadable=False):
        self.res = dict(resources)          # id -> tags
        self.fail_delete = set(fail_delete)
        self.entra = dict(entra or {})      # object id -> tags
        self.soft_deleted, self.purged, self.calls = set(), [], []
        self.unreadable = unreadable
        self.name = "fake"

    def __call__(self, tool, args, timeout=120):
        self.calls.append(args)
        a = " ".join(args)
        if args[:2] == ["resource", "list"]:
            if self.unreadable:
                return CmdResult(1, "", "AuthorizationFailed")
            return CmdResult(0, json.dumps([{"id": i, "tags": t} for i, t in self.res.items()]), "")
        if args[:2] == ["resource", "delete"]:
            rid = args[args.index("--ids") + 1]
            if rid in self.fail_delete:
                return CmdResult(1, "", "Conflict: in use")
            if self.res.pop(rid, None) is None:
                return CmdResult(1, "", "ResourceNotFound")
            if "/accounts/" in rid:
                self.soft_deleted.add(rid.split("/")[-1])
            return CmdResult(0, "", "")
        if args[:2] == ["resource", "show"]:
            rid = args[args.index("--ids") + 1]
            return CmdResult(0, "{}", "") if rid in self.res else CmdResult(1, "", "ResourceNotFound")
        if "cognitiveservices" in args and "purge" in args:
            name = args[args.index("-n") + 1]
            self.soft_deleted.discard(name)
            self.purged.append(name)
            return CmdResult(0, "", "")
        if "list-deleted" in args:
            return CmdResult(0, json.dumps(sorted(self.soft_deleted)), "")
        if args[0] == "rest":
            method, url = args[args.index("--method") + 1], args[args.index("--url") + 1]
            if "graph.microsoft.com" in url:
                oid = url.split("/")[-1].split("?")[0]
                if oid not in self.entra:
                    return CmdResult(1, "", "Request_ResourceNotFound")
                if method == "DELETE":
                    self.entra.pop(oid)
                    return CmdResult(0, "", "")
                return CmdResult(0, json.dumps({"tags": self.entra[oid]}), "")
            rid = url.split("https://management.azure.com")[1].split("?")[0]
            if method == "DELETE":
                return CmdResult(0, "", "") if self.res.pop(rid, None) is not None else CmdResult(1, "", "NotFound")
            return CmdResult(0, "{}", "") if rid in self.res else CmdResult(1, "", "NotFound")
        return CmdResult(1, "", f"unexpected {a}")


@pytest.fixture
def ledger(tmp_path):
    lg = Ledger(tmp_path / "ledger.jsonl")
    lg.created("arm", ACC, "hapq-a-lab01", "A", location="westeurope")
    lg.created("arm", APP, "ca-mcp", "A")
    lg.created("arm", ENV, "cae", "A")
    lg.created("arm", VNET, "vnet", "A")
    lg.created("arm", ROLE, "r1", "A")
    lg.created("entra_app", "app-oid", "hapq-mcp-lab01", "A")
    lg.created("agent", "https://x/api/projects/proj1|hapq-min", "hapq-min", "A", account_id=ACC)
    lg.preexisting("arm", FOREIGN, "foreign", "A")
    return lg


def fake_with_all(**kw):
    res = {i: TAG for i in (ACC, APP, ENV, VNET)} | {ROLE: None, FOREIGN: TAG}
    return FakeAzure(res, entra={"app-oid": ["hapq-kit"]}, **kw)


def test_plan_order_and_scope(cfg, ledger):
    p = cleanup.plan(cfg, Az(FAKE_SUB, fake_with_all()), ledger)
    names = [s.entry.name for s in p.steps]
    assert "foreign" not in names                       # pre-existing: never in scope
    assert names.index("hapq-min") < names.index("r1") < names.index("ca-mcp") < names.index("cae") \
        < names.index("hapq-a-lab01") < names.index("vnet") < names.index("hapq-mcp-lab01")
    agent = next(s for s in p.steps if s.entry.name == "hapq-min")
    assert agent.action == "cascade"                    # removed with its kit-owned account


def test_read_error_is_not_nothing(cfg, ledger):
    with pytest.raises(cleanup.CleanupReadError):
        cleanup.plan(cfg, Az(FAKE_SUB, fake_with_all(unreadable=True)), ledger)


def test_confirmation_required(cfg, ledger):
    fake = fake_with_all()
    p = cleanup.plan(cfg, Az(FAKE_SUB, fake), ledger)
    with pytest.raises(PermissionError):
        cleanup.execute(cfg, Az(FAKE_SUB, fake), ledger, p, confirm="nope")
    assert not any("delete" in c for c in fake.calls)


def test_without_purge_account_stays_soft_deleted_and_status_fails(cfg, ledger):
    fake = fake_with_all()
    az = Az(FAKE_SUB, fake)
    report = cleanup.execute(cfg, az, ledger, cleanup.plan(cfg, az, ledger), confirm=cfg.resource_group)
    assert FOREIGN in fake.res                          # untouched
    assert report.soft_deleted == ["hapq-a-lab01"] and report.status is Status.FAIL
    assert fake.purged == []


def test_explicit_purge_completes_cleanup(cfg, ledger):
    fake = fake_with_all()
    az = Az(FAKE_SUB, fake)
    report = cleanup.execute(cfg, az, ledger, cleanup.plan(cfg, az, ledger), confirm=cfg.resource_group,
                             purge_accounts={"hapq-a-lab01"})
    assert fake.purged == ["hapq-a-lab01"]
    assert report.status is Status.PASS, (report.remaining, report.unknown)
    assert ledger.owned() == []


def test_failed_delete_is_reported_and_others_continue(cfg, ledger):
    fake = fake_with_all(fail_delete={ENV})
    az = Az(FAKE_SUB, fake)
    report = cleanup.execute(cfg, az, ledger, cleanup.plan(cfg, az, ledger), confirm=cfg.resource_group,
                             purge_accounts={"hapq-a-lab01"})
    assert report.status is Status.FAIL
    assert ENV in fake.res and VNET not in fake.res
    assert any(e.event == "delete_failed" for e in ledger.entries())
    assert {"kind": "arm", "name": "cae", "type": "microsoft.app/managedenvironments"} in report.remaining


def test_untagged_or_unmarked_objects_are_not_deleted(cfg, ledger):
    fake = fake_with_all()
    fake.res[APP] = {}                                  # tag removed since creation
    fake.entra["app-oid"] = []                          # marker missing
    az = Az(FAKE_SUB, fake)
    report = cleanup.execute(cfg, az, ledger, cleanup.plan(cfg, az, ledger), confirm=cfg.resource_group,
                             purge_accounts={"hapq-a-lab01"})
    assert APP in fake.res and "app-oid" in fake.entra
    assert report.status is Status.FAIL


def test_data_plane_without_access_is_blocked(cfg, tmp_path):
    lg = Ledger(tmp_path / "l.jsonl")
    lg.created("agent", "https://x/api/projects/p|a", "a", "B", account_id="/not/owned")
    p = cleanup.plan(cfg, Az(FAKE_SUB, FakeAzure({})), lg)
    report = cleanup.execute(cfg, Az(FAKE_SUB, FakeAzure({})), lg, p, confirm=cfg.resource_group)
    assert p.steps[0].outcome == "blocked" and report.status is Status.BLOCKED


def test_resource_group_kept_unless_requested_and_empty(cfg, tmp_path):
    lg = Ledger(tmp_path / "l.jsonl")
    lg.created("resource_group", f"/subscriptions/{FAKE_SUB}/resourceGroups/{cfg.resource_group}", cfg.resource_group, "A")
    fake = FakeAzure({FOREIGN: TAG})
    az = Az(FAKE_SUB, fake)
    report = cleanup.execute(cfg, az, lg, cleanup.plan(cfg, az, lg), confirm=cfg.resource_group)
    assert report.plan.steps[0].outcome == "kept" and report.status is Status.PASS
    report = cleanup.execute(cfg, az, lg, cleanup.plan(cfg, az, lg), confirm=cfg.resource_group, include_rg=True)
    assert report.plan.steps[0].outcome == "failed" and "not empty" in report.plan.steps[0].error
    assert not any(c[:2] == ["group", "delete"] for c in fake.calls)
