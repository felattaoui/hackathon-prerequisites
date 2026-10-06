"""Manual verifications (portal interactions that code cannot prove).

The operator performs the steps, then records the outcome. The record carries evidence=manual,
the operator alias and the declared execution point; it is never presented as an automated test.
"""
from __future__ import annotations

from .config import KitConfig, load_toml
from .results import CheckResult, Evidence, ExecutionContext, RunRecorder, Status

STEPS = {
    "MAN-C02-PORTAL-CHAT-INVNET": [
        "From a machine inside the lab VNet (or connected by VPN/ExpressRoute), check that "
        "`nslookup <account>.services.ai.azure.com` returns a private IP of the lab VNet. Record the IP range only.",
        "Open https://ai.azure.com with the New Foundry toggle on, open the lab project.",
        "Open the model playground for the deployed model and send a synthetic prompt (e.g. 'Say OK').",
        "Record PASS if a model answer is shown, FAIL with the exact error text otherwise.",
    ],
    "MAN-C02-PORTAL-CHAT-PUBLIC": [
        "From a machine on the public internet (no VPN), confirm `nslookup` returns a public IP.",
        "Open the same project and playground in https://ai.azure.com.",
        "Record PASS if access is refused (copy the error, e.g. 403 'Virtual Network/Firewall rules'), "
        "FAIL if the chat works from the public internet.",
    ],
    "MAN-C06-CONTROL-PLANE": [
        "Open https://ai.azure.com > Operate (Control Plane, preview) for the lab subscription.",
        "List agents; check that hosted agents of proj1 and proj2 appear in one view with their project.",
        "Record PASS if both projects' agents are listed, FAIL otherwise. This is inventory only, not a registry.",
    ],
}


def manual_checks() -> dict[str, dict]:
    scenarios = {s["id"]: s for s in load_toml("criteria.toml").get("scenario", [])}
    return {k: {**scenarios.get(k, {}), "steps": v} for k, v in STEPS.items()}


def record(cfg: KitConfig, check_id: str, environment: str, status: str, observed: str, operator: str) -> str:
    checks = manual_checks()
    if check_id not in checks:
        raise ValueError(f"unknown manual check {check_id}; known: {', '.join(checks)}")
    criterion = next(c["id"] for c in load_toml("criteria.toml")["criterion"] if check_id in c.get("scenarios", []))
    point = next((r.split("@", 1)[1] for r in checks[check_id].get("runs", []) if r.startswith(f"{environment}@")), "-")
    ctx = ExecutionContext.capture(cfg, runner="real", tools={"operator": operator})
    if point != "-":  # the matrix keys results by environment@execution point (e.g. B@browser-inside-vnet)
        import dataclasses
        ctx = dataclasses.replace(ctx, execution_point=point)
    rec = RunRecorder(cfg.results_dir, ctx, suite="manual")
    rec.add(CheckResult(check_id, criterion, check_id, Status(status), Evidence.MANUAL,
                        checks[check_id].get("pass_if", ""), observed, region=cfg.lab_region, environment=environment,
                        details={"operator": operator, "steps": checks[check_id]["steps"]}))
    rec.write()
    return rec.run_id
