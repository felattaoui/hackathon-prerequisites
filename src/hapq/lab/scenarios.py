"""Phase A execution scenarios (S01–S15). Written for environment A; designed to be replayed in B.

Requires the `lab` extra (azure-identity, azure-ai-projects>=2.5, httpx) and a deployed lab profile.
Every paid action goes through the Guard (limits). Every created object is ledgered. Statuses come from
hapq.lab.classify. Exceptions are mapped explicitly:
  - platform HTTP errors      -> FAIL with the platform's status/message
  - kit limits reached        -> BLOCKED
  - harness/import problems   -> NOT_TESTED ("harness error"), never PASS or FAIL
"""
from __future__ import annotations

import hashlib
import io
import json
import secrets
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from ..azcli import Az
from ..config import REPO_ROOT, KitConfig
from ..ledger import Ledger
from ..limits import Guard, LimitExceeded
from ..redact import redact_text
from ..results import CheckResult, Evidence, Status
from . import classify
from .profile import LabProfile

AGENTS_DIR = REPO_ROOT / "agents"
TOOLBOX = "hapq-tools"
CONNECTION = "hapq-mcp"
AGENT_MIN, AGENT_LG, AGENT_XP = "hapq-min", "hapq-lg", "hapq-xp"


def zip_dir(path: Path, requirements: Path | None = None) -> bytes:
    """Flat zip of an agent folder. `requirements` replaces requirements.txt (e.g. a pinned lock file)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(path.iterdir()):
            if f.is_file() and f.suffix in (".py", ".txt"):
                src = requirements if (f.name == "requirements.txt" and requirements is not None) else f
                # deterministic entry: fixed timestamp and permissions, so the hash depends on content only
                info = zipfile.ZipInfo(f.name, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o100644 << 16  # regular file + rw-r--r-- (S_IFREG needed by Unix extractors)
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, src.read_bytes())  # flat at the root, as required
    return buf.getvalue()


class Runtime:
    def __init__(self, cfg: KitConfig, profile: LabProfile, ledger: Ledger, run_id: str, az: Az | None = None):
        import httpx
        from azure.ai.projects import AIProjectClient
        from azure.identity import AzureCliCredential

        self.cfg, self.profile, self.ledger, self.run_id = cfg, profile, ledger, run_id
        lim = cfg.limits
        self.az = az or Az(cfg.subscription_id)
        self.guard = Guard(lim)
        self.cred = AzureCliCredential(process_timeout=lim.call_timeout_s)
        # every client gets the kit's timeout and a bounded retry count
        self.http = httpx.Client(timeout=lim.call_timeout_s, transport=httpx.HTTPTransport(retries=0))
        self.projects = {n: AIProjectClient(endpoint=e, credential=self.cred, connection_timeout=lim.call_timeout_s,
                                            read_timeout=lim.call_timeout_s, retry_total=lim.max_retries)
                         for n, e in zip(profile.project_names, profile.project_endpoints)}
        self.endpoints = dict(zip(profile.project_names, profile.project_endpoints))
        self.results: list[CheckResult] = []
        self.state: dict = {}

    # -- helpers ---------------------------------------------------------------------------------------------
    def token(self, scope: str) -> str:
        return self.cred.get_token(scope).token  # never printed or stored

    def record(self, sid: str, criterion: str, status: Status, observed: str, **details) -> CheckResult:
        r = CheckResult(sid, criterion, sid, status, Evidence.OBSERVED, details.pop("expected", ""),
                        redact_text(observed), region=self.profile.region, environment=self.profile.environment,
                        details=details)
        self.results.append(r)
        return r

    def passed(self, sid: str) -> bool:
        return any(r.check_id == sid and r.status is Status.PASS for r in self.results)

    def skip(self, sid: str, criterion: str, prereq: str) -> CheckResult:
        return self.record(sid, criterion, Status.NOT_TESTED, f"prerequisite {prereq} did not PASS")

    def guarded(self, sid: str, criterion: str, fn):
        try:
            with self.guard.slot():
                return fn()
        except LimitExceeded as exc:
            return self.record(sid, criterion, Status.BLOCKED, str(exc))
        except Exception as exc:  # map platform errors vs harness errors
            code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
            if code:
                return self.record(sid, criterion, Status.FAIL, f"platform HTTP {code}: {str(exc)[:300]}")
            return self.record(sid, criterion, Status.NOT_TESTED, f"harness error {type(exc).__name__}: {str(exc)[:300]}")

    # -- setup: MCP connection + toolbox in proj1 (ledgered) ----------------------------------------------------
    def setup_toolbox(self) -> None:
        from azure.ai.projects.models import MCPToolboxTool

        p = self.profile
        conn_id = f"{p.account_id}/projects/{p.project_names[0]}/connections/{CONNECTION}"
        url = f"https://management.azure.com{conn_id}?api-version=2025-04-01-preview"
        existing = self.az.json("rest", "--method", "GET", "--url", url, scoped=False)
        owned = {e.id.lower() for e in self.ledger.owned() if e.kind == "connection"}
        if existing.ok and conn_id.lower() not in owned:
            raise PermissionError(f"connection {CONNECTION} exists and was not created by the kit")
        if not existing.ok:
            body = {"properties": {"authType": "AgenticIdentityToken", "category": "RemoteTool",
                                   "target": f"{p.mcp_url}/mcp", "audience": p.mcp_audience,
                                   "Credentials": {}, "metadata": {}}}
            put = self.az.json("rest", "--method", "PUT", "--url", url, "--headers", "Content-Type=application/json",
                               "--body", json.dumps(body), scoped=False)
            if not put.ok:
                raise RuntimeError("connection creation failed: " + (put.stderr or "")[-300:])
            self.ledger.created("connection", conn_id, CONNECTION, p.environment, self.run_id, account_id=p.account_id)
        proj1 = self.projects[p.project_names[0]]
        tb = proj1.toolboxes.create_version(name=TOOLBOX, description="hapq test toolbox", tools=[
            MCPToolboxTool(server_label="whoami", server_url=f"{p.mcp_url}/mcp", require_approval="never",
                           project_connection_id=CONNECTION)])
        self.ledger.created("toolbox", f"{self.endpoints[p.project_names[0]]}|{TOOLBOX}", TOOLBOX, p.environment,
                            self.run_id, account_id=p.account_id, version=str(tb.version))

    # -- hosted agents ----------------------------------------------------------------------------------------------
    def deploy(self, project: str, name: str, src: Path, env: dict, requirements: Path | None = None) -> dict:
        from azure.ai.projects.models import (CodeConfiguration, HostedAgentDefinition, ProtocolVersionRecord,
                                              SessionConfiguration)

        from azure.core.exceptions import ResourceNotFoundError

        client, lim = self.projects[project], self.cfg.limits
        agent_id = f"{self.endpoints[project]}|{name}"
        owned = {e.id for e in self.ledger.owned() if e.kind == "agent"}
        try:
            client.agents.get(agent_name=name)
            exists = True
        except ResourceNotFoundError:
            exists = False  # only an explicit 404 means "absent"; any other error propagates
        if exists and agent_id not in owned:
            raise PermissionError(f"agent {name} exists in {project} and was not created by the kit: not modified")
        self.guard.version()
        if agent_id not in owned:
            # ledgered BEFORE the call: a create that times out client-side may still succeed server-side
            self.ledger.created("agent", agent_id, name, self.profile.environment, self.run_id,
                                account_id=self.profile.account_id, project=project)
        code = zip_dir(src, requirements)
        created = client.agents.create_version_from_code(
            agent_name=name,
            definition=HostedAgentDefinition(
                cpu=lim.cpu, memory=lim.memory,
                code_configuration=CodeConfiguration(runtime="python_3_13", entry_point=["python", "main.py"],
                                                     dependency_resolution="remote_build"),
                protocol_versions=[ProtocolVersionRecord(protocol="responses", version="2.0.0")],
                environment_variables=env,
                session_configuration=SessionConfiguration(idle_timeout_seconds=lim.idle_timeout_s)),
            code=(f"{name}.zip", code, "application/zip"), code_zip_sha256=hashlib.sha256(code).hexdigest(),
            description="hapq lab agent (synthetic)")
        history, deadline, version = [], time.time() + lim.deploy_timeout_s, None
        while time.time() < deadline:
            self.guard.poll()
            version = client.agents.get_version(agent_name=name, agent_version=created.version)
            history.append(version["status"])
            if version["status"] in ("active", "failed"):
                break
            time.sleep(10)
        final = history[-1] if history else "unknown"
        error = json.dumps(version.get("error")) if version is not None and version.get("error") else None
        return {"version": created.version, "history": history, "final": final, "error": error}

    def openai(self, project: str, agent: str):
        lim = self.cfg.limits
        return self.projects[project].get_openai_client(agent_name=agent).with_options(
            timeout=lim.call_timeout_s, max_retries=lim.max_retries)

    def invoke(self, project: str, agent: str, text: str, session: str | None = None,
               previous_response_id: str | None = None) -> dict:
        self.guard.call()
        if session:
            self.guard.session(session)
        kwargs = {"input": text, "max_output_tokens": self.cfg.limits.max_output_tokens}
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id
        if session:
            kwargs["extra_body"] = {"agent_session_id": session}
        try:
            r = self.openai(project, agent).responses.create(**kwargs)
        except Exception:
            self.guard.add_tokens(None)  # failed call: consumption unknown, not zero
            raise
        usage = getattr(r, "usage", None)
        total = getattr(usage, "total_tokens", None) if usage is not None else None
        self.guard.add_tokens(total)  # None -> counted as unknown, never as zero
        new_session = (r.model_extra or {}).get("agent_session_id")
        self.guard.session(new_session)
        return {"text": r.output_text or "", "session": new_session, "id": r.id, "status": getattr(r, "status", None),
                "usage": None if usage is None else {
                    "input": getattr(usage, "input_tokens", None), "output": getattr(usage, "output_tokens", None),
                    "reasoning": getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", None),
                    "total": total}, "max_output_tokens": kwargs["max_output_tokens"]}

    def mcp_calls(self, nonce: str, wait_s: int = 45) -> list[dict]:
        tok = self.token(f"{self.profile.mcp_audience}/.default")
        deadline = time.time() + wait_s
        while True:
            r = self.http.get(f"{self.profile.mcp_url}/calls", params={"nonce": nonce},
                              headers={"Authorization": f"Bearer {tok}"})
            calls = r.json() if r.status_code == 200 else []
            if calls or time.time() > deadline:
                return calls
            time.sleep(5)

    def session_logs(self, project: str, agent: str, version: str, session: str, wait_s: int = 60,
                     max_lines: int = 200) -> list[str]:
        """Bounded read of a session's container log stream (time and line limits)."""
        self.guard.call()
        stream = self.projects[project].agents.get_session_log_stream(agent_name=agent, agent_version=version,
                                                                      session_id=session)
        lines, deadline = [], time.time() + wait_s
        iterator = stream.iter_lines() if hasattr(stream, "iter_lines") else (stream if hasattr(stream, "__iter__") else [stream])
        for chunk in iterator:
            text = chunk.decode("utf-8", "replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
            if text.strip():
                lines.append(text.strip())
            if len(lines) >= max_lines or time.time() > deadline:
                break
        return lines

    def correlated_traces(self, start: datetime, ids: list[str]) -> tuple[int, int, str]:
        """Rows carrying one of the tested ids (response id, session id) vs. any recent rows."""
        ids = [i for i in ids if i and all(c.isalnum() or c in "-_" for c in i)]
        if not ids:
            return 0, 0, "no request id to correlate"
        since = f"datetime({start.isoformat(timespec='seconds')})"
        has = " or ".join(f"* has '{i}'" for i in ids)
        query = (f"union requests, dependencies, traces, exceptions | where timestamp > {since} "
                 f"| summarize correlated=countif({has}), recent=count()")
        deadline, last = time.time() + self.cfg.limits.trace_wait_s, (0, 0, "")
        while True:
            self.guard.poll()
            res = self.az.json("monitor", "app-insights", "query", "--apps", self.profile.app_insights_app_id,
                               "--analytics-query", query, scoped=False, timeout=self.cfg.limits.call_timeout_s)
            if not res.ok:
                err = (res.stderr or "")[-200:]
                if "403" in err or "authoriz" in err.lower():
                    raise PermissionError("trace query not authorized: " + err)
                last = (0, 0, err)
            else:
                row = res.json()["tables"][0]["rows"][0]
                last = (int(row[0]), int(row[1]), "")
                if last[0]:
                    return last
            if time.time() > deadline:
                return last
            time.sleep(30)

    def expected_agent_principal(self, project: str, agent: str) -> str | None:
        """Principal id of the agent's own identity as reported by the platform (None if not exposed)."""
        details = self.projects[project].agents.get(agent_name=agent)
        data = details.as_dict() if hasattr(details, "as_dict") else dict(details)

        def find(obj, in_identity=False):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if in_identity and k in ("principal_id", "principalId", "object_id", "objectId") and v:
                        return v
                    found = find(v, in_identity or "identity" in k.lower())
                    if found:
                        return found
            elif isinstance(obj, list):
                for v in obj:
                    found = find(v, in_identity)
                    if found:
                        return found
            return None
        return find(data)

    def a2a_base(self, project: str, agent: str) -> str:
        return f"{self.endpoints[project]}/agents/{agent}/endpoint/protocols/a2a"


def run_phase_a(rt: Runtime) -> list[CheckResult]:
    p = rt.profile
    proj1, proj2 = p.project_names[0], p.project_names[1]
    start = datetime.now(timezone.utc)

    # S14/S15: the MCP tool refuses missing and foreign tokens (harness sanity, part of C04.b)
    def s14():
        code = rt.http.post(f"{p.mcp_url}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code
        return rt.record("S14-MCP-NO-TOKEN-401", "C04.b", *classify.expected_refusal(code, 401))
    rt.guarded("S14-MCP-NO-TOKEN-401", "C04.b", s14)

    def s15():
        code = rt.http.post(f"{p.mcp_url}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                            headers={"Authorization": f"Bearer {rt.token('https://ai.azure.com/.default')}"}).status_code
        return rt.record("S15-MCP-WRONG-AUDIENCE-401", "C04.b", *classify.expected_refusal(code, 401))
    rt.guarded("S15-MCP-WRONG-AUDIENCE-401", "C04.b", s15)

    toolbox_ok = True
    try:
        with rt.guard.slot():
            rt.setup_toolbox()
    except Exception as exc:  # recorded on the scenarios that depend on it
        toolbox_ok, rt.state["toolbox_error"] = False, f"toolbox setup failed: {type(exc).__name__}: {str(exc)[:200]}"

    # S01 + S09: create a hosted agent on the GA API, observe status transitions
    def s01():
        d = rt.deploy(proj1, AGENT_MIN, AGENTS_DIR / "minimal",
                      {"AZURE_AI_MODEL_DEPLOYMENT_NAME": p.model_deployment, "TOOLBOX_NAME": TOOLBOX})
        rt.state["min"] = d
        st, msg = classify.version_outcome(d["history"], d["final"], d["error"])
        rt.record("S01-GA-CREATE", "C01", st, msg + " (no preview header, no feature registration)",
                  version=d["version"])
        readable = bool(d["history"])
        return rt.record("S09-OBS-STATUS", "C07", Status.PASS if readable and d["final"] in ("active", "failed")
                         else Status.FAIL, f"{len(d['history'])} status reads, final '{d['final']}'")
    rt.guarded("S01-GA-CREATE", "C01", s01)

    nonce = secrets.token_hex(6)
    if rt.passed("S01-GA-CREATE"):
        def s02():
            out = rt.invoke(proj1, AGENT_MIN, f"NONCE:{nonce} Say hello.")
            rt.state["session"], rt.state["response_id"] = out["session"], out["id"]
            ok = classify.contains_nonce(out["text"], nonce)
            return rt.record("S02-INVOKE", "C02.a", Status.PASS if ok else Status.FAIL,
                             f"HTTP 200, nonce {'present' if ok else 'absent'} in answer")
        rt.guarded("S02-INVOKE", "C02.a", s02)
    else:
        rt.skip("S02-INVOKE", "C02.a", "S01-GA-CREATE")

    # S10: container log stream for the S02 session (bounded read)
    if rt.state.get("session"):
        def s10():
            lines = rt.session_logs(proj1, AGENT_MIN, rt.state["min"]["version"], rt.state["session"])
            return rt.record("S10-OBS-LOGS", "C07", Status.PASS if lines else Status.FAIL,
                             f"{len(lines)} log line(s) read for the tested session")
        rt.guarded("S10-OBS-LOGS", "C07", s10)
    else:
        rt.skip("S10-OBS-LOGS", "C07", "S02-INVOKE")

    # S07: agent identity reaches the MCP tool
    if rt.passed("S01-GA-CREATE") and toolbox_ok:
        def s07():
            n = secrets.token_hex(6)
            rt.invoke(proj1, AGENT_MIN, f"NONCE:{n} CALL_WHOAMI {n}")
            calls = rt.mcp_calls(n)
            expected = rt.expected_agent_principal(proj1, AGENT_MIN)
            st, msg = classify.agent_identity(calls, expected)
            return rt.record("S07-TOOL-AGENT-IDENTITY", "C04.b", st, msg,
                             claim_names=sorted({k for c in calls for k in c.get("claims", {}).get("claim_names", [])}))
        rt.guarded("S07-TOOL-AGENT-IDENTITY", "C04.b", s07)
    elif toolbox_ok:
        rt.skip("S07-TOOL-AGENT-IDENTITY", "C04.b", "S01-GA-CREATE")
    else:
        rt.record("S07-TOOL-AGENT-IDENTITY", "C04.b", Status.NOT_TESTED, rt.state["toolbox_error"])

    # S03–S06: A2A on the hosted agent
    if rt.passed("S01-GA-CREATE"):
        def s03():
            from azure.ai.projects.models import (A2AProtocolConfiguration, AgentCard, AgentCardSkill,
                                                  AgentEndpointConfig, ProtocolConfiguration,
                                                  ResponsesProtocolConfiguration)
            rt.projects[proj1].agents.update_details(
                agent_name=AGENT_MIN,
                agent_endpoint=AgentEndpointConfig(protocol_configuration=ProtocolConfiguration(
                    responses=ResponsesProtocolConfiguration(), a2a=A2AProtocolConfiguration())),
                agent_card=AgentCard(version="1.0", description="hapq test agent (synthetic)", skills=[
                    AgentCardSkill(id="echo", name="Echo nonce", description="Repeats the nonce it receives")]))
            rt.guard.call()
            r = rt.http.get(f"{rt.a2a_base(proj1, AGENT_MIN)}/agentCard/v1.0",
                            headers={"Authorization": f"Bearer {rt.token('https://ai.azure.com/.default')}"})
            card = r.json() if r.status_code == 200 else {}
            versions = {card.get("protocolVersion")} | {i.get("protocolVersion") for i in card.get("supportedInterfaces", []) or []}
            ok = r.status_code == 200 and "1.0" in versions
            return rt.record("S03-A2A-CARD", "C03", Status.PASS if ok else Status.FAIL,
                             f"HTTP {r.status_code}, protocol versions {sorted(v for v in versions if v)}")
        rt.guarded("S03-A2A-CARD", "C03", s03)

        def s04():
            rt.guard.call()
            marker = secrets.token_hex(6)           # expected in the agent's output
            body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()),  # JSON-RPC id: unrelated to the marker
                    "method": "SendMessage", "params": {"message": {
                        "messageId": str(uuid.uuid4()), "role": "ROLE_USER",
                        "parts": [{"text": f"NONCE:{marker} Say hello."}]}}}
            r = rt.http.post(rt.a2a_base(proj1, AGENT_MIN), json=body, headers={
                "Authorization": f"Bearer {rt.token('https://ai.azure.com/.default')}", "A2A-Version": "1.0"})
            rt.guard.add_tokens(None)               # A2A responses carry no usage: unknown, not zero
            payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            agent_text = classify.a2a_agent_text(payload)
            ok = r.status_code == 200 and "error" not in payload and marker in agent_text
            return rt.record("S04-A2A-MESSAGE", "C03", Status.PASS if ok else Status.FAIL,
                             f"HTTP {r.status_code}, JSON-RPC error={'error' in payload}, marker "
                             f"{'present' if marker in agent_text else 'absent'} in agent-produced content")
        if rt.passed("S03-A2A-CARD"):
            rt.guarded("S04-A2A-MESSAGE", "C03", s04)
        else:
            rt.skip("S04-A2A-MESSAGE", "C03", "S03-A2A-CARD")

        def s05():
            rt.guard.call()
            code = rt.http.get(f"{rt.a2a_base(proj1, AGENT_MIN)}/agentCard/v1.0").status_code
            return rt.record("S05-A2A-NO-TOKEN-401", "C03", *classify.expected_refusal(code, 401))
        rt.guarded("S05-A2A-NO-TOKEN-401", "C03", s05)

        def s06():
            rt.guard.call()
            r = rt.http.get(f"{p.mcp_url}/probe/a2a-card", params={"agent": AGENT_MIN},
                            headers={"Authorization": f"Bearer {rt.token(f'{p.mcp_audience}/.default')}"})
            code = r.json().get("status") if r.status_code == 200 else None
            st, msg = classify.expected_refusal(code, 403)
            return rt.record("S06-A2A-NO-ROLE-403", "C03", st, msg + " (identity: MCP app managed identity, no Foundry role)")
        rt.guarded("S06-A2A-NO-ROLE-403", "C03", s06)
    else:
        for sid in ("S03-A2A-CARD", "S04-A2A-MESSAGE", "S05-A2A-NO-TOKEN-401", "S06-A2A-NO-ROLE-403"):
            rt.skip(sid, "C03", "S01-GA-CREATE")

    # S08: LangGraph
    def s08():
        d = rt.deploy(proj1, AGENT_LG, AGENTS_DIR / "langgraph", {"FOUNDRY_MODEL_NAME": p.model_deployment})
        if d["final"] != "active":
            return rt.record("S08-LANGGRAPH", "C08", *classify.version_outcome(d["history"], d["final"], d["error"]))
        n = secrets.token_hex(6)
        out = rt.invoke(proj1, AGENT_LG, f"NONCE:{n} Say hello.")
        ok = classify.contains_nonce(out["text"], n)
        return rt.record("S08-LANGGRAPH", "C08", Status.PASS if ok else Status.FAIL,
                         f"version active, nonce {'present' if ok else 'absent'} in answer")
    rt.guarded("S08-LANGGRAPH", "C08", s08)

    # S12: cross-project tool consumption (expectation: refused)
    if toolbox_ok:
        def s12():
            consumer = f"{rt.endpoints[proj1]}/toolboxes/{TOOLBOX}/mcp"
            d = rt.deploy(proj2, AGENT_XP, AGENTS_DIR / "minimal",
                          {"AZURE_AI_MODEL_DEPLOYMENT_NAME": p.model_deployment,
                           "TOOLBOX_ENDPOINT": consumer + "?api-version=v1"})
            calls, refusals = [], []
            if d["final"] == "active":
                n = secrets.token_hex(6)
                out = rt.invoke(proj2, AGENT_XP, f"NONCE:{n} CALL_WHOAMI {n}")
                calls = rt.mcp_calls(n, wait_s=30)
                if out["session"]:
                    refusals = classify.technical_refusals(
                        rt.session_logs(proj2, AGENT_XP, d["version"], out["session"]), f"/toolboxes/{TOOLBOX}/")
            st, msg, layers = classify.cross_project_tool(d["final"], calls, refusals)
            return rt.record("S12-XPROJECT-TOOL", "C06", st, msg, **layers)
        rt.guarded("S12-XPROJECT-TOOL", "C06", s12)
    else:
        rt.record("S12-XPROJECT-TOOL", "C06", Status.NOT_TESTED, rt.state["toolbox_error"])

    # S13: inventory across projects (aggregation of per-project lists)
    def s13():
        found = {name: [a.name for a in client.agents.list()] for name, client in rt.projects.items()}
        expected = {proj1: [AGENT_MIN, AGENT_LG], proj2: [AGENT_XP]}
        expected = {k: [a for a in v if any(e.name == a for e in rt.ledger.owned() if e.kind == "agent")]
                    for k, v in expected.items()}
        return rt.record("S13-AGENT-INVENTORY", "C06", *classify.inventory(found, expected))
    rt.guarded("S13-AGENT-INVENTORY", "C06", s13)

    # S11: telemetry correlated with the tested request (response id / session id)
    if rt.passed("S02-INVOKE"):
        def s11():
            try:
                corr, recent, err = rt.correlated_traces(start, [rt.state.get("response_id"), rt.state.get("session")])
            except PermissionError as exc:
                return rt.record("S11-OBS-TRACES", "C07", Status.BLOCKED, str(exc))
            st, msg = classify.trace_correlation(corr, recent)
            return rt.record("S11-OBS-TRACES", "C07", st, msg + (f"; last query error {err}" if err else ""))
        rt.guarded("S11-OBS-TRACES", "C07", s11)
    else:
        rt.skip("S11-OBS-TRACES", "C07", "S02-INVOKE")

    for r in rt.results:
        r.details.setdefault("kit_usage", rt.guard.summary())
    return rt.results


def delete_data_plane(cfg: KitConfig, entry) -> tuple[bool, str]:
    """Data-plane deleter used by cleanup for agents, toolboxes and connections."""
    try:
        if entry.kind == "connection":
            res = Az(cfg.subscription_id).json("rest", "--method", "DELETE", "--url",
                                               f"https://management.azure.com{entry.id}?api-version=2025-04-01-preview",
                                               scoped=False)
            return res.ok, (res.stderr or "")[-200:]
        from azure.ai.projects import AIProjectClient
        from azure.identity import AzureCliCredential
        endpoint, name = entry.id.split("|", 1)
        client = AIProjectClient(endpoint=endpoint, credential=AzureCliCredential())
        if entry.kind == "agent":
            client.agents.delete(agent_name=name, force=True)
        else:
            client.toolboxes.delete(name=name)
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"
