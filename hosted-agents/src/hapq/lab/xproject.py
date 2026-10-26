"""C06 on environment A: second project (proj2), cross-project tool consumption (S12) and agent inventory (S13).

proj2 is created alone (ARM PUT, ledgered) instead of redeploying the whole phase-A template.
S12 consumer: agents/toolbox (the working FoundryToolbox pattern) deployed in proj2 with TOOLBOX_ENDPOINT pointing at
proj1's toolbox. Evidence is technical only (tool-side call records, HTTP refusals in container logs).
S13 shows an inventory can be BUILT by iterating projects; it is not a governance registry.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path

from ..azcli import Az
from ..config import REPO_ROOT, KitConfig
from ..ledger import Ledger
from ..results import CheckResult, Status
from . import classify
from .scenarios import AGENT_MIN, AGENT_XP, AGENTS_DIR, TOOLBOX, Runtime

PROFILE_A = REPO_ROOT / "config" / "lab-a.local.toml"
API = "2025-06-01"


def ensure_proj2(cfg: KitConfig, az: Az, ledger: Ledger, run_id: str, log=print) -> str:
    """Create proj2 on the existing A account if missing (ledgered), then add it to the local A profile."""
    text = PROFILE_A.read_text(encoding="utf-8")
    account = re.search(r'^account_name\s*=\s*"([^"]+)"', text, re.M).group(1)
    acc_id = f"{cfg.rg_scope}/providers/Microsoft.CognitiveServices/accounts/{account}"
    pid = f"{acc_id}/projects/proj2"
    url = f"https://management.azure.com{pid}?api-version={API}"
    got = az.json("rest", "--method", "get", "--url", url, scoped=False)
    if not got.ok:
        body = {"location": cfg.lab_region, "identity": {"type": "SystemAssigned"},
                "tags": {"kit": "hapq", "phase": "a", "purpose": "hosted-agent-prereq-lab"},
                "properties": {"displayName": "hapq proj2", "description": "Cross-project test project (synthetic data only)"}}
        if not any(e.id == pid for e in ledger.owned()):
            ledger.created("arm", pid, "proj2", "A", run_id, account_id=acc_id)  # ledgered before creation
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(body, fh)  # body in a file: Windows command-line quoting breaks inline JSON
        try:
            put = az.json("rest", "--method", "put", "--url", url, "--headers", "Content-Type=application/json",
                          "--body", f"@{fh.name}", scoped=False)
        finally:
            Path(fh.name).unlink(missing_ok=True)
        if not put.ok:
            raise RuntimeError("proj2 creation failed: " + (put.stderr or "")[-300:])
        for _ in range(60):
            st = az.json("rest", "--method", "get", "--url", url, scoped=False)
            if st.ok and (st.json().get("properties") or {}).get("provisioningState") == "Succeeded":
                break
            time.sleep(10)
        log("  proj2 created on the A account (ledgered)")
    if '"proj2"' not in text:
        ep = f"https://{account}.services.ai.azure.com/api/projects/proj2"
        text = re.sub(r'^project_names\s*=.*$', 'project_names = ["proj1", "proj2"]', text, flags=re.M)
        text = re.sub(r'^project_endpoints\s*=\s*\["([^"]+)"\]', lambda m: f'project_endpoints = ["{m.group(1)}", "{ep}"]',
                      text, flags=re.M)
        PROFILE_A.write_text(text, encoding="utf-8")
    return pid


def run(rt: Runtime) -> list[CheckResult]:
    p = rt.profile
    proj1, proj2 = p.project_names[0], p.project_names[1]

    def s12():
        consumer = f"{rt.endpoints[proj1]}/toolboxes/{TOOLBOX}/mcp?api-version=v1"
        d = rt.deploy(proj2, AGENT_XP, AGENTS_DIR / "toolbox",
                      {"AZURE_AI_MODEL_DEPLOYMENT_NAME": p.model_deployment, "TOOLBOX_ENDPOINT": consumer},
                      requirements=REPO_ROOT / "agents" / "locks" / "minimal.requirements.txt")
        calls, refusals, text = [], [], ""
        if d["final"] == "active":
            n = secrets.token_hex(6)
            out = rt.invoke(proj2, AGENT_XP, f"NONCE:{n} CALL_WHOAMI {n}")
            text = out.get("text") or ""
            calls = rt.mcp_calls(n, wait_s=45)
            if out["session"]:
                logs = rt.session_logs(proj2, AGENT_XP, d["version"], out["session"])
                refusals = classify.technical_refusals(logs, f"/toolboxes/{TOOLBOX}/")
        st, msg, layers = classify.cross_project_tool(d["final"], calls, refusals)
        layers["need_observed"] = layers.pop("observed")  # 'observed' is record()'s own argument
        return rt.record("S12-XPROJECT-TOOL", "C06", st, msg, version_status=d["final"], tool_calls=len(calls),
                         refusals=refusals[:3], **layers)
    rt.guarded("S12-XPROJECT-TOOL", "C06", s12)

    if any(r.check_id == "S12-XPROJECT-TOOL" and r.status is Status.FAIL for r in rt.results):
        rt.guarded("S12-XPROJECT-TOOL-RBAC", "C06", lambda: s12_rbac(rt))

    def s13():
        found = {name: [a.name for a in client.agents.list()] for name, client in rt.projects.items()}
        owned = {e.name for e in rt.ledger.owned() if e.kind == "agent"}
        expected = {proj1: [a for a in [AGENT_MIN] if a in owned], proj2: [a for a in [AGENT_XP] if a in owned]}
        st, msg = classify.inventory(found, expected)
        return rt.record("S13-AGENT-INVENTORY", "C06", st,
                         msg + f"; counts per project: { {k: len(v) for k, v in found.items()} }; an inventory built by "
                         "iterating projects, not an enterprise registry")
    rt.guarded("S13-AGENT-INVENTORY", "C06", s13)
    return rt.results


def run_rbac_only(rt: Runtime) -> list[CheckResult]:
    """Re-run only the RBAC variant (the role is already granted: the no-role case cannot be replayed)."""
    rt.state["rbac_wait"] = False
    rt.guarded("S12-XPROJECT-TOOL-RBAC", "C06", lambda: s12_rbac(rt))
    return rt.results


def s12_rbac(rt: Runtime):
    p = rt.profile
    proj1, proj2 = p.project_names[0], p.project_names[1]
    """Same need, with an explicit data-plane role for the project-2 agent identity on project 1 only.
    Answers: is cross-project consumption possible by RBAC (not by default)?"""
    d = rt.projects[proj2].agents.get(agent_name=AGENT_XP).as_dict()
    principal = (d.get("instance_identity") or {}).get("principal_id")
    if not principal:
        return rt.record("S12-XPROJECT-TOOL-RBAC", "C06", Status.NOT_TESTED, "agent identity of hapq-xp not readable")
    account = rt.endpoints[proj1].split("//", 1)[1].split(".", 1)[0]
    scope = f"{rt.cfg.rg_scope}/providers/Microsoft.CognitiveServices/accounts/{account}/projects/{proj1}"
    res = rt.az.json("role", "assignment", "create", "--assignee-object-id", principal, "--assignee-principal-type",
                     "ServicePrincipal", "--role", "53ca6127-db72-4b80-b1b0-d745d6d5456d", "--scope", scope, scoped=False)
    if res.ok:
        rt.ledger.created("role", res.json()["id"], "foundry-user-hapq-xp-on-proj1", "A", rt.run_id)
    calls, n, statuses = [], "", []
    for _ in range(3):  # RBAC propagation, bounded
        if not res.ok or rt.state.get("rbac_wait", True):
            time.sleep(90)
        n = secrets.token_hex(6)
        session = None
        try:
            out = rt.invoke(proj2, AGENT_XP, f"NONCE:{n} CALL_WHOAMI {n}")
            session = out.get("session")
        except Exception as exc:  # the agent fails when the toolbox refuses: keep the session for the logs
            resp = getattr(exc, "response", None)
            session = resp.headers.get("x-agent-session-id") if resp is not None else None
        calls = rt.mcp_calls(n, wait_s=45)
        if session:
            ver = d["versions"]["latest"]["version"]
            logs = rt.session_logs(proj2, AGENT_XP, ver, session)
            statuses = [m.group(1) for chunk in logs
                        for m in re.finditer(r"/toolboxes/\S+ \S*HTTP/1\.1 (\d{3})", chunk)]
        if calls or statuses:
            break
    oids = {c.get("claims", {}).get("oid") for c in calls}
    if calls and principal in oids:
        return rt.record("S12-XPROJECT-TOOL-RBAC", "C06", Status.PASS,
                         "with Foundry User granted to the project-2 agent identity on project 1 only, the tool of "
                         "project 1's toolbox received the call; the token oid is the project-2 agent identity",
                         tool_calls=len(calls))
    return rt.record("S12-XPROJECT-TOOL-RBAC", "C06", Status.FAIL,
                     "with Foundry User on project 1, project 1's toolbox answered the project-2 agent with HTTP "
                     f"{' -> '.join(statuses) or 'no status captured'} (MCP handshake/listing vs tool call); no tool "
                     f"call reached the tool ({len(calls)})", tool_calls=len(calls), toolbox_statuses=statuses,
                     role_assigned=res.ok)
