"""Explicit cleanup of the objects this kit created, in dependency order, with final verification.

Rules
- Scope = ledger entries with event "created" (or a previous failed/soft delete). Pre-existing objects are
  never touched, even if tagged. ARM resources must also still carry the kit tag (guard against reuse).
- Dry-run is the default. Execution requires the resource-group name as confirmation.
- Purge of soft-deleted Foundry accounts is irreversible: it requires --purge plus the exact account names.
- A read error stops planning (BLOCKED); it is never interpreted as "nothing to delete".
- Every step has an outcome; any failed step or remaining object makes the overall status FAIL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .azcli import Az
from .config import KitConfig
from .ledger import Entry, Ledger
from .results import CheckResult, Evidence, Status

# Deletion order for ARM types (children before parents, consumers before networks).
ARM_ORDER = [
    "microsoft.authorization/roleassignments",
    "microsoft.consumption/budgets",
    "microsoft.app/containerapps",
    "microsoft.app/jobs",
    "microsoft.app/managedenvironments",
    "microsoft.cognitiveservices/accounts",
    "microsoft.compute/virtualmachines",
    "microsoft.network/networkinterfaces",
    "microsoft.compute/disks",
    "microsoft.network/privateendpoints",
    "microsoft.network/privatednszones/virtualnetworklinks",
    "microsoft.network/privatednszones",
    "microsoft.containerregistry/registries",
    "microsoft.storage/storageaccounts",
    "microsoft.insights/privatelinkscopes",
    "microsoft.insights/components",
    "microsoft.operationalinsights/workspaces",
    "microsoft.managedidentity/userassignedidentities",
    "*",  # any other type
    "microsoft.network/natgateways",
    "microsoft.network/publicipaddresses",
    "microsoft.network/virtualnetworks",
]
UNTAGGABLE = ("microsoft.authorization/roleassignments", "microsoft.consumption/budgets")
API_VERSIONS = {"microsoft.authorization/roleassignments": "2022-04-01", "microsoft.consumption/budgets": "2023-11-01"}


class CleanupReadError(RuntimeError):
    pass


def arm_type(resource_id: str) -> str:
    parts = resource_id.strip("/").split("/")
    i = max(k for k, p in enumerate(parts) if p.lower() == "providers")
    ns, rest = parts[i + 1], parts[i + 2:]
    return "/".join([ns] + rest[0::2]).lower()


def _rank(e: Entry) -> tuple[int, int]:
    """Global order: data plane, role assignments, ARM (by type), Entra SP, Entra app."""
    if e.kind in ("agent", "toolbox", "connection"):
        return (0, ("agent", "toolbox", "connection").index(e.kind))
    if e.kind == "role":
        return (1, 0)
    if e.kind == "resource_group":
        return (4, 0)
    if e.kind == "arm":
        t = arm_type(e.id)
        idx = ARM_ORDER.index(t) if t in ARM_ORDER else ARM_ORDER.index("*")
        return (2, idx)
    return (3, 0 if e.kind == "entra_sp" else 1)


@dataclass
class Step:
    entry: Entry
    action: str  # delete | cascade | skip-preexisting | skip-untagged | blocked
    reason: str = ""
    outcome: str = "planned"  # planned | deleted | soft-deleted | purged | already-absent | failed | skipped | blocked
    error: str = ""
    parent: str = ""  # for cascade steps: lowercase id of the parent being deleted


@dataclass
class CleanupPlan:
    steps: list[Step]
    environment: str | None

    @property
    def deletions(self) -> list[Step]:
        return [s for s in self.steps if s.action == "delete"]


@dataclass
class CleanupReport:
    plan: CleanupPlan
    remaining: list[dict] = field(default_factory=list)
    unknown: list[dict] = field(default_factory=list)
    soft_deleted: list[str] = field(default_factory=list)

    @property
    def status(self) -> Status:
        if any(s.outcome == "failed" for s in self.plan.steps) or self.remaining:
            return Status.FAIL
        if self.unknown or any(s.outcome == "blocked" for s in self.plan.steps):
            return Status.BLOCKED
        if self.soft_deleted:
            return Status.FAIL
        return Status.PASS


def plan(cfg: KitConfig, az: Az, ledger: Ledger, environment: str | None = None) -> CleanupPlan:
    owned = ledger.owned(environment)
    arm_ids = [e.id for e in owned if e.kind == "arm"]
    tags: dict[str, dict] = {}
    if arm_ids:
        res = az.json("resource", "list", "-g", cfg.resource_group, "--query", "[].{id:id,tags:tags}")
        if not res.ok:
            raise CleanupReadError("cannot list resources: " + (res.stderr or "").strip()[-300:])
        tags = {r["id"].lower(): (r.get("tags") or {}) for r in res.json() or []}
    owned_arm_lower = {i.lower() for i in arm_ids}
    steps = []
    for e in sorted(owned, key=_rank):
        if e.kind == "arm":
            rid = e.id.lower()
            parent = next((p for p in owned_arm_lower if rid != p and rid.startswith(p + "/")), None)
            if parent:
                steps.append(Step(e, "cascade", f"removed with parent {parent.split('/')[-1]}", parent=parent))
                continue
            t = arm_type(e.id)
            if rid in tags and t not in UNTAGGABLE and tags[rid].get(cfg.kit_tag_key) != cfg.kit_tag_value:
                steps.append(Step(e, "skip-untagged", "kit tag missing: not deleted (manual review)"))
                continue
        if e.kind in ("agent", "toolbox", "connection"):
            account = e.extra.get("account_id", "").lower()
            if account and account in owned_arm_lower:
                steps.append(Step(e, "cascade", "removed with its Foundry account", parent=account))
                continue
        steps.append(Step(e, "delete"))
    return CleanupPlan(steps, environment)


def _absent(res) -> bool:
    text = (res.stderr or "").lower()
    return any(k in text for k in ("not found", "notfound", "does not exist", "resourcenotfound", "request_resourcenotfound", "(404)"))


def _delete_arm(az: Az, e: Entry):
    t = arm_type(e.id)
    if t in API_VERSIONS:
        return az.json("rest", "--method", "DELETE", "--url",
                       f"https://management.azure.com{e.id}?api-version={API_VERSIONS[t]}", scoped=False, timeout=600)
    return az.json("resource", "delete", "--ids", e.id, scoped=False, timeout=3600)


def _entra_has_marker(az: Az, e: Entry) -> bool:
    path = "applications" if e.kind == "entra_app" else "servicePrincipals"
    res = az.json("rest", "--method", "GET", "--url", f"https://graph.microsoft.com/v1.0/{path}/{e.id}?$select=tags",
                  scoped=False)
    return res.ok and "hapq-kit" in (res.json() or {}).get("tags", [])


DataPlaneDeleter = Callable[[Entry], tuple[bool, str]]


def execute(cfg: KitConfig, az: Az, ledger: Ledger, p: CleanupPlan, confirm: str, purge_accounts: set[str] | None = None,
            data_plane: DataPlaneDeleter | None = None, include_rg: bool = False) -> CleanupReport:
    if confirm != cfg.resource_group:
        raise PermissionError("confirmation does not match the resource-group name; nothing deleted")
    purge_accounts = {a.lower() for a in (purge_accounts or set())}
    for s in p.steps:
        e = s.entry
        if s.action != "delete":
            s.outcome = "skipped" if s.action != "cascade" else "planned"
            continue
        if e.kind == "resource_group":
            if not include_rg:
                s.outcome, s.reason = "kept", "kept (use --include-resource-group to delete it when empty)"
                continue
            left = az.json("resource", "list", "-g", cfg.resource_group, "--query", "[].id")
            if not left.ok:
                s.outcome, s.error = "failed", "cannot list resources: " + (left.stderr or "")[-200:]
            elif left.json():
                s.outcome, s.error = "failed", f"resource group not empty ({len(left.json())} resource(s)); not deleted"
            else:
                res = az.json("group", "delete", "-n", cfg.resource_group, "--yes", timeout=3600)
                s.outcome = "deleted" if res.ok else ("already-absent" if _absent(res) else "failed")
                s.error = "" if res.ok or _absent(res) else (res.stderr or "")[-300:]
            if s.outcome in ("deleted", "already-absent"):
                ledger.event(e, "deleted")
            elif s.outcome == "failed":
                ledger.event(e, "delete_failed", error=s.error)
            continue
        if e.kind in ("agent", "toolbox", "connection"):
            if data_plane is None:
                s.outcome, s.error = "blocked", "data-plane access required (run from the lab execution point)"
                continue
            ok, err = data_plane(e)
            s.outcome, s.error = ("deleted", "") if ok else ("failed", err)
        elif e.kind == "role":
            res = az.json("rest", "--method", "DELETE", "--url",
                          f"https://management.azure.com{e.id}?api-version=2022-04-01", scoped=False)
            s.outcome = "deleted" if res.ok else ("already-absent" if _absent(res) else "failed")
            s.error = "" if res.ok or _absent(res) else (res.stderr or "")[-300:]
        elif e.kind in ("entra_app", "entra_sp"):
            if not _entra_has_marker(az, e):
                show = az.json("rest", "--method", "GET", "--url",
                               f"https://graph.microsoft.com/v1.0/{'applications' if e.kind == 'entra_app' else 'servicePrincipals'}/{e.id}",
                               scoped=False)
                if _absent(show):
                    s.outcome = "already-absent"
                else:
                    s.outcome, s.error = "failed", "kit marker tag not found on the Entra object: not deleted"
                continue
            path = "applications" if e.kind == "entra_app" else "servicePrincipals"
            res = az.json("rest", "--method", "DELETE", "--url", f"https://graph.microsoft.com/v1.0/{path}/{e.id}", scoped=False)
            s.outcome = "deleted" if res.ok else ("already-absent" if _absent(res) else "failed")
            s.error = "" if res.ok or _absent(res) else (res.stderr or "")[-300:]
        else:  # arm
            res = _delete_arm(az, e)
            if res.ok:
                s.outcome = "deleted"
            elif _absent(res):
                s.outcome = "already-absent"
            else:
                s.outcome, s.error = "failed", (res.stderr or "")[-300:]
            if s.outcome in ("deleted", "already-absent") and arm_type(e.id) == "microsoft.cognitiveservices/accounts":
                if e.name.lower() in purge_accounts:
                    pr = az.json("cognitiveservices", "account", "purge", "-g", cfg.resource_group, "-n", e.name,
                                 "-l", e.extra.get("location", cfg.lab_region), timeout=1800)
                    if pr.ok or _absent(pr):
                        s.outcome = "purged"
                    else:
                        s.outcome, s.error = "failed", "purge failed: " + (pr.stderr or "")[-300:]
                else:
                    s.outcome = "soft-deleted"
        if s.outcome in ("deleted", "already-absent", "purged"):
            ledger.event(e, "purged" if s.outcome == "purged" else "deleted")
        elif s.outcome == "soft-deleted":
            ledger.event(e, "deleted", soft_deleted=True)
        elif s.outcome == "failed":
            ledger.event(e, "delete_failed", error=s.error)
    removed = {s.entry.id.lower() for s in p.steps if s.outcome in ("deleted", "already-absent", "purged", "soft-deleted")}
    for s in p.steps:
        if s.action == "cascade":
            if s.parent in removed:
                s.outcome = "removed-with-parent"
                ledger.event(s.entry, "deleted", cascade=True)
            else:
                s.outcome, s.error = "failed", "parent not deleted"
    return verify(cfg, az, ledger, p)


def verify(cfg: KitConfig, az: Az, ledger: Ledger, p: CleanupPlan) -> CleanupReport:
    """Final check: every planned object must be absent; soft-deleted kit accounts are reported."""
    report = CleanupReport(p)
    for s in p.steps:
        e = s.entry
        if s.action in ("skip-preexisting", "skip-untagged"):
            report.remaining.append({"kind": e.kind, "name": e.name, "reason": s.reason})
            continue
        if e.kind == "resource_group":
            if s.outcome in ("deleted", "already-absent"):
                gone = az.json("group", "exists", "-n", cfg.resource_group)
                if gone.ok and gone.json() is True:
                    report.remaining.append({"kind": e.kind, "name": e.name})
            continue  # "kept" is an explicit choice, not a leftover
        if e.kind == "arm":
            t = arm_type(e.id)
            res = (az.json("rest", "--method", "GET", "--url", f"https://management.azure.com{e.id}?api-version={API_VERSIONS[t]}", scoped=False)
                   if t in API_VERSIONS else az.json("resource", "show", "--ids", e.id, scoped=False))
        elif e.kind == "role":
            res = az.json("rest", "--method", "GET", "--url", f"https://management.azure.com{e.id}?api-version=2022-04-01", scoped=False)
        elif e.kind in ("entra_app", "entra_sp"):
            path = "applications" if e.kind == "entra_app" else "servicePrincipals"
            res = az.json("rest", "--method", "GET", "--url", f"https://graph.microsoft.com/v1.0/{path}/{e.id}", scoped=False)
        else:
            if s.outcome in ("deleted", "removed-with-parent"):
                continue  # verified through the parent account / data-plane delete result
            report.unknown.append({"kind": e.kind, "name": e.name, "reason": "data plane not verified"})
            continue
        if res.ok:
            report.remaining.append({"kind": e.kind, "name": e.name, "type": arm_type(e.id) if e.kind == "arm" else e.kind})
        elif not _absent(res):
            report.unknown.append({"kind": e.kind, "name": e.name, "reason": (res.stderr or "")[-200:]})
    accounts = {s.entry.name.lower() for s in p.steps
                if s.entry.kind == "arm" and arm_type(s.entry.id) == "microsoft.cognitiveservices/accounts"}
    if accounts:
        deleted = az.json("cognitiveservices", "account", "list-deleted", "--query", "[].name")
        if not deleted.ok:
            report.unknown.append({"kind": "arm", "name": "soft-deleted accounts", "reason": "list-deleted unreadable"})
        else:
            report.soft_deleted = sorted(n for n in (deleted.json() or []) if n.lower() in accounts)
    return report


def as_result(report: CleanupReport, environment: str) -> CheckResult:
    counts: dict[str, int] = {}
    for s in report.plan.steps:
        counts[s.outcome] = counts.get(s.outcome, 0) + 1
    observed = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    if report.remaining:
        observed += f"; remaining: {len(report.remaining)}"
    if report.soft_deleted:
        observed += f"; soft-deleted accounts not purged: {', '.join(report.soft_deleted)}"
    if report.unknown:
        observed += f"; unverifiable: {len(report.unknown)}"
    return CheckResult("PF-CLEANUP", "C00", "Kit-created objects removed", report.status, Evidence.OBSERVED,
                       "every ledger object absent, no soft-deleted kit account", observed,
                       details={"steps": [{"kind": s.entry.kind, "name": s.entry.name, "action": s.action,
                                           "outcome": s.outcome, "error": s.error} for s in report.plan.steps],
                                "remaining": report.remaining, "unknown": report.unknown},
                       environment=environment if environment in ("A", "B") else "subscription")
