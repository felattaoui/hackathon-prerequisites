"""In-VNet test runner for the private test environment (runs ON the runner VM, driven by az vm run-command).

Self-contained: python venv /opt/hapq/venv with azure-identity, azure-ai-projects, httpx, azure-storage-blob.
Authenticates with the VM's system-assigned managed identity. Every command prints ONE compact JSON line
(run-command output is truncated at ~4 KB). Identifiers are compared on the VM; only booleans and redacted
values are printed. Synthetic data only.

Usage: python runner.py <command> '<json args>'
"""
import base64
import hashlib
import io
import json
import re
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
ROOT = Path(__file__).resolve().parents[2]


def red(text) -> str:
    return JWT.sub("<token>", GUID.sub("<guid>", str(text)))


def out(**kw):
    print(json.dumps({k: (red(v) if isinstance(v, str) else v) for k, v in kw.items()})[:3800])


def cred():
    from azure.identity import ManagedIdentityCredential
    return ManagedIdentityCredential()


def token(scope: str) -> str:
    return cred().get_token(scope).token


def project(endpoint: str):
    from azure.ai.projects import AIProjectClient
    return AIProjectClient(endpoint=endpoint, credential=cred(), connection_timeout=60, read_timeout=120, retry_total=2)


def zip_dir(path: Path, requirements: Path | None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(path.iterdir()):
            if f.is_file() and f.suffix in (".py", ".txt"):
                src = requirements if (f.name == "requirements.txt" and requirements) else f
                info = zipfile.ZipInfo(f.name, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o100644 << 16  # regular file + rw-r--r-- (S_IFREG needed by Unix extractors)
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, src.read_bytes())
    return buf.getvalue()


def zip_tree(path: Path, requirements: Path | None) -> bytes:
    """Recursive, deterministic variant (third-party samples with packages, e.g. the network diagnostic agent)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
            rel = f.relative_to(path).as_posix()
            src = requirements if (rel == "requirements.txt" and requirements) else f
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, src.read_bytes())
    return buf.getvalue()


# ---------------------------------------------------------------- commands
def cmd_dns(a):
    res = {}
    for host in a["hosts"]:
        try:
            ips = sorted({i[4][0] for i in socket.getaddrinfo(host, 443)})
        except OSError as exc:
            ips = [f"error {type(exc).__name__}"]
        res[host] = {"ips": ips, "private_10_42": any(ip.startswith("10.42.") for ip in ips)}
    out(ok=True, dns=res)


def _poll(client, name, version, timeout_s):
    history, deadline, v = [], time.time() + timeout_s, None
    while time.time() < deadline:
        v = client.agents.get_version(agent_name=name, agent_version=version)
        history.append(v["status"])
        if v["status"] in ("active", "failed"):
            break
        time.sleep(10)
    err = v.get("error") if v is not None else None
    return history, (json.dumps(err)[:600] if err else None)


def cmd_deploy_code(a):
    from azure.ai.projects.models import (CodeConfiguration, HostedAgentDefinition, ProtocolVersionRecord,
                                          SessionConfiguration)
    client = project(a["endpoint"])
    lock = ROOT / a["lock"] if a.get("lock") else None
    code = (zip_tree if a.get("recursive") else zip_dir)(ROOT / a["src"], lock)
    created = client.agents.create_version_from_code(
        agent_name=a["agent"],
        definition=HostedAgentDefinition(
            cpu="0.5", memory="1Gi",
            code_configuration=CodeConfiguration(runtime="python_3_13", entry_point=["python", "main.py"],
                                                 dependency_resolution="remote_build"),
            protocol_versions=[ProtocolVersionRecord(protocol=a.get("protocol", "responses"), version="2.0.0")],
            environment_variables=a.get("env", {}), session_configuration=SessionConfiguration(idle_timeout_seconds=120)),
        code=(f"{a['agent']}.zip", code, "application/zip"), code_zip_sha256=hashlib.sha256(code).hexdigest(),
        description="hapq private test agent (synthetic)")
    history, err = _poll(client, a["agent"], created.version, a.get("timeout_s", 900))
    out(ok=True, version=created.version, history=history, final=history[-1] if history else None, error=err)


def cmd_deploy_image(a):
    from azure.ai.projects.models import (ContainerConfiguration, HostedAgentDefinition, ProtocolVersionRecord,
                                          SessionConfiguration)
    client = project(a["endpoint"])
    created = client.agents.create_version(
        agent_name=a["agent"],
        definition=HostedAgentDefinition(
            cpu="0.5", memory="1Gi", container_configuration=ContainerConfiguration(image=a["image"]),
            protocol_versions=[ProtocolVersionRecord(protocol="responses", version="2.0.0")],
            environment_variables=a.get("env", {}), session_configuration=SessionConfiguration(idle_timeout_seconds=120)),
        description="hapq private-registry image agent (synthetic)")
    history, err = _poll(client, a["agent"], created.version, a.get("timeout_s", 900))
    out(ok=True, version=created.version, history=history, final=history[-1] if history else None, error=err)


def cmd_invoke(a):
    client = project(a["endpoint"])
    oa = client.get_openai_client(agent_name=a["agent"]).with_options(timeout=180, max_retries=1)
    kw = {"input": a["text"], "max_output_tokens": a.get("max_output_tokens", 1000)}
    if a.get("session"):
        kw["extra_body"] = {"agent_session_id": a["session"]}
    try:
        r = oa.responses.create(**kw)
    except Exception as exc:
        out(ok=False, error=f"{type(exc).__name__}: {str(exc)[:500]}", status_code=getattr(exc, "status_code", None))
        return
    u = getattr(r, "usage", None)
    out(ok=True, id=r.id, status=r.status, text=(r.output_text or "")[:400],
        session=(r.model_extra or {}).get("agent_session_id"),
        usage=None if u is None else {"input": u.input_tokens, "output": u.output_tokens, "total": u.total_tokens})


def cmd_logs(a):
    client = project(a["endpoint"])
    stream = client.agents.get_session_log_stream(agent_name=a["agent"], agent_version=a["version"], session_id=a["session"])
    lines, deadline = [], time.time() + a.get("wait_s", 40)
    for chunk in stream:
        t = chunk.decode("utf-8", "replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for m in re.finditer(r'"message":"(.*?)"\}', t):
            lines.append(m.group(1))
        if len(lines) > 300 or time.time() > deadline:
            break
    if a.get("save"):
        Path(a["save"]).parent.mkdir(parents=True, exist_ok=True)
        Path(a["save"]).write_text(red("\n".join(lines)), encoding="utf-8")
    keep = [l for l in lines if any(k in l for k in a.get("grep", ["HTTP Request", "Error", "Traceback", "HAPQ"]))]
    out(ok=True, count=len(lines), matches=[l[:a.get("width", 220)] for l in keep[:a.get("max", 10)]])


def cmd_identity_match(a):
    """S07: compare oids recorded by the MCP tool with the agent instance identity, on the VM."""
    import httpx
    client = project(a["endpoint"])
    details = client.agents.get(agent_name=a["agent"]).as_dict()
    inst = (details.get("instance_identity") or {}).get("principal_id")
    blue = (details.get("blueprint") or {}).get("principal_id")
    tok = token(f"{a['audience']}/.default")
    calls, deadline = [], time.time() + a.get("wait_s", 45)
    while time.time() < deadline and not calls:
        r = httpx.get(f"{a['mcp_url']}/calls", params={"nonce": a["nonce"]}, headers={"Authorization": f"Bearer {tok}"},
                      timeout=30)
        calls = r.json() if r.status_code == 200 else []
        if not calls:
            time.sleep(5)
    oids = [c.get("claims", {}).get("oid") for c in calls]
    out(ok=True, calls=len(calls), expected_available=bool(inst), match_instance=bool(inst) and inst in oids,
        match_blueprint=bool(blue) and blue in oids, idtyp=sorted({c.get("claims", {}).get("idtyp") for c in calls if c.get("claims")}))


def cmd_http(a):
    import httpx
    headers = dict(a.get("headers", {}))
    if a.get("scope"):
        headers["Authorization"] = f"Bearer {token(a['scope'])}"
    try:
        r = httpx.request(a.get("method", "GET"), a["url"], headers=headers, json=a.get("json"), timeout=60)
        out(ok=True, status=r.status_code, body=r.text[:600])
    except Exception as exc:
        out(ok=False, error=f"{type(exc).__name__}: {str(exc)[:300]}")


def cmd_blob_put(a):
    from azure.storage.blob import BlobServiceClient
    svc = BlobServiceClient(f"https://{a['account']}.blob.core.windows.net", credential=cred())
    c = svc.get_container_client(a["container"])
    try:
        c.create_container()
    except Exception:
        pass
    c.upload_blob(a["blob"], a["content"].encode(), overwrite=True)
    out(ok=True, uploaded=a["blob"])


def cmd_trace_query(a):
    import httpx
    tok = token("https://api.applicationinsights.io/.default")
    deadline, last = time.time() + a.get("wait_s", 600), {}
    while True:
        r = httpx.post(f"https://api.applicationinsights.io/v1/apps/{a['app_id']}/query",
                       headers={"Authorization": f"Bearer {tok}"}, json={"query": a["query"]}, timeout=60)
        if r.status_code == 200:
            rows = r.json()["tables"][0]["rows"]
            last = {"status": 200, "row": rows[0] if rows else None}
            if rows and rows[0] and rows[0][0]:
                break
        else:
            last = {"status": r.status_code, "body": r.text[:300]}
        if time.time() > deadline:
            break
        time.sleep(30)
    out(ok=True, **last)


def cmd_docker_push(a):
    """Build a context with docker on the VM and push it to the private ACR (token exchange with the VM identity)."""
    import httpx
    login = a["login_server"]
    aad = token("https://management.azure.com/.default")
    ex = httpx.post(f"https://{login}/oauth2/exchange", data={"grant_type": "access_token", "service": login,
                                                              "access_token": aad}, timeout=60)
    if ex.status_code != 200:
        out(ok=False, step="acr token exchange", status=ex.status_code, body=ex.text[:300])
        return
    refresh = ex.json()["refresh_token"]
    image = f"{login}/{a['repo']}"
    steps = [["docker", "login", login, "-u", "00000000-0000-0000-0000-000000000000", "--password-stdin"],
             ["docker", "build", "-t", image, str(ROOT / a["context"])], ["docker", "push", image]]
    for i, cmd in enumerate(steps):
        p = subprocess.run(cmd, input=refresh if i == 0 else None, capture_output=True, text=True, timeout=1500)
        if p.returncode != 0:
            out(ok=False, step=" ".join(cmd[:2]), stderr=p.stderr[-800:])
            return
    out(ok=True, image=image)


def cmd_toolbox(a):
    from azure.ai.projects.models import MCPToolboxTool
    client = project(a["endpoint"])
    tb = client.toolboxes.create_version(name=a["name"], description="hapq private toolbox", tools=[
        MCPToolboxTool(server_label="whoami", server_url=a["server_url"], require_approval="never",
                       project_connection_id=a["connection"])])
    out(ok=True, version=str(tb.version))


def cmd_agent_principal(a):
    """Principal id of the agent's own identity, needed to grant it a data role (not a secret; printed raw)."""
    d = project(a["endpoint"]).agents.get(agent_name=a["agent"]).as_dict()
    print(json.dumps({"ok": True, "principal": (d.get("instance_identity") or {}).get("principal_id")}))


def cmd_agent_version(a):
    d = project(a["endpoint"]).agents.get(agent_name=a["agent"]).as_dict()
    out(ok=True, version=((d.get("versions") or {}).get("latest") or {}).get("version"))


def cmd_invocations(a):
    """POST to the agent's Invocations endpoint; the full body is saved on the VM (read back with `chunk`)."""
    import httpx
    url = f"{a['endpoint']}/agents/{a['agent']}/endpoint/protocols/invocations?api-version=v1"
    r = httpx.post(url, json=a.get("body", {}), headers={"Authorization": f"Bearer {token('https://ai.azure.com/.default')}"},
                   timeout=a.get("timeout_s", 300))
    Path(a["save"]).parent.mkdir(parents=True, exist_ok=True)
    Path(a["save"]).write_text(red(r.text), encoding="utf-8")
    out(ok=r.status_code < 400, status=r.status_code, size=len(r.text), session=r.headers.get("x-agent-session-id"))


def cmd_chunk(a):
    """Read a saved (already redacted) file in pieces small enough for the run-command output limit."""
    data = Path(a["path"]).read_text(encoding="utf-8")
    off, n = a.get("offset", 0), a.get("length", 2500)
    print(json.dumps({"ok": True, "total": len(data), "data": data[off:off + n]}))


def cmd_s07_attempt(a):
    """One identical S07 call + its full session log (saved on the VM); returns the complete McpError line and ids."""
    client = project(a["endpoint"])
    oa = client.get_openai_client(agent_name=a["agent"]).with_options(timeout=180, max_retries=0)
    t0 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    res = {"at": t0}
    try:
        r = oa.responses.with_raw_response.create(input=a["text"], max_output_tokens=1000)
        body = r.parse()
        res.update(http=r.status_code, response_id=body.id, status=body.status, text=(body.output_text or "")[:200],
                   session=(body.model_extra or {}).get("agent_session_id"),
                   request_id=r.headers.get("x-request-id") or r.headers.get("apim-request-id"),
                   region=r.headers.get("x-ms-region"))
    except Exception as exc:
        res.update(error=f"{type(exc).__name__}: {str(exc)[:400]}", http=getattr(exc, "status_code", None))
    ver = ((client.agents.get(agent_name=a["agent"]).as_dict().get("versions") or {}).get("latest") or {}).get("version")
    lines = []
    if res.get("session"):
        stream = client.agents.get_session_log_stream(agent_name=a["agent"], agent_version=ver, session_id=res["session"])
        deadline = time.time() + 45
        for chunk in stream:
            t = chunk.decode("utf-8", "replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
            lines += [m.group(1) for m in re.finditer(r'"message":"(.*?)"\}', t)]
            if time.time() > deadline or any("Inbound POST /responses completed" in l for l in lines[-5:]):
                break
        Path(a["save"]).parent.mkdir(parents=True, exist_ok=True)
        Path(a["save"]).write_text(red("\n".join(lines)), encoding="utf-8")
    err = next((l for l in lines if "McpError: tools/list failed" in l or l.startswith("mcp.shared.exceptions.McpError")), "")
    trace = next((re.search(r"trace-id: ([0-9a-f]+)", l).group(1) for l in lines if "trace-id:" in l), None)
    res.update(version=ver, log_lines=len(lines), mcp_error=err.replace(chr(92) + "u0022", '"')[:2000], trace_id=trace,
               tools_list_error=bool(err))
    out(ok=True, **res)


def cmd_run_sample(a):
    """Run a third-party test script unmodified with the given env; full output saved (redacted) on the VM."""
    import os
    env = {**os.environ, **a.get("env", {})}
    t0 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    p = subprocess.run([sys.executable, str(ROOT / a["script"]), *a.get("args", [])], capture_output=True, text=True,
                       env=env, timeout=a.get("timeout_s", 1500), cwd=str(ROOT))
    text = red(p.stdout + "\n--- stderr ---\n" + p.stderr)
    Path(a["save"]).parent.mkdir(parents=True, exist_ok=True)
    Path(a["save"]).write_text(text, encoding="utf-8")
    keys = ("TEST PASSED", "TEST FAILED", "Known Issue", "PASSED", "FAILED", "Error code", "Request ID", "Response ID",
            "424", "401", "403", "TaskCanceled", "resolve")
    hits = [l[:300] for l in text.splitlines() if any(k in l for k in keys)]
    out(ok=True, at=t0, exit_code=p.returncode, size=len(text), highlights=hits[-12:])


def cmd_tls_compare(a):
    """S26 diagnosis from the VM: DNS, IP, TCP, TLS (with SNI) and HTTP status per host; full result saved."""
    import ssl
    res = []
    for h in a["hosts"]:
        r = {"host": h}
        try:
            ips = sorted({ai[4][0] for ai in socket.getaddrinfo(h, 443, proto=socket.IPPROTO_TCP)})
            r["ips"] = ips
            t0 = time.time()
            s = socket.create_connection((ips[0], 443), timeout=8)
            r["tcp_ms"] = round((time.time() - t0) * 1000, 1)
            t1 = time.time()
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(s, server_hostname=h) as tls:
                r["tls_ms"], r["tls"] = round((time.time() - t1) * 1000, 1), tls.version()
                r["cert_cn"] = dict(x[0] for x in tls.getpeercert().get("subject", ()))["commonName"]
                path = a.get("paths", {}).get(h, "/")
                tls.sendall(f"GET {path} HTTP/1.1\r\nHost: {h}\r\nConnection: close\r\n\r\n".encode())
                r["http_status"] = tls.recv(64).decode("latin-1").split("\r\n")[0]
        except Exception as exc:
            r["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        res.append(r)
    Path(a["save"]).parent.mkdir(parents=True, exist_ok=True)
    Path(a["save"]).write_text(red(json.dumps(res, indent=1)), encoding="utf-8")
    out(ok=True, results=[{k: v for k, v in r.items() if k in ("host", "ips", "tls", "http_status", "error")} for r in res])


def cmd_a2a_card(a):
    """S03 from the VNet: enable a2a on a (kit test) hosted agent, then GET its agent card with the VM identity."""
    import httpx
    from azure.ai.projects.models import (A2AProtocolConfiguration, AgentCard, AgentCardSkill, AgentEndpointConfig,
                                          ProtocolConfiguration, ResponsesProtocolConfiguration)
    client = project(a["endpoint"])
    try:
        client.agents.update_details(
            agent_name=a["agent"],
            agent_endpoint=AgentEndpointConfig(protocol_configuration=ProtocolConfiguration(
                responses=ResponsesProtocolConfiguration(), a2a=A2AProtocolConfiguration())),
            agent_card=AgentCard(version="1.0", description="hapq A2A test agent (synthetic)", skills=[
                AgentCardSkill(id="echo", name="Echo nonce", description="Repeats the nonce it receives")]))
    except Exception as exc:
        out(ok=False, stage="enable", error=f"{type(exc).__name__}: {str(exc)[:400]}")
        return
    url = f"{a['endpoint']}/agents/{a['agent']}/endpoint/protocols/a2a/agentCard/v1.0"
    r = httpx.get(url, headers={"Authorization": f"Bearer {token('https://ai.azure.com/.default')}"}, timeout=60)
    card = r.json() if r.status_code == 200 else {}
    versions = sorted(({card.get("protocolVersion")} | {i.get("protocolVersion") for i in card.get("supportedInterfaces") or []})
                      - {None})
    out(ok=True, status=r.status_code, versions=versions)


def cmd_list_agents(a):
    res = {}
    for name, ep in a["projects"].items():
        try:
            res[name] = sorted(x.name for x in project(ep).agents.list())
        except Exception as exc:
            res[name] = [f"error {type(exc).__name__}"]
    out(ok=True, agents=res)


if __name__ == "__main__":
    name, args = sys.argv[1], json.loads(base64.b64decode(sys.argv[2]).decode()) if len(sys.argv) > 2 else {}
    try:
        globals()["cmd_" + name.replace("-", "_")](args)
    except Exception as exc:  # harness error on the VM: reported, never a PASS
        out(ok=False, error=f"{type(exc).__name__}: {str(exc)[:600]}")
