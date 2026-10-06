"""Phase B scenarios: in-VNet runner (positive paths) + workstation (expected refusals with a VALID token).

Every result is recorded with environment "B" and its real execution point ("in-vnet-runner" or "workstation-public").
Agents are ledgered before creation (account_id of the private account: cleanup cascades with it).
Status attribution: kit defect / missing configuration or permission / explicit service refusal / not tested.
"""
from __future__ import annotations

import base64
import json
import secrets
import socket
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from ..azcli import Az
from ..config import REPO_ROOT, KitConfig
from ..ledger import Ledger
from ..redact import redact_text
from ..results import CheckResult, Evidence, ExecutionContext, RunRecorder, Status
from . import classify, phase_b
from .profile import LabProfile

RUNNER_PY = "/opt/hapq/venv/bin/python /opt/hapq/kit/tools/vnet_runner/runner.py"
LOCKS = "agents/locks"
BUSY = "Run command extension execution is in progress"
RUN_COMMAND_MAX_S = 90 * 60  # Azure run-command (v1) hard limit
DISPATCH = REPO_ROOT / "results" / "runner-dispatch.jsonl"


class RunnerBusy(RuntimeError):
    """The runner VM is held by a command that is unknown or past its deadline: hand back, never kill it."""


def dispatch_log(event: str, did: str, label: str, timeout: int, **kw):
    DISPATCH.parent.mkdir(parents=True, exist_ok=True)
    with DISPATCH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": event, "id": did, "label": label, "timeout": timeout, "t": time.time(),
                             "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **kw}) + "\n")


def busy_holder(exclude: str = "") -> dict | None:
    """Last dispatched command with no recorded end (e.g. left by an interrupted session), with its deadline."""
    if not DISPATCH.exists():
        return None
    open_: dict[str, dict] = {}
    for line in DISPATCH.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["event"] == "start":
            open_[e["id"]] = e
        else:
            open_.pop(e["id"], None)
    open_.pop(exclude, None)
    if not open_:
        return None
    h = max(open_.values(), key=lambda e: e["t"])
    return {**h, "deadline": h["t"] + min(h["timeout"], RUN_COMMAND_MAX_S) + 120}


class Private:
    def __init__(self, cfg: KitConfig, az: Az, ledger: Ledger, run_id: str, log=print):
        self.cfg, self.az, self.ledger, self.run_id, self.log = cfg, az, ledger, run_id, log
        self.p = LabProfile.load("B")
        self.x = json.loads((REPO_ROOT / "config" / "lab-b-extra.local.json").read_text(encoding="utf-8"))
        self.ep1, self.ep2 = self.p.project_endpoints
        self.host = f"{self.p.account_name}.services.ai.azure.com"
        self.results: list[CheckResult] = []
        self.state: dict = {}

    # ---------------------------------------------------------------- plumbing
    def record(self, sid, criterion, status, observed, point="in-vnet-runner", **details):
        r = CheckResult(sid, criterion, sid, Status(status), Evidence.OBSERVED, details.pop("expected", ""),
                        redact_text(observed), region=self.p.region, environment="B", details=details)
        r.details["execution_point"] = point
        self.results.append(r)
        self.log(f"  {sid} [{point}] {r.status.value}: {r.observed[:160]}")
        return r

    def runner(self, script: str, timeout: int = 3600, label: str = "script") -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8", newline="\n") as fh:
            fh.write(script)
            path = fh.name
        try:
            while True:
                did = secrets.token_hex(4)
                dispatch_log("start", did, label, timeout)
                res = phase_b.run_on_runner(self.cfg, self.az, self.x["runner_vm"], f"@{path}", timeout=timeout)
                busy = not res["ok"] and BUSY in (res.get("stderr") or "")
                dispatch_log("busy" if busy else "end", did, label, timeout, ok=res["ok"])
                if not busy:
                    return res
                holder = busy_holder(exclude=did)
                if holder is None:
                    raise RunnerBusy("the runner VM is busy with a command this kit did not dispatch (or not recorded): "
                                     "not waiting, not stopping it; hand back to the operator")
                left = holder["deadline"] - time.time()
                if left <= 0:
                    raise RunnerBusy(f"command '{holder['label']}' (dispatched {holder['at']}) is past its deadline "
                                     "and still holds the VM: considered stuck; not stopped; hand back to the operator")
                self.log(f"  runner busy with '{holder['label']}' dispatched {holder['at']} "
                         f"(within budget, {int(left // 60)} min left); waiting 60 s")
                time.sleep(60)
        finally:
            Path(path).unlink(missing_ok=True)

    def rr(self, cmd: str, args: dict, timeout: int = 3600) -> dict:
        b64 = base64.b64encode(json.dumps(args).encode()).decode()
        res = self.runner(f"cd /opt/hapq/kit && {RUNNER_PY} {cmd} {b64}", timeout=timeout, label=cmd)
        line = next((l for l in reversed(res["stdout"].splitlines()) if l.startswith("{")), None)
        if not res["ok"] or line is None:
            return {"ok": False, "error": "run-command failed or no JSON: " + (res["stderr"] or res["stdout"])[-600:]}
        return json.loads(line)

    def ready(self, wait_s: int = 900) -> bool:
        deadline = time.time() + wait_s
        while time.time() < deadline:
            res = self.runner("test -f /opt/hapq/ready && echo READY || echo WAIT", timeout=300)
            if "READY" in res.get("stdout", ""):
                return True
            time.sleep(30)
        return False

    def upload_kit(self) -> bool:
        bundle = phase_b.runner_bundle()
        script = (f"mkdir -p /opt/hapq/kit && echo '{bundle}' | base64 -d > /tmp/kit.tgz && "
                  "rm -rf /opt/hapq/kit/* && tar -xzf /tmp/kit.tgz -C /opt/hapq/kit && echo UPLOADED")
        return "UPLOADED" in self.runner(script, timeout=600).get("stdout", "")

    def ledger_agent(self, endpoint: str, name: str):
        aid = f"{endpoint}|{name}"
        if aid not in {e.id for e in self.ledger.owned() if e.kind == "agent"}:
            self.ledger.created("agent", aid, name, "B", self.run_id, account_id=self.p.account_id,
                                project=endpoint.rsplit("/", 1)[-1])

    def deploy_code(self, endpoint, name, src, lock, env):
        self.ledger_agent(endpoint, name)
        return self.rr("deploy-code", {"endpoint": endpoint, "agent": name, "src": src, "lock": lock, "env": env})

    def invoke(self, endpoint, name, text, **kw):
        return self.rr("invoke", {"endpoint": endpoint, "agent": name, "text": text, **kw})

    def token(self, scope: str) -> str:
        from azure.identity import AzureCliCredential
        return AzureCliCredential(process_timeout=60).get_token(scope).token

    # ---------------------------------------------------------------- scenarios
    def s21_dns(self):
        hosts = [self.host, f"{self.p.account_name}.cognitiveservices.azure.com", f"{self.p.account_name}.openai.azure.com",
                 f"{self.x['storage']}.blob.core.windows.net", self.x["acr_login_server"]]
        r = self.rr("dns", {"hosts": hosts})
        if not r.get("ok"):
            return self.record("S21-DNS-PRIVATE", "C02.c", Status.NOT_TESTED, "kit/runner error: " + r.get("error", ""))
        private = {h: v["private_10_42"] for h, v in r["dns"].items()}
        public_view = {}
        for h in hosts:  # workstation view of the same names (expected: public addresses)
            try:
                ips = sorted({i[4][0] for i in socket.getaddrinfo(h, 443)})
            except OSError:
                ips = []
            public_view[h] = not any(ip.startswith("10.") for ip in ips)
        ok = all(private.values())
        return self.record("S21-DNS-PRIVATE", "C02.c", Status.PASS if ok else Status.FAIL,
                           f"in-VNet resolution to 10.42.x for {sum(private.values())}/{len(hosts)} names; "
                           f"workstation resolves them to non-private addresses for {sum(public_view.values())}/{len(hosts)}",
                           private_in_vnet=private, public_from_workstation=public_view)

    def s20_public(self):
        import httpx
        tok = self.token("https://ai.azure.com/.default")
        try:
            r = httpx.get(f"{self.ep1}/agents?api-version=v1", headers={"Authorization": f"Bearer {tok}"}, timeout=60)
            code, body = r.status_code, r.text[:400]
        except Exception as exc:
            code, body = None, f"{type(exc).__name__}"
        st, msg = classify.network_refusal(code, body)
        return self.record("S20-PUBLIC-DENIED-WITH-TOKEN", "C02.a", st,
                           f"public internet, valid Entra token with Foundry User: {msg}", point="workstation-public",
                           http=code, body_excerpt=body[:300])

    def s01_minimal(self):
        d = self.deploy_code(self.ep1, "hapq-b-min", "agents/minimal", f"{LOCKS}/minimal.requirements.txt",
                             {"AZURE_AI_MODEL_DEPLOYMENT_NAME": self.p.model_deployment})
        if not d.get("ok"):
            return self.record("S01-GA-CREATE", "C01", Status.NOT_TESTED, "kit/runner error: " + d.get("error", ""))
        self.state["min"] = d
        st, msg = classify.version_outcome(d["history"], d["final"], d["error"])
        self.record("S01-GA-CREATE", "C01", st, msg + " (from inside the VNet, account with public access disabled)",
                    version=d["version"], status_history=d["history"])
        self.record("S09-OBS-STATUS", "C07", Status.PASS if d["history"] else Status.FAIL,
                    f"{len(d['history'])} status read(s) from the VNet, final '{d['final']}'")
        if st is Status.NOT_TESTED:  # kit defect: says nothing about the subnet
            return self.record("S25-CLASSA-INJECTION", "C02.e", Status.NOT_TESTED, "prerequisite S01 (kit defect)",
                               point="workstation-public")
        self.record("S25-CLASSA-INJECTION", "C02.e", Status.PASS if d["final"] == "active" else Status.FAIL,
                    f"network-injected account on delegated subnet {self.x['agent_subnet']} (class A); hosted agent "
                    f"version '{d['final']}'", point="workstation-public")

    def s02_invoke(self):
        n1, n2 = secrets.token_hex(6), secrets.token_hex(6)
        a = self.invoke(self.ep1, "hapq-b-min", f"NONCE:{n1} Say hello in five words.")
        if not a.get("ok"):
            return self.record("S02-INVOKE", "C02.c", Status.FAIL, "invoke from the VNet failed: " + a.get("error", ""))
        b = self.invoke(self.ep1, "hapq-b-min", f"NONCE:{n2} Say goodbye in five words.", session=a.get("session"))
        self.state.update(session=a.get("session"), response_id=a.get("id"))
        ok = n1 in a.get("text", "") and n2 in b.get("text", "")
        return self.record("S02-INVOKE", "C02.c", Status.PASS if ok else Status.FAIL,
                           f"2 calls from the VNet; nonce present in both answers={ok}; same session={b.get('session') == a.get('session')}",
                           answers=[a.get("text"), b.get("text")], usage=[a.get("usage"), b.get("usage")])

    def s10_logs(self):
        if not self.state.get("session"):
            return self.record("S10-OBS-LOGS", "C07", Status.NOT_TESTED, "prerequisite S02 (no session)")
        r = self.rr("logs", {"endpoint": self.ep1, "agent": "hapq-b-min", "version": self.state["min"]["version"],
                             "session": self.state["session"]})
        return self.record("S10-OBS-LOGS", "C07", Status.PASS if r.get("count") else Status.FAIL,
                           f"{r.get('count')} log line(s) read from the VNet for the tested session")

    def s08_langgraph(self):
        d = self.deploy_code(self.ep1, "hapq-b-lg", "agents/langgraph", f"{LOCKS}/langgraph.requirements.txt",
                             {"FOUNDRY_MODEL_NAME": self.p.model_deployment})
        if d.get("final") != "active":
            return self.record("S08-LANGGRAPH", "C08", Status.FAIL if d.get("final") == "failed" else Status.NOT_TESTED,
                               f"version {d.get('final')}: {d.get('error') or d.get('error', '')}")
        n = secrets.token_hex(6)
        a = self.invoke(self.ep1, "hapq-b-lg", f"NONCE:{n} Say hello in five words.")
        ok = n in a.get("text", "")
        return self.record("S08-LANGGRAPH", "C08", Status.PASS if ok else Status.FAIL,
                           f"LangGraph agent active in the private account; nonce in answer={ok}", answer=a.get("text"))

    def s22_outbound(self):
        n = secrets.token_hex(6)
        put = self.rr("blob-put", {"account": self.x["storage"], "container": "hapq", "blob": f"{n}.txt", "content": f"PRIVATE-{n}"})
        if not put.get("ok"):
            return self.record("S22-OUTBOUND-PRIVATE-STORAGE", "C02.d", Status.NOT_TESTED, "kit/runner error writing blob: " + put.get("error", ""))
        d = self.deploy_code(self.ep1, "hapq-b-out", "agents/outbound", None, {})
        if d.get("final") != "active":
            return self.record("S22-OUTBOUND-PRIVATE-STORAGE", "C02.d", Status.FAIL, f"outbound agent version {d.get('final')}: {d.get('error')}")
        pid = self.rr("agent-principal", {"endpoint": self.ep1, "agent": "hapq-b-out"})
        if not pid.get("principal"):
            return self.record("S22-OUTBOUND-PRIVATE-STORAGE", "C02.d", Status.NOT_TESTED, "agent identity not readable: " + str(pid)[:200])
        self.grant_blob_reader(pid["principal"])
        reply, attempts = "", 0
        for attempts in range(1, 4):  # RBAC propagation: bounded retries, same request
            time.sleep(60)
            reply = self.invoke(self.ep1, "hapq-b-out", f"READ {self.x['storage']}/hapq/{n}.txt").get("text", "")
            if f"PRIVATE-{n}" in reply:
                break
        ws = self.workstation_blob_read(n)
        ok = f"PRIVATE-{n}" in reply
        return self.record("S22-OUTBOUND-PRIVATE-STORAGE", "C02.d", Status.PASS if ok else Status.FAIL,
                           f"hosted agent read the private blob with its own identity={ok} after {attempts} attempt(s); "
                           f"same blob from the public internet: {ws}", agent_reply=reply[:200])

    def grant_blob_reader(self, principal: str):
        scope = f"{self.cfg.rg_scope}/providers/Microsoft.Storage/storageAccounts/{self.x['storage']}"
        self.grant(principal, "Storage Blob Data Reader", scope, "blob-reader-hapq-b-out")

    def grant(self, principal: str, role: str, scope: str, label: str) -> bool:
        """Data-plane role for a kit-created agent identity on a kit-created resource (never tenant-wide)."""
        res = self.az.json("role", "assignment", "create", "--assignee-object-id", principal,
                           "--assignee-principal-type", "ServicePrincipal", "--role", role,
                           "--scope", scope, scoped=False)
        if res.ok:
            self.ledger.created("role", res.json()["id"], label, "B", self.run_id)
        return res.ok

    def workstation_blob_read(self, n: str) -> str:
        import httpx
        tok = self.token("https://storage.azure.com/.default")
        try:
            r = httpx.get(f"https://{self.x['storage']}.blob.core.windows.net/hapq/{n}.txt",
                          headers={"Authorization": f"Bearer {tok}", "x-ms-version": "2023-11-03"}, timeout=60)
            code = "AuthorizationFailure" if "AuthorizationFailure" in r.text else r.text[:80]
            return f"HTTP {r.status_code} {code}"
        except Exception as exc:
            return f"{type(exc).__name__}"

    def s23_s24_telemetry(self):
        rid, sid = self.state.get("response_id"), self.state.get("session")
        if not rid:
            return self.record("S23-TELEMETRY-INGEST-PRIVATE", "C02.d", Status.NOT_TESTED, "prerequisite S02")
        q = (f"union requests, dependencies, traces | where timestamp > ago(2h) "
             f"| summarize n=countif(* has '{rid}' or * has '{sid}')")
        r = self.rr("trace-query", {"app_id": self.p.app_insights_app_id, "query": q, "wait_s": self.cfg.limits.trace_wait_s})
        n = (r.get("row") or [0])[0] if r.get("status") == 200 else 0
        self.record("S23-TELEMETRY-INGEST-PRIVATE", "C02.d", Status.PASS if n else Status.NOT_TESTED,
                    f"{n} row(s) correlated with the tested request, queried from the VNet (ingestion public access "
                    "disabled, data through AMPLS)" if r.get("status") == 200 else f"query from the VNet failed: {r}")
        runner_ok = r.get("status") == 200
        ws = self.az.json("monitor", "app-insights", "query", "--apps", self.p.app_insights_app_id,
                          "--analytics-query", "requests | take 1", scoped=False)
        # control in the same run: the same identity and query against the PUBLIC-query App Insights of environment A
        control = self.az.json("monitor", "app-insights", "query", "--apps", LabProfile.load("A").app_insights_app_id,
                               "--analytics-query", "requests | take 1", scoped=False)
        refused = (not ws.ok) and control.ok
        self.record("S24-TELEMETRY-QUERY-PRIVATE", "C02.c", Status.PASS if runner_ok else Status.FAIL,
                    f"telemetry query from the VNet: HTTP {r.get('status')}")
        return self.record("S24-TELEMETRY-QUERY-PRIVATE", "C02.c",
                           Status.PASS if refused else (Status.FAIL if ws.ok else Status.NOT_TESTED),
                           f"same query from the public internet: {'refused' if not ws.ok else 'ALLOWED'} "
                           f"({redact_text((ws.stderr or '')[-120:]).strip()}); control with the same identity on the "
                           f"public-query App Insights of environment A: {'succeeded' if control.ok else 'failed'} "
                           "(rights identical: refusal comes from publicNetworkAccessForQuery=Disabled)",
                           point="workstation-public")


    def push_image(self, context: str, repo: str) -> dict:
        import hashlib
        root = REPO_ROOT / context
        tag = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(root.glob("*")) if p.is_file())).hexdigest()[:12]
        return self.rr("docker-push", {"login_server": self.x["acr_login_server"], "repo": f"{repo}:{tag}",
                                       "context": context}, timeout=3000)

    def s07_mcp(self):
        img = self.push_image("tools/mcp_whoami", "mcp-whoami")
        if not img.get("ok"):
            return self.record("S07-TOOL-AGENT-IDENTITY", "C04.b", Status.NOT_TESTED,
                               f"kit/runner: MCP image build or push to the private ACR failed: {img}")
        self.p = phase_b.deploy(self.cfg, self.az, self.ledger, self.cfg_suffix(), self.run_id, deploy_mcp=True,
                                mcp_image=img["image"], log=self.log)
        url = self.p.mcp_url
        health = {}
        for _ in range(12):  # wake the scaled-to-zero tool, bounded
            health = self.rr("http", {"url": f"{url}/healthz"})
            if health.get("status") == 200:
                break
            time.sleep(15)
        if health.get("status") != 200:
            return self.record("S07-TOOL-AGENT-IDENTITY", "C04.b", Status.BLOCKED, f"private MCP tool not ready: {health}")
        no_tok = self.rr("http", {"url": f"{url}/mcp", "method": "POST", "json": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}})
        self.record("S14-MCP-NO-TOKEN-401", "C04.b", *classify.expected_refusal(no_tok.get("status"), 401))
        try:
            socket.getaddrinfo(url.split("//", 1)[1], 443)
            public = "resolvable from the public internet"
        except OSError:
            public = "not resolvable from the public internet"
        self.state["mcp_public"] = public
        # connection (ARM) + toolbox (data plane, from the VNet) in the private proj1
        conn_id = f"{self.p.account_id}/projects/proj1/connections/hapq-mcp"
        body = {"properties": {"authType": "AgenticIdentityToken", "category": "RemoteTool", "target": f"{url}/mcp",
                               "audience": self.p.mcp_audience, "Credentials": {}, "metadata": {}}}
        put = self.az.json("rest", "--method", "PUT", "--url",
                           f"https://management.azure.com{conn_id}?api-version=2025-04-01-preview",
                           "--headers", "Content-Type=application/json", "--body", json.dumps(body), scoped=False)
        if not put.ok:
            return self.record("S07-TOOL-AGENT-IDENTITY", "C04.b", Status.NOT_TESTED,
                               "connection creation failed (ARM): " + redact_text((put.stderr or "")[-300:]))
        self.ledger.created("connection", conn_id, "hapq-mcp", "B", self.run_id, account_id=self.p.account_id)
        tb = self.rr("toolbox", {"endpoint": self.ep1, "name": "hapq-tools", "server_url": f"{url}/mcp", "connection": "hapq-mcp"})
        if not tb.get("ok"):
            return self.record("S07-TOOL-AGENT-IDENTITY", "C04.b", Status.NOT_TESTED, f"toolbox creation from the VNet failed: {tb}")
        self.ledger.created("toolbox", f"{self.ep1}|hapq-tools", "hapq-tools", "B", self.run_id, account_id=self.p.account_id)
        d = self.deploy_code(self.ep1, "hapq-b-mcp", "agents/toolbox", f"{LOCKS}/minimal.requirements.txt",
                             {"AZURE_AI_MODEL_DEPLOYMENT_NAME": self.p.model_deployment, "TOOLBOX_NAME": "hapq-tools"})
        if d.get("final") != "active":
            return self.record("S07-TOOL-AGENT-IDENTITY", "C04.b", Status.NOT_TESTED, f"test agent not ready: {d}")
        n = secrets.token_hex(6)
        a = self.invoke(self.ep1, "hapq-b-mcp", f"NONCE:{n} CALL_WHOAMI {n}")
        m = self.rr("identity-match", {"endpoint": self.ep1, "agent": "hapq-b-mcp", "audience": self.p.mcp_audience,
                                       "mcp_url": url, "nonce": n})
        if m.get("match_instance"):
            st, msg = Status.PASS, "private MCP tool validated a token whose oid equals the agent instance identity"
        elif not m.get("expected_available"):
            st, msg = Status.NOT_TESTED, "expected agent identity not available"
        else:
            st, msg = Status.FAIL, f"{m.get('calls')} call(s) recorded; principal is not the agent instance identity"
        return self.record("S07-TOOL-AGENT-IDENTITY", "C04.b", st, msg + f"; tool {public}",
                           calls=m.get("calls"), idtyp=m.get("idtyp"), match_blueprint=m.get("match_blueprint"),
                           answer=(a.get("text") or "")[:200])

    def fetch(self, remote: str, name: str) -> Path:
        """Copy a (redacted) file saved on the VM to results/diag/<run_id>/<name>, in chunks (run-command ~4 KB limit)."""
        dest = REPO_ROOT / "results" / "diag" / self.run_id / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        parts, off, total = [], 0, None
        while total is None or off < total:
            c = self.rr("chunk", {"path": remote, "offset": off, "length": 2500})
            if not c.get("ok"):
                parts.append(f"\n[fetch error at {off}: {c.get('error')}]")
                break
            total, data = c["total"], c["data"]
            parts.append(data)
            off += len(data)
            if not data:
                break
        dest.write_text(redact_text("".join(parts)), encoding="utf-8")
        return dest

    def mcp_diag(self):
        """Diagnostic only (no verdict): one call to the existing S07 agent, then its full session log, untruncated."""
        n = secrets.token_hex(6)
        a = self.invoke(self.ep1, "hapq-b-mcp", f"NONCE:{n} CALL_WHOAMI {n}")
        self.log("  diag invoke: " + redact_text(json.dumps({k: a.get(k) for k in ("ok", "status", "error", "status_code", "text")}))[:900])
        ver = self.rr("agent-version", {"endpoint": self.ep1, "agent": "hapq-b-mcp"})
        if not (a.get("session") and ver.get("version")):
            return self.log(f"  diag: no session/version ({ver})")
        r = self.rr("logs", {"endpoint": self.ep1, "agent": "hapq-b-mcp", "version": ver["version"], "session": a["session"],
                             "grep": ["McpError"], "width": 300, "max": 2, "wait_s": 60, "save": "/opt/hapq/out/mcp-session.log"})
        self.log(f"  diag logs: {r.get('count')} line(s)")
        path = self.fetch("/opt/hapq/out/mcp-session.log", "mcp-session.log")
        self.log(f"  diag full session log saved: {path.relative_to(REPO_ROOT)}")

    def netdiag(self):
        """Microsoft diagnostic agent (foundry-samples, ZIP mode: no ACR, no model) probing MCP and ACR from the
        Foundry runtime. Shows what the agent sandbox reaches; does NOT prove the Toolbox proxy path."""
        acr = self.x["acr_login_server"]
        mcp_host = self.p.mcp_url.split("//", 1)[1].rstrip("/")
        self.ledger_agent(self.ep1, "hapq-b-netdiag")
        d = self.rr("deploy-code", {"endpoint": self.ep1, "agent": "hapq-b-netdiag", "src": "agents/netdiag", "lock": None,
                                    "env": {}, "protocol": "invocations", "recursive": True})
        self.log(f"  netdiag version {d.get('version')}: {d.get('final')} {str(d.get('error') or '')[:300]}")
        if d.get("final") != "active":
            return
        body = {"hosts": [mcp_host, acr, f"{acr.split('.')[0]}.{self.p.region}.data.azurecr.io"], "public_hosts": [],
                "dns_attempts": 3, "direct_targets": [f"{self.x.get('aca_static_ip', '10.42.5.144')}:443"],
                "include_env_dump": False, "include_evidence": True}
        r = self.rr("invocations", {"endpoint": self.ep1, "agent": "hapq-b-netdiag", "body": body,
                                    "save": "/opt/hapq/out/netdiag.json", "timeout_s": 300})
        self.log(f"  netdiag invoke: {r}")
        path = self.fetch("/opt/hapq/out/netdiag.json", "netdiag.json")
        self.log(f"  netdiag report saved: {path.relative_to(REPO_ROOT)}")

    def acr_bypass(self, enable: bool) -> str:
        name = self.x["acr_login_server"].split(".")[0]
        self.az.json("acr", "update", "-n", name, "--allow-trusted-services", "true" if enable else "false", scoped=False)
        got = self.az.json("acr", "show", "-n", name, "--query", "{b:networkRuleBypassOptions,p:publicNetworkAccess}", scoped=False)
        return json.dumps(got.json() if got.ok else {"error": (got.stderr or "")[-200:]})

    def s26_bypass(self):
        """One retry of S26 with the trusted-services exception (operator-approved), then restore None in all cases."""
        try:
            state = self.acr_bypass(True)
            self.log(f"  ACR exception set: {state}")
            time.sleep(60)
            img = self.push_image("agents/container", "hapq-agent")  # same content -> same tag (no rebuild of a new image)
            if not img.get("ok"):
                return self.record("S26-PRIVATE-ACR-IMAGE-BYPASS", "C02.f", Status.NOT_TESTED, f"image push failed: {img}")
            d = self.rr("deploy-image", {"endpoint": self.ep1, "agent": "hapq-b-img", "image": img["image"],
                                         "env": {"AZURE_AI_MODEL_DEPLOYMENT_NAME": self.p.model_deployment}})
            if d.get("final") != "active":
                return self.record("S26-PRIVATE-ACR-IMAGE-BYPASS", "C02.f", Status.FAIL,
                                   f"with network exception (AzureServices), public access disabled: version {d.get('final')}: "
                                   f"{d.get('error') or d}", acr_state=state)
            n = secrets.token_hex(6)
            a = self.invoke(self.ep1, "hapq-b-img", f"NONCE:{n} Say hello in five words.")
            ok = n in (a.get("text") or "")
            return self.record("S26-PRIVATE-ACR-IMAGE-BYPASS", "C02.f", Status.PASS if ok else Status.FAIL,
                               "with network exception (networkRuleBypassOptions=AzureServices, public access disabled): "
                               f"image-based version active, answered={ok}. Does not prove a pull through the private endpoint",
                               acr_state=state, version=d.get("version"), answer=(a.get("text") or "")[:200])
        finally:
            restored = self.acr_bypass(False)
            self.log(f"  ACR exception removed: {restored}")
            self.state["acr_restored"] = restored

    def s07_retry(self, attempts: int = 5):
        """S07 replayed identically N times (sample 19 known issue: DataProxy on one of two scale units, round robin).
        One success is enough to classify the failure as intermittent. The original S07 result is not overwritten."""
        runs = []
        for i in range(1, attempts + 1):
            n = secrets.token_hex(6)
            r = self.rr("s07-attempt", {"endpoint": self.ep1, "agent": "hapq-b-mcp", "text": f"NONCE:{n} CALL_WHOAMI {n}",
                                        "save": f"/opt/hapq/out/s07-retry-{i}.log"})
            r["attempt"], r["nonce_used"] = i, True
            if r.get("ok") and not r.get("tools_list_error") and r.get("text"):
                m = self.rr("identity-match", {"endpoint": self.ep1, "agent": "hapq-b-mcp", "audience": self.p.mcp_audience,
                                               "mcp_url": self.p.mcp_url, "nonce": n})
                r["tool_calls"], r["match_instance"] = m.get("calls"), m.get("match_instance")
            r["success"] = bool(r.get("match_instance"))
            runs.append(r)
            self.log(f"  S07 retry {i}/{attempts} at {r.get('at')}: success={r['success']} status={r.get('status')} "
                     f"tools_list_error={r.get('tools_list_error')} response_id={r.get('response_id')}")
        dest = REPO_ROOT / "results" / "diag" / self.run_id / "s07-retry.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(redact_text(json.dumps(runs, indent=1)), encoding="utf-8")
        ok = sum(r["success"] for r in runs)
        same = len({(r.get("mcp_error") or "")[:160] for r in runs if not r["success"]})
        return self.record("S07-PRIVATE-MCP-RETRY", "C04.b", Status.PASS if ok else Status.FAIL,
                           f"{ok}/{attempts} identical attempt(s) succeeded"
                           + ("" if ok else f"; every failure is a toolbox tools/list error ({same} distinct message(s))")
                           + f"; evidence {dest.relative_to(REPO_ROOT).as_posix()}",
                           attempts=[{k: r.get(k) for k in ("attempt", "at", "success", "status", "response_id", "request_id",
                                                             "session", "trace_id", "region", "error")} for r in runs])

    def align_min_replicas(self) -> str:
        """foundry-samples 19 runs the private MCP app with --min-replicas 1: reference alignment, not a fix."""
        app = self.p.mcp_url.split("//", 1)[1].split(".", 1)[0]
        self.az.json("containerapp", "update", "-g", self.cfg.resource_group, "-n", app, "--min-replicas", "1", scoped=False)
        got = self.az.json("containerapp", "show", "-g", self.cfg.resource_group, "-n", app,
                           "--query", "{min:properties.template.scale.minReplicas,run:properties.runningStatus}", scoped=False)
        state = json.dumps(got.json() if got.ok else {"error": (got.stderr or "")[-200:]})
        self.log(f"  MCP app min-replicas aligned on sample 19: {state}")
        return state

    def s07_control(self):
        """Sample 19 test script, unmodified: prompt agent -> Tool Server -> DataProxy -> OUR private MCP tool.
        Qualifies the gap with the hosted-agent -> Toolbox path; does not replace S07."""
        replicas = self.align_min_replicas()
        self.ledger_agent(self.ep1, "mcp-tool-test")
        env = {"PROJECT_ENDPOINT": self.ep1, "MODEL_NAME": self.p.model_deployment,
               "MCP_SERVER_PRIVATE": f"{self.p.mcp_url}/mcp", "MCP_SERVER_PUBLIC": f"{self.p.mcp_url}/mcp"}
        r = self.rr("run-sample", {"script": "tools/sample19/test_mcp_tools_agents_v2.py",
                                   "args": ["--test", "private", "--retry", "3", "--keep-agent"], "env": env,
                                   "save": "/opt/hapq/out/sample19-mcp.txt", "timeout_s": 1500}, timeout=1700)
        path = self.fetch("/opt/hapq/out/sample19-mcp.txt", "sample19-mcp.txt")
        text = path.read_text(encoding="utf-8")
        passed = "agent_private: ✓ PASSED" in text or "agent_private: ✓ PASSED" in text
        dns = "could not be resolved" in text or "Name or service not known" in text
        reached = "customer-managed downstream service responded with 401" in text
        if passed:
            verdict, msg = Status.PASS, "prompt agent reached the private MCP tool through the Tool Server/DataProxy"
        elif dns:
            verdict, msg = Status.FAIL, "prompt agent path fails too: the DataProxy does not resolve the private MCP FQDN"
        elif reached:
            verdict, msg = Status.FAIL, ("prompt agent path REACHED the private MCP tool (the tool answered an auth "
                                         "refusal: it requires an Entra token and MCPTool sends none)")
        else:
            verdict, msg = Status.FAIL, "prompt agent path failed; see the script output for the error"
        return self.record("S07-CONTROL-PROMPT-AGENT", "C04.b", verdict,
                           msg + f"; sample 19 script exit={r.get('exit_code')}; evidence {path.relative_to(REPO_ROOT).as_posix()}",
                           min_replicas=replicas, highlights=r.get("highlights"))

    def s26_tls_slow(self):
        """Same probe with a 20 s TLS timeout: separates a slow handshake from a blocked one."""
        self.state["tls_timeout"] = 20
        return self.s26_tls()

    def s26_tls(self):
        """S26 diagnosis (no image deployment): same hosts probed from the VNet VM and from the hosted-agent runtime
        (Microsoft diagnostic agent, ZIP mode, no model). Hypothesis tested: name-based egress filtering in the runtime."""
        acr = self.x["acr_login_server"]
        name = acr.split(".")[0]
        hosts = [acr, f"{name}.{self.p.region}.data.azurecr.io", f"{self.x['storage']}.blob.core.windows.net",
                 self.host, f"{self.p.account_name}.cognitiveservices.azure.com",
                 self.p.mcp_url.split("//", 1)[1].rstrip("/")]
        paths = {acr: "/v2/", f"{name}.{self.p.region}.data.azurecr.io": "/v2/"}
        vm = self.rr("tls-compare", {"hosts": hosts, "paths": paths, "save": "/opt/hapq/out/tls-vm.json"})
        self.log("  VM view: " + redact_text(json.dumps(vm.get("results")))[:1800])
        self.fetch("/opt/hapq/out/tls-vm.json", "tls-vm.json")
        body = {"hosts": hosts, "public_hosts": [], "dns_attempts": 2, "include_env_dump": False, "include_evidence": True,
                "tcp_timeout_sec": int(self.state.get("tls_timeout", 5)), "http_timeout_sec": 20}
        r = self.rr("invocations", {"endpoint": self.ep1, "agent": "hapq-b-netdiag", "body": body,
                                    "save": "/opt/hapq/out/netdiag-s26.json", "timeout_s": 300})
        self.log(f"  runtime view (netdiag) invoke: {r}")
        path = self.fetch("/opt/hapq/out/netdiag-s26.json", "netdiag-s26.json")
        rep = json.loads(path.read_text(encoding="utf-8") or "{}")
        tls = {}
        for x in rep.get("results", []):
            if x.get("probe") == "conn.tls":
                tls[(x.get("target") or {}).get("host")] = (x.get("evidence") or {}).get("status")
        vm_tls = {v["host"]: ("ok" if v.get("tls") else "FAIL") for v in vm.get("results", [])}
        rows = {h: {"vm": vm_tls.get(h), "runtime": tls.get(h)} for h in hosts}
        self.log("  TLS comparison: " + redact_text(json.dumps(rows)))
        diff = [h for h, v in rows.items() if v["vm"] == "ok" and (v["runtime"] or "").upper() != "OK"]
        return self.record("S26-DIAG-TLS", "C02.f", Status.FAIL if diff else Status.PASS,
                           f"TLS from the VNet VM vs from the hosted-agent runtime: {len(diff)} host(s) reachable from "
                           f"the VM but not from the runtime ({', '.join(h.split('.')[0] + '...' for h in diff)}). "
                           "Diagnostic only: not an image deployment",
                           comparison=rows)

    def s07_std(self):
        """S07 on proj3 (Standard agent setup, BYO Cosmos/Search/Storage): Learn states private MCP requires it.
        1) sample 19 control (prompt agent -> Tool Server), 2) hosted agent -> Toolbox -> private MCP with exact
        agent identity. Same private MCP tool, same VNet, same DNS as the Basic-setup runs."""
        import tempfile
        ep3 = self.ep1.rsplit("/", 1)[0] + "/proj3"
        url = self.p.mcp_url
        # 1) control
        self.ledger_agent(ep3, "mcp-tool-test")
        env = {"PROJECT_ENDPOINT": ep3, "MODEL_NAME": self.p.model_deployment,
               "MCP_SERVER_PRIVATE": f"{url}/mcp", "MCP_SERVER_PUBLIC": f"{url}/mcp"}
        r = self.rr("run-sample", {"script": "tools/sample19/test_mcp_tools_agents_v2.py",
                                   "args": ["--test", "private", "--retry", "3", "--keep-agent"], "env": env,
                                   "save": "/opt/hapq/out/sample19-mcp-std.txt", "timeout_s": 1500}, timeout=1700)
        path = self.fetch("/opt/hapq/out/sample19-mcp-std.txt", "sample19-mcp-std.txt")
        text = path.read_text(encoding="utf-8")
        passed = "agent_private: ✓ PASSED" in text
        dns = "could not be resolved" in text or "Name or service not known" in text
        # the platform's own statement that the DataProxy reached the tool (not the script's direct HTTP test)
        auth = "customer-managed downstream service responded with 401" in text
        msg = ("prompt agent reached the private MCP tool" if passed else
               "DataProxy does not resolve the private MCP FQDN (same as Basic setup)" if dns else
               "DataProxy REACHED the private tool: platform error says the customer-managed downstream service responded with 401 (the sample MCPTool sends no token; the tool requires Entra)" if auth
               else "failed; see script output")
        self.record("S07-STD-CONTROL-PROMPT-AGENT", "C04.b", Status.PASS if passed else Status.FAIL,
                    f"proj3 (Standard setup): {msg}; script exit={r.get('exit_code')}; evidence {path.relative_to(REPO_ROOT).as_posix()}",
                    highlights=r.get("highlights"), reached_tool=bool(passed or (auth and not dns)))
        # 2) hosted agent -> Toolbox -> private MCP (connection + toolbox in proj3)
        conn_id = f"{self.p.account_id}/projects/proj3/connections/hapq-mcp-p3"  # names are account-wide
        body = {"properties": {"authType": "AgenticIdentityToken", "category": "RemoteTool", "target": f"{url}/mcp",
                               "audience": self.p.mcp_audience, "Credentials": {}, "metadata": {}}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(body, fh)
        try:
            put = self.az.json("rest", "--method", "PUT", "--url",
                               f"https://management.azure.com{conn_id}?api-version=2025-04-01-preview",
                               "--headers", "Content-Type=application/json", "--body", f"@{fh.name}", scoped=False)
        finally:
            Path(fh.name).unlink(missing_ok=True)
        if not put.ok:
            return self.record("S07-STD-TOOL-AGENT-IDENTITY", "C04.b", Status.NOT_TESTED,
                               "connection creation failed (ARM): " + redact_text((put.stderr or "")[-300:]))
        self.ledger.created("connection", conn_id, "hapq-mcp-p3", "B", self.run_id, account_id=self.p.account_id)
        tb = self.rr("toolbox", {"endpoint": ep3, "name": "hapq-tools", "server_url": f"{url}/mcp", "connection": "hapq-mcp-p3"})
        if not tb.get("ok"):
            return self.record("S07-STD-TOOL-AGENT-IDENTITY", "C04.b", Status.NOT_TESTED, f"toolbox creation failed: {tb}")
        self.ledger.created("toolbox", f"{ep3}|hapq-tools", "hapq-tools", "B", self.run_id, account_id=self.p.account_id)
        d = self.deploy_code(ep3, "hapq-b3-mcp", "agents/toolbox", f"{LOCKS}/minimal.requirements.txt",
                             {"AZURE_AI_MODEL_DEPLOYMENT_NAME": self.p.model_deployment, "TOOLBOX_NAME": "hapq-tools"})
        if d.get("final") != "active":
            return self.record("S07-STD-TOOL-AGENT-IDENTITY", "C04.b", Status.NOT_TESTED, f"test agent not ready: {d}")
        n = secrets.token_hex(6)
        a = self.rr("s07-attempt", {"endpoint": ep3, "agent": "hapq-b3-mcp", "text": f"NONCE:{n} CALL_WHOAMI {n}",
                                    "save": "/opt/hapq/out/s07-std.log"})
        m = self.rr("identity-match", {"endpoint": ep3, "agent": "hapq-b3-mcp", "audience": self.p.mcp_audience,
                                       "mcp_url": url, "nonce": n})
        if m.get("match_instance"):
            st, msg = Status.PASS, "private MCP tool validated a token whose oid equals the agent instance identity"
        elif not m.get("expected_available"):
            st, msg = Status.NOT_TESTED, "expected agent identity not available"
        else:
            st, msg = Status.FAIL, (f"{m.get('calls')} call(s) recorded; toolbox tools/list error: "
                                    f"{(a.get('mcp_error') or '')[-260:]}")
        return self.record("S07-STD-TOOL-AGENT-IDENTITY", "C04.b", st, "proj3 (Standard setup): " + msg,
                           calls=m.get("calls"), idtyp=m.get("idtyp"), match_blueprint=m.get("match_blueprint"),
                           response_id=a.get("response_id"), trace_id=a.get("trace_id"))

    def s26_std(self):
        """New hypothesis (observation-based, not documented): the Standard setup changes the platform path for tools
        (DataProxy resolved the private MCP only on proj3); test ONE image deployment on proj3, same image, AcrPull for
        the proj3 identity and the agent identity, no network exception."""
        ep3 = self.ep1.rsplit("/", 1)[0] + "/proj3"
        acr = f"{self.cfg.rg_scope}/providers/Microsoft.ContainerRegistry/registries/{self.x['acr_login_server'].split('.')[0]}"
        pj = self.az.json("rest", "--method", "get", "--url",
                          f"https://management.azure.com{self.p.account_id}/projects/proj3?api-version=2025-06-01", scoped=False)
        proj_mi = ((pj.json() or {}).get("identity") or {}).get("principalId") if pj.ok else None
        if proj_mi:
            self.grant(proj_mi, "AcrPull", acr, "acrpull-proj3")
        img = self.push_image("agents/container", "hapq-agent")  # same content -> same tag, no new image
        if not img.get("ok"):
            return self.record("S26-STD-PRIVATE-ACR-IMAGE", "C02.f", Status.NOT_TESTED, f"image push failed: {img}")
        self.ledger_agent(ep3, "hapq-b3-img")
        spec = {"endpoint": ep3, "agent": "hapq-b3-img", "image": img["image"],
                "env": {"AZURE_AI_MODEL_DEPLOYMENT_NAME": self.p.model_deployment}}
        first = self.rr("deploy-image", spec)  # creates the agent (and its identity); may fail before AcrPull
        if first.get("final") != "active":
            pid = self.rr("agent-principal", {"endpoint": ep3, "agent": "hapq-b3-img"})
            if pid.get("principal"):
                self.grant(pid["principal"], "AcrPull", acr, "acrpull-hapq-b3-img")
                time.sleep(90)
                d = self.rr("deploy-image", spec)
            else:
                d = first
        else:
            d = first
        if d.get("final") != "active":
            return self.record("S26-STD-PRIVATE-ACR-IMAGE", "C02.f", Status.FAIL,
                               f"proj3 (Standard setup), private ACR, no network exception: version {d.get('final')}: "
                               f"{d.get('error') or d}", first_attempt=str(first.get("error"))[:200])
        n = secrets.token_hex(6)
        a = self.invoke(ep3, "hapq-b3-img", f"NONCE:{n} Say hello in five words.")
        ok = n in (a.get("text") or "")
        return self.record("S26-STD-PRIVATE-ACR-IMAGE", "C02.f", Status.PASS if ok else Status.FAIL,
                           "proj3 (Standard setup): hosted agent version from an image in the private ACR (public access "
                           f"disabled, networkRuleBypassOptions None) is active and answered={ok}",
                           answer=(a.get("text") or "")[:200], first_attempt=str(first.get("final")))

    def s03_s15(self):
        """S03 (A2A card) and S15 (foreign-audience token refused by the private tool), both from the VNet."""
        d = self.deploy_code(self.ep1, "hapq-b-a2a", "agents/minimal", f"{LOCKS}/minimal.requirements.txt",
                             {"AZURE_AI_MODEL_DEPLOYMENT_NAME": self.p.model_deployment})
        if d.get("final") != "active":
            self.record("S03-A2A-CARD", "C03", Status.NOT_TESTED, f"test agent not ready: {d.get('final')} {d.get('error')}")
        else:
            r = self.rr("a2a-card", {"endpoint": self.ep1, "agent": "hapq-b-a2a"})
            ok = r.get("status") == 200 and "1.0" in (r.get("versions") or [])
            kit_error = not r.get("ok") and r.get("stage") != "enable"  # runner/harness error: never a verdict
            self.record("S03-A2A-CARD", "C03", Status.PASS if ok else Status.NOT_TESTED if kit_error else Status.FAIL,
                        f"from the VNet: a2a enabled on a hosted agent of the private account; card HTTP {r.get('status')}, "
                        f"protocol versions {r.get('versions')} {r.get('error') or ''}".strip())
        t = self.rr("http", {"url": f"{self.p.mcp_url}/mcp", "method": "POST",
                             "json": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, "scope": "https://ai.azure.com/.default"})
        return self.record("S15-MCP-WRONG-AUDIENCE-401", "C04.b", *classify.expected_refusal(t.get("status"), 401))

    def s26_image(self):
        img = self.push_image("agents/container", "hapq-agent")
        if not img.get("ok"):
            return self.record("S26-PRIVATE-ACR-IMAGE", "C02.f", Status.NOT_TESTED, f"kit/runner: image build/push failed: {img}")
        self.ledger_agent(self.ep1, "hapq-b-img")
        spec = {"endpoint": self.ep1, "agent": "hapq-b-img", "image": img["image"],
                "env": {"AZURE_AI_MODEL_DEPLOYMENT_NAME": self.p.model_deployment}}
        first = None
        pid = self.rr("agent-principal", {"endpoint": self.ep1, "agent": "hapq-b-img"})
        if not pid.get("principal"):
            # the agent (and its identity) only exists after a first version; that version is expected to fail
            first = self.rr("deploy-image", spec)
            pid = self.rr("agent-principal", {"endpoint": self.ep1, "agent": "hapq-b-img"})
        if not pid.get("principal"):
            return self.record("S26-PRIVATE-ACR-IMAGE", "C02.f", Status.NOT_TESTED, "agent identity not readable: " + str(pid)[:200])
        # Learn (deploy-hosted-agent-private-azure-container-registry): the per-agent identity pulls the image -> AcrPull
        acr = f"{self.cfg.rg_scope}/providers/Microsoft.ContainerRegistry/registries/{self.x['acr_login_server'].split('.')[0]}"
        self.grant(pid["principal"], "AcrPull", acr, "acrpull-hapq-b-img")
        time.sleep(90)  # RBAC propagation
        d = self.rr("deploy-image", spec)
        if first is not None:
            self.state["s26_first"] = {"final": first.get("final"), "error": str(first.get("error"))[:200]}
        if d.get("final") != "active":
            return self.record("S26-PRIVATE-ACR-IMAGE", "C02.f", Status.FAIL if d.get("final") == "failed" else Status.NOT_TESTED,
                               f"image-based version {d.get('final')}: {d.get('error') or d}")
        n = secrets.token_hex(6)
        a = self.invoke(self.ep1, "hapq-b-img", f"NONCE:{n} Say hello in five words.")
        ws = self.az.json("acr", "repository", "list", "-n", self.x["acr_login_server"].split(".")[0], scoped=False)
        public = "refused" if not ws.ok else "ALLOWED"
        ok = n in (a.get("text") or "")
        return self.record("S26-PRIVATE-ACR-IMAGE", "C02.f", Status.PASS if ok else Status.FAIL,
                           f"hosted agent version from an image in the private ACR is active and answered={ok}; "
                           f"registry listing from the public internet: {public}", answer=(a.get("text") or "")[:200],
                           before_acrpull=self.state.get("s26_first"))

    def cfg_suffix(self) -> str:
        return self.p.account_name.rsplit("-", 1)[-1]


def run(cfg: KitConfig, az: Az, ledger: Ledger, run_id: str, steps: list[str], log=print) -> list[CheckResult]:
    pr = Private(cfg, az, ledger, run_id, log)
    if not pr.ready():
        pr.record("RUNNER", "C00", Status.BLOCKED, "in-VNet runner not ready (cloud-init) within 15 min")
        return pr.results
    if not pr.upload_kit():
        pr.record("RUNNER", "C00", Status.BLOCKED, "kit upload to the in-VNet runner failed")
        return pr.results
    order = {"dns": pr.s21_dns, "public": pr.s20_public, "minimal": pr.s01_minimal, "invoke": pr.s02_invoke,
             "logs": pr.s10_logs, "langgraph": pr.s08_langgraph, "outbound": pr.s22_outbound,
             "telemetry": pr.s23_s24_telemetry, "mcp": pr.s07_mcp, "image": pr.s26_image, "mcp-diag": pr.mcp_diag,
             "netdiag": pr.netdiag, "image-bypass": pr.s26_bypass,
             "s07-retry": pr.s07_retry, "s07-control": pr.s07_control,
             "s26-tls": pr.s26_tls, "s26-tls-slow": pr.s26_tls_slow,
             "s07-std": pr.s07_std, "s26-std": pr.s26_std, "s03-s15": pr.s03_s15}
    for step in steps or list(order):
        try:
            order[step]()
        except RunnerBusy as exc:
            pr.record(f"STEP-{step}", "C00", Status.BLOCKED, f"runner VM unavailable: {exc}")
            break
        except Exception as exc:
            pr.record(f"STEP-{step}", "C00", Status.NOT_TESTED, f"kit error in step {step}: {type(exc).__name__}: {str(exc)[:300]}")
    return pr.results


def record(cfg: KitConfig, az: Az, results: list[CheckResult], suite: str) -> Path:
    from ..checks.preflight import collect_tool_versions
    by_point: dict[str, list[CheckResult]] = {}
    for r in results:
        by_point.setdefault(r.details.get("execution_point", "in-vnet-runner"), []).append(r)
    last = None
    for point, rs in by_point.items():
        ctx = ExecutionContext.capture(cfg, "real", collect_tool_versions(az))
        ctx.execution_point = point
        rec = RunRecorder(cfg.results_dir, ctx, suite)
        rec.extend(rs)
        last = rec.write()
        time.sleep(1)  # distinct run ids
    return last
