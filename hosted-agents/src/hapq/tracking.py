"""Record what a creation step really created, including after a failure or a timeout.

- The "before" snapshot of a deployment is persisted (results/pending/<deployment>.json) before ARM is called
  and reused on retry, so objects created by a failed or timed-out attempt are never reclassified as
  pre-existing by a later attempt. The baseline is removed only after a completed, recorded deployment.
- Snapshots include objects `az resource list` does not show (budgets, role assignments under the RG).
- A read error raises: an unreadable state is never treated as "nothing exists".
- Before deploying, a what-if (read-only) refuses any change to an existing object the kit does not own.
"""
from __future__ import annotations

import json
from pathlib import Path

from .azcli import Az
from .config import KitConfig
from .ledger import Ledger

SAFE_CHANGES = {"Create", "Ignore"}  # Ignore = exists in the RG but untouched by the template


class SnapshotError(RuntimeError):
    pass


class ExistingObjectError(RuntimeError):
    pass


class DeploymentError(RuntimeError):
    def __init__(self, message: str, recorded: dict):
        super().__init__(message)
        self.recorded = recorded


def _ids(res, what: str) -> set[str]:
    if not res.ok:
        raise SnapshotError(f"cannot read {what}: {(res.stderr or '').strip()[-300:]}")
    return {r["id"].lower() for r in (res.json() or [])}


def snapshot(cfg: KitConfig, az: Az) -> set[str]:
    rg_scope = cfg.rg_scope.lower()
    ids = _ids(az.json("resource", "list", "-g", cfg.resource_group, "--query", "[].{id:id}"), "resources")
    ids |= _ids(az.json("consumption", "budget", "list", "-g", cfg.resource_group, "--query", "[].{id:id}"), "budgets")
    roles = az.json("role", "assignment", "list", "--all", "--query", "[].{id:id}")
    ids |= {i for i in _ids(roles, "role assignments") if i.startswith(rg_scope)}
    return ids


def record_deployment(cfg: KitConfig, az: Az, ledger: Ledger, environment: str, before: set[str],
                      output_resources: list[str], run_id: str = "") -> dict[str, list[str]]:
    """Ledger the resources a deployment touched: new ones as created, earlier ones as preexisting."""
    after = snapshot(cfg, az)
    outputs = {i.lower() for i in output_resources}
    touched = outputs | (after - before)
    state = ledger.state()
    created, preexisting = [], []
    for rid in sorted(touched):
        name = rid.rstrip("/").split("/")[-1]
        known = state.get(("arm", rid))
        if rid in before:
            if not known:
                ledger.preexisting("arm", rid, name, environment, run_id=run_id)
            preexisting.append(rid)
        elif rid in after or rid in outputs:
            if not known or known.event != "created":
                ledger.created("arm", rid, name, environment, run_id=run_id)
            created.append(rid)
    return {"created": created, "preexisting": preexisting}


def _baseline_path(cfg: KitConfig, name: str) -> Path:
    return cfg.results_dir / "pending" / f"{name}.json"


def baseline(cfg: KitConfig, az: Az, name: str) -> set[str]:
    """Persisted pre-deployment snapshot; reused if a previous attempt did not complete."""
    p = _baseline_path(cfg, name)
    if p.exists():
        return set(json.loads(p.read_text(encoding="utf-8")))
    ids = snapshot(cfg, az)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(ids)), encoding="utf-8")
    return ids


def _param_args(params: dict) -> list[str]:
    return [f"{k}={json.dumps(v) if isinstance(v, (list, dict, bool, int)) else v}" for k, v in params.items()]


def whatif_guard(cfg: KitConfig, az: Az, ledger: Ledger, template: Path, params: dict) -> list[dict]:
    """Refuse a deployment that would modify an existing object not created by the kit (read-only check)."""
    res = az.json("deployment", "group", "what-if", "-g", cfg.resource_group, "-f", str(template),
                  "--result-format", "ResourceIdOnly", "--no-pretty-print", "--parameters", *_param_args(params),
                  timeout=900)
    if not res.ok:
        raise SnapshotError("what-if failed: " + (res.stderr or "")[-400:])
    owned = {e.id.lower() for e in ledger.owned() if e.kind == "arm"}
    changes = [{"id": c["resourceId"], "change": c["changeType"]} for c in (res.json() or {}).get("changes", [])]
    # An id that still contains template expressions cannot designate an existing object (names computed at
    # deployment, e.g. a DNS zone named after a new environment's domain); the post-deployment diff classifies it.
    unresolved = [c for c in changes if c["change"] == "Unsupported" and any(ch in c["id"] for ch in "[]()'")]
    def is_owned(rid: str) -> bool:  # a child of a kit-owned resource is owned too (DNS links, zone groups...)
        rid = rid.lower()
        return rid in owned or any(rid.startswith(o + "/") for o in owned)
    foreign = [c for c in changes if c["change"] not in SAFE_CHANGES and not is_owned(c["id"]) and c not in unresolved]
    if foreign:
        raise ExistingObjectError("deployment would touch objects the kit did not create: " +
                                  "; ".join(f"{c['change']} {c['id'].split('/')[-1]}" for c in foreign))
    return changes


UNTAGGABLE_TYPES = ("/providers/microsoft.authorization/roleassignments/",)


def recover_pending(cfg: KitConfig, az: Az, ledger: Ledger, environment: str, name: str, run_id: str) -> list[str]:
    """Interrupted earlier attempt: ledger the kit-tagged objects that appeared since the persisted baseline.

    Only objects absent from the baseline AND carrying the kit tag (or untaggable role assignments under the RG)
    are adopted; anything else stays foreign and the what-if guard keeps refusing to touch it.
    """
    p = _baseline_path(cfg, name)
    if not p.exists():
        return []
    before = set(json.loads(p.read_text(encoding="utf-8")))
    res = az.json("resource", "list", "-g", cfg.resource_group, "--query", "[].{id:id,tags:tags}")
    if not res.ok:
        raise SnapshotError("cannot read resources for recovery: " + (res.stderr or "")[-300:])
    tags = {r["id"].lower(): (r.get("tags") or {}) for r in res.json() or []}
    now = snapshot(cfg, az)
    state, adopted = ledger.state(), []
    for rid in sorted(now - before):
        kit = tags.get(rid, {}).get(cfg.kit_tag_key) == cfg.kit_tag_value or any(t in rid for t in UNTAGGABLE_TYPES)
        if kit and not (state.get(("arm", rid)) and state[("arm", rid)].event == "created"):
            ledger.created("arm", rid, rid.rstrip("/").split("/")[-1], environment, run_id=run_id, recovered=True)
            adopted.append(rid)
    return adopted


def tracked_deploy(cfg: KitConfig, az: Az, ledger: Ledger, environment: str, name: str, template: Path,
                   params: dict, run_id: str) -> dict:
    """recovery -> what-if guard -> persisted baseline -> deploy -> ledger (also on failure/timeout) -> clear baseline."""
    recover_pending(cfg, az, ledger, environment, name, run_id)
    whatif_guard(cfg, az, ledger, template, params)
    before = baseline(cfg, az, name)
    res = az.json("deployment", "group", "create", "-g", cfg.resource_group, "-n", name, "-f", str(template),
                  "--parameters", *_param_args(params), timeout=cfg.limits.deploy_timeout_s * 3)
    outputs, resources = {}, []
    if res.ok:
        props = res.json()["properties"]
        outputs = {k: v["value"] for k, v in (props.get("outputs") or {}).items()}
        resources = [r["id"] for r in props.get("outputResources") or []]
    else:  # partial deployment: ask ARM what it already did
        show = az.json("deployment", "group", "show", "-g", cfg.resource_group, "-n", name)
        if show.ok:
            resources = [r["id"] for r in (show.json()["properties"].get("outputResources") or [])]
    recorded = record_deployment(cfg, az, ledger, environment, before, resources, run_id)
    if not res.ok:
        raise DeploymentError(f"deployment {name} failed or timed out (created objects are ledgered; baseline kept "
                              f"for retry): " + (res.stderr or "")[-500:], recorded)
    _baseline_path(cfg, name).unlink(missing_ok=True)
    return {"outputs": outputs, "recorded": recorded}
