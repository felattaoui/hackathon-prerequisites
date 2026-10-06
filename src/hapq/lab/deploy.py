"""Phase A deployment orchestration. Dry-run by default; nothing is created without --execute and confirmation.

Steps (each creation is ledgered as it happens):
 1. gate      read-only preflight must PASS (identity, RG, providers, roles, policies, Entra app creation allowed)
 2. entra     app registration + service principal (MCP audience), tagged and ledgered
 3. infra     Bicep pass 1 (account, projects, model, Log Analytics, App Insights, ACR, ACA env, identity, budget)
 4. image     ACR Tasks build of tools/mcp_whoami (remote build, no local Docker)
 5. mcp       Bicep pass 2 (MCP container app)
 6. profile   write config/lab-a.local.toml for the scenarios
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..azcli import Az
from ..checks import preflight
from ..config import REPO_ROOT, KitConfig
from ..ledger import Ledger
from ..results import Status
from ..tracking import tracked_deploy
from . import entra
from .profile import LabProfile

BICEP_A = REPO_ROOT / "infra" / "phase-a-public" / "main.bicep"
MCP_SRC = REPO_ROOT / "tools" / "mcp_whoami"
GATE_CHECKS = ("PF-AUTH", "PF-SUB", "PF-RG", "PF-PROVIDERS", "PF-POLICIES", "PF-ROLES", "PF-MODEL-QUOTA",
               "PF-ENTRA-APP-CREATE")


@dataclass
class Step:
    name: str
    description: str
    creates: str


def plan_phase_a(cfg: KitConfig, suffix: str, budget_emails: list[str]) -> list[Step]:
    budget = f"budget {len(budget_emails)} recipient(s)" if budget_emails else "no budget (no e-mail given)"
    return [
        Step("gate", "read-only preflight: " + ", ".join(GATE_CHECKS), "nothing"),
        Step("entra", f"app registration 'hapq-mcp-{suffix}' (+ service principal), tag hapq-kit", "2 Entra objects"),
        Step("infra", f"Bicep pass 1 in {cfg.resource_group} / {cfg.lab_region}", "Foundry account hapq-a-" + suffix +
             f", proj1, proj2, {cfg.model.name} {cfg.model.sku} {cfg.model.capacity}K TPM, Log Analytics (1 GB/day cap),"
             f" App Insights, ACR Basic, Container Apps env (Consumption), user-assigned identity, AcrPull, {budget}"),
        Step("image", "az acr build tools/mcp_whoami (ACR Tasks)", "1 image in the lab ACR"),
        Step("mcp", "Bicep pass 2 (deployMcpApp=true)", "Container App (min 0 / max 1 replica, 0.25 vCPU / 0.5 GiB)"),
        Step("profile", "write config/lab-a.local.toml", "local file"),
    ]


def execute_phase_a(cfg: KitConfig, az: Az, ledger: Ledger, suffix: str, budget_emails: list[str], confirm: str,
                    run_id: str, log=print) -> LabProfile:
    if confirm != cfg.resource_group:
        raise PermissionError("confirmation does not match the resource-group name; nothing created")
    gate = {r.check_id: r for r in preflight.run_all(cfg, az) if r.check_id in GATE_CHECKS}
    failed = [f"{k}: {r.status.value} ({r.observed})" for k, r in gate.items() if r.status is not Status.PASS]
    missing = set(GATE_CHECKS) - set(gate)
    if failed or missing:
        raise RuntimeError("preflight gate not passed: " + "; ".join(failed + [f"{m}: not run" for m in missing]))
    log("gate: PASS")

    app = entra.ensure_mcp_app(az, ledger, f"hapq-mcp-{suffix}", "A", run_id)
    log(f"entra: app ready (audience {app['audience'][:12]}…)")

    common = {"location": cfg.lab_region, "suffix": suffix, "modelName": cfg.model.name,
              "modelVersion": cfg.model.version, "modelSku": cfg.model.sku, "modelCapacity": cfg.model.capacity,
              "mcpAudiences": f"{app['audience']},{app['appId']}", "budgetContactEmails": budget_emails,
              "budgetStartDate": date.today().replace(day=1).isoformat()}
    infra = tracked_deploy(cfg, az, ledger, "A", f"hapq-a-{suffix}-infra", BICEP_A,
                           {**common, "deployMcpApp": False}, run_id)
    log(f"infra: {len(infra['recorded']['created'])} created")
    out = infra["outputs"]

    digest = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(MCP_SRC.glob("*")) if p.is_file())).hexdigest()[:12]
    image = f"{out['acrLoginServer']}/mcp-whoami:{digest}"
    build = az.json("acr", "build", "-r", out["acrName"], "-t", f"mcp-whoami:{digest}", str(MCP_SRC), "--no-logs",
                    timeout=1800)
    if not build.ok:
        raise RuntimeError("image build failed: " + (build.stderr or "")[-500:])
    log(f"image: {image.split('/')[-1]}")

    mcp = tracked_deploy(cfg, az, ledger, "A", f"hapq-a-{suffix}-mcp", BICEP_A,
                         {**common, "deployMcpApp": True, "mcpImage": image}, run_id)
    log(f"mcp: {len(mcp['recorded']['created'])} created")
    out = mcp["outputs"]

    profile = LabProfile(
        environment="A", region=out["accountLocation"], account_name=out["accountName"], account_id=out["accountId"],
        project_names=out["projectNames"], project_endpoints=out["projectEndpoints"],
        project_principal_ids=out["projectPrincipalIds"], model_deployment=cfg.model.name,
        app_insights_app_id=out["appInsightsAppId"], app_insights_id=out["appInsightsId"], mcp_url=out["mcpUrl"],
        mcp_audience=app["audience"], mcp_app_id=app["appId"], acr_name=out["acrName"])
    log(f"profile: {profile.write().name}")
    return profile
