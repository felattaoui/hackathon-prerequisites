"""MCP step: agent identity -> MCP tool (C04.b) on the existing lab A. Dry-run unless approved.

Reuses account hapq-a-<suffix>, proj1, gpt-5-mini, Log Analytics/App Insights and the existing ledger.
Keeps hapq-min (reference), hapq-lg and hapq-a2a untouched; uses a distinct test agent `hapq-mcp`.
Steps: Entra app (+SP) -> infra/mcp pass 1 -> ACR Tasks image build -> pass 2 (Container App) -> profile update ->
project connection (AgenticIdentityToken, RemoteTool) + toolbox in proj1 -> test agent -> S14, S15, S07.
"""
from __future__ import annotations

import dataclasses
import hashlib
import secrets

from ..azcli import Az
from ..config import REPO_ROOT, KitConfig
from ..ledger import Ledger
from ..results import CheckResult, Status
from ..tracking import tracked_deploy
from . import classify, entra
from .profile import LabProfile

BICEP_MCP = REPO_ROOT / "infra" / "mcp" / "main.bicep"
MCP_SRC = REPO_ROOT / "tools" / "mcp_whoami"
AGENT_MCP = "hapq-mcp"


def plan(cfg: KitConfig, suffix: str) -> list[str]:
    return [
        f"Entra: app registration 'hapq-mcp-{suffix}' + service principal (tag hapq-kit), audience of the tool",
        f"Azure (infra/mcp, pass 1): user-assigned identity, ACR Basic, AcrPull (identity -> ACR), Container Apps env "
        f"(Consumption, no private endpoint) in {cfg.resource_group}/{cfg.lab_region}; reuses Log Analytics",
        "ACR Tasks: build tools/mcp_whoami (no local Docker)",
        "Azure (infra/mcp, pass 2): Container App 0.25 vCPU / 0.5 GiB, min 0 / max 1 replica, public HTTPS, Entra token check",
        "proj1 (existing): connection 'hapq-mcp' (RemoteTool, AgenticIdentityToken, audience = kit app) + toolbox 'hapq-tools'",
        f"test agent '{AGENT_MCP}' (agents/toolbox + lock, TOOLBOX_NAME=hapq-tools); hapq-min untouched",
        "scenarios: S14 (no token -> 401), S15 (foreign audience -> 401), S06 (tool identity, no Foundry role, card GET -> 403), S07 (principal == agent instance identity)",
    ]


def deploy(cfg: KitConfig, az: Az, ledger: Ledger, suffix: str, run_id: str, log=print) -> LabProfile:
    profile = LabProfile.load("A")
    app = entra.ensure_mcp_app(az, ledger, f"hapq-mcp-{suffix}", "A", run_id)
    common = {"location": cfg.lab_region, "suffix": suffix, "mcpAudiences": f"{app['audience']},{app['appId']}"}
    infra = tracked_deploy(cfg, az, ledger, "A", f"hapq-mcp-{suffix}-infra", BICEP_MCP,
                           {**common, "deployMcpApp": False}, run_id)
    out = infra["outputs"]
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(MCP_SRC.glob("*")) if p.is_file())).hexdigest()[:12]
    build = az.json("acr", "build", "-r", out["acrName"], "-t", f"mcp-whoami:{digest}", str(MCP_SRC), "--no-logs",
                    timeout=1800)
    if not build.ok:
        raise RuntimeError("image build failed: " + (build.stderr or "")[-400:])
    app_out = tracked_deploy(cfg, az, ledger, "A", f"hapq-mcp-{suffix}-app", BICEP_MCP,
                             {**common, "deployMcpApp": True, "mcpImage": f"{out['acrLoginServer']}/mcp-whoami:{digest}"},
                             run_id)["outputs"]
    profile = dataclasses.replace(profile, mcp_url=app_out["mcpUrl"], mcp_audience=app["audience"],
                                  mcp_app_id=app["appId"], acr_name=out["acrName"])
    log(f"profile updated: {profile.write().name}")
    return profile


def tool_ready(rt, cold_start_s: int = 180) -> tuple[bool, str]:
    """Prerequisite: the LATEST revision is not failed, and GET /healthz == 200 (bounded wait, wakes a scaled-to-zero app)."""
    import time
    app = rt.profile.mcp_url.split("//", 1)[-1].split(".", 1)[0]
    show = rt.az.json("containerapp", "show", "-n", app, "-g", rt.cfg.resource_group,
                      "--query", "{latest:properties.latestRevisionName, ready:properties.latestReadyRevisionName}")
    if not show.ok:
        return False, "container app state unreadable: " + (show.stderr or "")[-200:]
    latest = show.json()["latest"]
    deadline, last = time.time() + cold_start_s, "no answer"
    while time.time() < deadline:
        rev = rt.az.json("containerapp", "revision", "show", "-n", app, "-g", rt.cfg.resource_group, "--revision", latest,
                         "--query", "{health:properties.healthState, running:properties.runningState}")
        state = rev.json() if rev.ok else {}
        if state.get("running") in ("ActivationFailed", "Failed") or state.get("health") == "Unhealthy":
            return False, f"latest revision {latest} failed: {state}"
        try:
            code = rt.http.get(f"{rt.profile.mcp_url}/healthz", timeout=20).status_code
            if code == 200 and state.get("health") == "Healthy":
                return True, f"latest revision {latest} {state.get('health')}/{state.get('running')}, /healthz 200"
            last = f"/healthz HTTP {code}, revision {state}"
        except Exception as exc:
            last = f"/healthz {type(exc).__name__}, revision {state}"
        time.sleep(10)
    return False, f"not ready within {cold_start_s}s ({last})"


def run(rt) -> list[CheckResult]:
    from .scenarios import AGENTS_DIR, TOOLBOX

    p, project = rt.profile, rt.profile.project_names[0]
    skip = rt.state.get("skip", ())
    ok, why = tool_ready(rt)
    if not ok:  # no scenario is attempted against a tool that is not ready: no accumulated timeouts
        for sid, crit in (("S14-MCP-NO-TOKEN-401", "C04.b"), ("S15-MCP-WRONG-AUDIENCE-401", "C04.b"),
                          ("S06-A2A-NO-ROLE-403", "C03"), ("S07-TOOL-AGENT-IDENTITY", "C04.b")):
            if sid.split("-")[0] not in skip:  # only scenarios that were requested
                rt.record(sid, crit, Status.BLOCKED, f"MCP tool not ready: {why}")
        return rt.results
    rt.state["tool_ready"] = why

    def s14():
        rt.guard.call()
        code = rt.http.post(f"{p.mcp_url}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code
        return rt.record("S14-MCP-NO-TOKEN-401", "C04.b", *classify.expected_refusal(code, 401))
    if "S14" not in skip:
        rt.guarded("S14-MCP-NO-TOKEN-401", "C04.b", s14)

    def s15():
        rt.guard.call()
        code = rt.http.post(f"{p.mcp_url}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                            headers={"Authorization": f"Bearer {rt.token('https://ai.azure.com/.default')}"}).status_code
        return rt.record("S15-MCP-WRONG-AUDIENCE-401", "C04.b", *classify.expected_refusal(code, 401))
    if "S15" not in skip:
        rt.guarded("S15-MCP-WRONG-AUDIENCE-401", "C04.b", s15)

    def s06():
        rt.guard.call()
        r = rt.http.get(f"{p.mcp_url}/probe/a2a-card", params={"agent": "hapq-a2a"},
                        headers={"Authorization": f"Bearer {rt.token(f'{p.mcp_audience}/.default')}"})
        payload = r.json() if r.status_code == 200 else {}
        code, problem = payload.get("status"), payload.get("problem") or {}
        st, msg = classify.expected_refusal(code, 403)
        rt.guard.call()  # control: the same request by an identity WITH a Foundry role, in the same run
        control = rt.http.get(f"{p.project_endpoints[0]}/agents/hapq-a2a/endpoint/protocols/a2a/agentCard/v1.0",
                              headers={"Authorization": f"Bearer {rt.token('https://ai.azure.com/.default')}",
                                       "A2A-Version": "1.0"}).status_code
        if code == 404 and control == 200:  # the platform hides the resource from an unauthorized identity
            st, msg = Status.PASS, (f"access refused to an identity without Foundry role: HTTP 404 "
                                    f"({problem.get('title')}); same request with a Foundry role in this run: HTTP 200; "
                                    "expected 403, observed 404 (resource hidden)")
        elif code == 404:
            st, msg = Status.NOT_TESTED, f"inconclusive: 404 without role, control with role returned {control}"
        if code is None:
            st, msg = Status.NOT_TESTED, f"kit/tool error: probe answered HTTP {r.status_code}"
        return rt.record("S06-A2A-NO-ROLE-403", "C03", st,
                         msg + " (agent card GET only, by the tool's managed identity which holds no Foundry role)",
                         probe_http=r.status_code, probe_body=r.text[:300], problem=problem,
                         control_with_role_http=control if code == 404 else None)
    if "S06" not in skip:
        rt.guarded("S06-A2A-NO-ROLE-403", "C03", s06)

    def s07():
        rt.setup_toolbox()
        d = rt.deploy(project, AGENT_MCP, AGENTS_DIR / "toolbox",
                      {"AZURE_AI_MODEL_DEPLOYMENT_NAME": p.model_deployment, "TOOLBOX_NAME": TOOLBOX},
                      requirements=REPO_ROOT / "agents" / "locks" / "minimal.requirements.txt")
        if d["final"] != "active":
            st, msg = classify.version_outcome(d["history"], d["final"], d["error"])
            return rt.record("S07-TOOL-AGENT-IDENTITY", "C04.b", Status.NOT_TESTED, f"test agent not ready: {msg}")
        n = secrets.token_hex(6)
        out = rt.invoke(project, AGENT_MCP, f"NONCE:{n} CALL_WHOAMI {n}")
        calls = rt.mcp_calls(n)
        expected = rt.expected_agent_principal(project, AGENT_MCP)
        st, msg = classify.agent_identity(calls, expected)
        evidence = []
        if not calls and out["session"]:  # why did no call reach the tool? technical lines only, never model text
            lines = rt.session_logs(project, AGENT_MCP, d["version"], out["session"], wait_s=30)
            evidence = [ln[-300:] for ln in lines if any(k in ln for k in ("/toolboxes/", "mcp", "MCP", "Toolbox"))
                        and any(k in ln for k in ("HTTP", "Error", "error", "401", "403", "404", "500"))][:8]
        return rt.record("S07-TOOL-AGENT-IDENTITY", "C04.b", st, msg, answer=out["text"][:200],
                         session_id=out["session"], response_id=out["id"], agent_log_evidence=evidence,
                         tool_ready=rt.state.get("tool_ready"),
                         claim_names=sorted({k for c in calls for k in c.get("claims", {}).get("claim_names", [])}),
                         idtyp=sorted({c.get("claims", {}).get("idtyp") for c in calls if c.get("claims", {}).get("idtyp")}))
    if "S07" not in skip:
        rt.guarded("S07-TOOL-AGENT-IDENTITY", "C04.b", s07)
    for r in rt.results:
        r.details["kit_usage"] = rt.guard.summary()
    return rt.results
