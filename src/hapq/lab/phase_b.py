"""Phase B — private test environment: deployment and in-VNet execution helpers.

Deployment reuses the kit's what-if guard and ledger (tracked_deploy). The runner VM has no public IP and is driven
only through `az vm run-command invoke` (no SSH key is kept). The Entra app (MCP audience) of phase A is reused:
no new tenant object is created.
"""
from __future__ import annotations

import base64
import io
import json
import tarfile
from pathlib import Path

from ..azcli import Az
from ..config import REPO_ROOT, KitConfig
from ..ledger import Ledger
from ..tracking import tracked_deploy
from . import entra
from .profile import LabProfile

BICEP_B = REPO_ROOT / "infra" / "phase-b-private" / "main.bicep"
RUNNER_SCRIPT = REPO_ROOT / "tools" / "vnet_runner" / "runner.py"


def ephemeral_ssh_public_key() -> str:
    """The VM API requires a public key; the private key is generated in memory and discarded."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    return key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()


def plan(cfg: KitConfig, suffix: str) -> list[str]:
    return [
        f"infra/phase-b-private pass 1 in {cfg.resource_group}/{cfg.lab_region}: VNet 10.42.0.0/16 (agent /24 delegated, "
        "PE /24, runner /27, ACA /23), NAT gateway + public IP, 9 private DNS zones, Log Analytics + App Insights "
        "(ingestion and query disabled publicly) + AMPLS, Foundry account hapq-b-<suffix> (public access disabled, "
        "network injection), gpt-5-mini DataZoneStandard 10K TPM, proj1 + proj2, storage + ACR Premium (both private), "
        "4 private endpoints, D2as_v6 runner VM (no public IP; B2s capacity-restricted), internal Container Apps environment, MCP identity, 7 role "
        "assignments (see Bicep header)",
        "reuse the phase A Entra app as MCP audience (no new tenant object)",
        "pass 2 (after the runner pushes the MCP image to the private ACR): MCP Container App in the internal environment",
    ]


def deploy(cfg: KitConfig, az: Az, ledger: Ledger, suffix: str, run_id: str, deploy_mcp: bool = False,
           mcp_image: str = "", log=print) -> LabProfile:
    app = entra.ensure_mcp_app(az, ledger, f"hapq-mcp-{suffix}", "A", run_id)  # existing, kit-owned
    # The VM API forbids changing the SSH key of an existing VM: reuse its public key (not a secret) if it exists.
    existing = az.json("vm", "show", "-g", cfg.resource_group, "-n", f"vm-hapq-b-runner-{suffix}",
                       "--query", "osProfile.linuxConfiguration.ssh.publicKeys[0].keyData")
    ssh_key = existing.json() if existing.ok and existing.json() else ephemeral_ssh_public_key()
    params = {"location": cfg.lab_region, "suffix": suffix, "runnerSshPublicKey": ssh_key,
              "modelName": cfg.model.name, "modelVersion": cfg.model.version, "modelSku": cfg.model.sku,
              "modelCapacity": cfg.model.capacity, "mcpAudiences": f"{app['audience']},{app['appId']}",
              "deployMcpApp": deploy_mcp, "mcpImage": mcp_image}
    name = f"hapq-b-{suffix}-{'mcp' if deploy_mcp else 'infra'}"
    out = tracked_deploy(cfg, az, ledger, "B", name, BICEP_B, params, run_id)
    o = out["outputs"]
    log(f"{name}: {len(out['recorded']['created'])} object(s) recorded")
    profile = LabProfile(environment="B", region=o["accountLocation"], account_name=o["accountName"],
                         account_id=o["accountId"], project_names=o["projectNames"], project_endpoints=o["projectEndpoints"],
                         project_principal_ids=o["projectPrincipalIds"], model_deployment=cfg.model.name,
                         app_insights_app_id=o["appInsightsAppId"], app_insights_id=o["appInsightsId"],
                         mcp_url=o.get("mcpUrl", ""), mcp_audience=app["audience"], mcp_app_id=app["appId"],
                         acr_name=o["acrName"])
    profile.write()
    extra = {"storage": o["storageName"], "acr_login_server": o["acrLoginServer"], "runner_vm": o["runnerVmName"],
             "agent_subnet": o["agentSubnetPrefix"], "telemetry_query_access": o["telemetryQueryAccess"]}
    (REPO_ROOT / "config" / "lab-b-extra.local.json").write_text(json.dumps(extra, indent=1), encoding="utf-8")
    return profile


def runner_bundle() -> str:
    """tar.gz (base64) of what the runner needs: its script, agent sources, lock files, MCP tool source."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in ("tools/vnet_runner/runner.py", "agents/minimal", "agents/toolbox", "agents/langgraph",
                    "agents/locks", "agents/outbound", "agents/container", "agents/netdiag", "tools/mcp_whoami", "tools/sample19"):
            p = REPO_ROOT / rel
            if not p.exists():
                continue
            files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts]
            for f in files:
                tar.add(f, arcname=str(f.relative_to(REPO_ROOT)).replace("\\", "/"))
    return base64.b64encode(buf.getvalue()).decode()


def run_on_runner(cfg: KitConfig, az: Az, vm: str, command: str, timeout: int = 3600) -> dict:
    """Run a shell script on the in-VNet VM; returns {ok, stdout, stderr} (output is truncated by Azure at ~4 KB)."""
    res = az.json("vm", "run-command", "invoke", "-g", cfg.resource_group, "-n", vm, "--command-id", "RunShellScript",
                  "--scripts", command, timeout=timeout)
    if not res.ok:
        return {"ok": False, "stdout": "", "stderr": (res.stderr or "")[-1500:]}
    msg = (res.json() or {}).get("value", [{}])[0].get("message", "")
    out = msg.split("[stdout]", 1)[-1].split("[stderr]", 1)
    return {"ok": True, "stdout": out[0].strip(), "stderr": out[1].strip() if len(out) > 1 else ""}
