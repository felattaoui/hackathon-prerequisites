"""Separate, limited qualification runs on the existing West Europe lab: LangGraph (S08) and A2A (S03-S06).

Both reuse the account, project, model and observability of the first trial, and never touch the reference
agent `hapq-min`. Each uses its own test agent deployed with a pinned lock file:
  - hapq-lg   agents/langgraph  + agents/locks/langgraph.requirements.txt
  - hapq-a2a  agents/minimal    + agents/locks/minimal.requirements.txt (same code as the reference agent)
Error attribution:
  - kit/code/protocol error  -> NOT_TESTED with "kit" in the observation (our request or our agent code)
  - explicit platform refusal -> FAIL with the platform's status and message
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import secrets
from datetime import datetime, timezone

from ..config import REPO_ROOT, KitConfig
from ..results import CheckResult, Status
from . import classify

LOCKS = REPO_ROOT / "agents" / "locks"
AGENT_LG, AGENT_A2A = "hapq-lg", "hapq-a2a"
_VERSIONS = re.compile(r"HAPQ_VERSIONS (.+?)(?:\"|$)")


def limits_for(cfg: KitConfig, scenario: str) -> KitConfig:
    caps = {"langgraph": dict(max_agent_versions=1, max_sessions=1, max_calls=3),
            "a2a": dict(max_agent_versions=1, max_sessions=2, max_calls=6)}[scenario]
    return dataclasses.replace(cfg, limits=dataclasses.replace(cfg.limits, max_total_tokens=20000, **caps))


# ---------------- error attribution (pure, unit-tested) ----------------
def platform_or_kit(exc: Exception) -> tuple[Status, str]:
    """HTTP errors returned by Foundry are explicit platform answers; anything else is on the kit side."""
    code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    msg = str(exc)[:300]
    if code in (400, 404, 405, 409, 422) and re.search(r"not supported|unsupported|not allowed|cannot|invalid.*(kind|type)", msg, re.I):
        return Status.FAIL, f"explicit platform refusal HTTP {code}: {msg}"
    if code:
        return Status.FAIL, f"platform HTTP {code}: {msg}"
    return Status.NOT_TESTED, f"kit/code error {type(exc).__name__}: {msg}"


_JSONRPC_KIT_CODES = {-32700: "parse error", -32600: "invalid request", -32601: "method not found",
                      -32602: "invalid params"}


def a2a_message_outcome(http_status: int | None, payload: dict | None, agent_text: str, marker: str) -> tuple[Status, str]:
    """S04 attribution: transport/HTTP refusal (platform), JSON-RPC shape error (kit/protocol), A2A error, success."""
    if http_status is None:
        return Status.NOT_TESTED, "kit/transport error: no HTTP answer"
    if http_status in (401, 403):
        return Status.FAIL, f"platform refused the A2A call: HTTP {http_status}"
    if http_status >= 400:
        return Status.FAIL, f"platform HTTP {http_status} on the A2A endpoint"
    err = (payload or {}).get("error")
    if err:
        code = err.get("code")
        if code in _JSONRPC_KIT_CODES:
            return Status.NOT_TESTED, f"kit/protocol error: JSON-RPC {code} {_JSONRPC_KIT_CODES[code]}: {str(err.get('message'))[:160]}"
        return Status.FAIL, f"explicit platform refusal (A2A JSON-RPC {code}): {str(err.get('message'))[:160]}"
    if marker in agent_text:
        return Status.PASS, "marker present in agent-produced content"
    return Status.FAIL, "A2A call succeeded but the agent-produced content lacks the marker"


# ---------------- LangGraph ----------------
def run_langgraph(rt) -> list[CheckResult]:
    from .scenarios import AGENTS_DIR

    project = rt.profile.project_names[0]
    start = datetime.now(timezone.utc)

    def s08():
        d = rt.deploy(project, AGENT_LG, AGENTS_DIR / "langgraph", {"FOUNDRY_MODEL_NAME": rt.profile.model_deployment},
                      requirements=LOCKS / "langgraph.requirements.txt")
        details = {"version": d["version"], "status_history": d["history"], "error": d["error"]}
        if d["final"] != "active":
            st, msg = classify.version_outcome(d["history"], d["final"], d["error"])
            return rt.record("S08-LANGGRAPH", "C08", st, msg, **details)
        n = secrets.token_hex(6)
        try:
            out = rt.invoke(project, AGENT_LG, f"NONCE:{n} Say hello in five words.")
        except Exception as exc:
            if getattr(exc, "status_code", None) == 424:
                sid = classify.session_id_from_error(str(exc))
                lines = rt.session_logs(project, AGENT_LG, d["version"], sid, wait_s=30) if sid else []
                st, msg = classify.session_not_ready(lines)
                return rt.record("S08-LANGGRAPH", "C08", st, f"HTTP 424; {msg}", **details)
            raise
        st, msg = classify.answer_check(out["text"], n, out["usage"], out["max_output_tokens"])
        lines = rt.session_logs(project, AGENT_LG, d["version"], out["session"]) if out["session"] else []
        if not out["text"]:  # empty answer: attribute it using the container logs, never guess
            denied = [ln for ln in lines if "PermissionDenied" in ln or "401 Unauthorized" in ln]
            crash_st, crash_msg = classify.session_not_ready(lines)
            if denied:
                st, msg = Status.NOT_TESTED, "kit defect: the agent's model call was denied (401) - credential used by the agent code"
            elif crash_st is Status.NOT_TESTED:
                st, msg = crash_st, crash_msg.replace("crashed at startup", "raised an error while answering")
        installed = next((m.group(1) for ln in lines for m in [_VERSIONS.search(ln)] if m), None)
        corr = None
        try:
            corr, recent, _ = rt.correlated_traces(start, [out["id"], out["session"]])
        except PermissionError:
            pass
        return rt.record("S08-LANGGRAPH", "C08", st, f"version active; {msg}",
                         answer=out["text"][:300], usage=out["usage"], session_reused=None,
                         platform_installed_versions=installed, log_lines=len(lines), correlated_trace_rows=corr,
                         **details)
    rt.guarded("S08-LANGGRAPH", "C08", s08)
    for r in rt.results:
        r.details["kit_usage"] = rt.guard.summary()
    return rt.results


# ---------------- A2A ----------------
async def _a2a_send(base_url: str, token: str, marker: str, timeout: int) -> tuple[int | None, dict, str]:
    """Official a2a-sdk client (Learn sample pattern). Returns (http status, response dict, agent text)."""
    import httpx
    from a2a.client import A2ACardResolver, ClientConfig, create_client
    from a2a.helpers import new_text_message
    from a2a.types.a2a_pb2 import Role, SendMessageRequest
    from google.protobuf.json_format import MessageToDict

    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=httpx.Timeout(timeout)) as http:
        resolver = A2ACardResolver(httpx_client=http, base_url=base_url, agent_card_path="agentCard/v1.0")
        card = await resolver.get_agent_card()
        client = await create_client(agent=card, client_config=ClientConfig(streaming=False, httpx_client=http))
        texts, raw = [], []
        try:
            async for event in client.send_message(SendMessageRequest(
                    message=new_text_message(f"NONCE:{marker} Say hello in five words.", role=Role.ROLE_USER))):
                items = event if isinstance(event, tuple) else (event,)
                for item in items:
                    if hasattr(item, "DESCRIPTOR"):
                        d = MessageToDict(item)
                        raw.append(d)
                        texts.append(classify.a2a_agent_text({"result": d.get("message") and {"message": d["message"]}
                                                              or d.get("task") and {"task": d["task"]} or d}))
        finally:
            await client.close()
        return 200, {"events": raw}, "\n".join(t for t in texts if t)


def run_a2a(rt) -> list[CheckResult]:
    from .scenarios import AGENTS_DIR

    project = rt.profile.project_names[0]
    base = rt.a2a_base(project, AGENT_A2A)

    def setup():
        d = rt.deploy(project, AGENT_A2A, AGENTS_DIR / "minimal",
                      {"AZURE_AI_MODEL_DEPLOYMENT_NAME": rt.profile.model_deployment},
                      requirements=LOCKS / "minimal.requirements.txt")
        rt.state["a2a_agent"] = d
        if d["final"] != "active":
            st, msg = classify.version_outcome(d["history"], d["final"], d["error"])
            return rt.record("S03-A2A-CARD", "C03", Status.NOT_TESTED, f"test agent not ready: {msg}")
        return None

    rt.guarded("S03-A2A-CARD", "C03", setup)
    if rt.results:  # test agent not ready
        return rt.results

    def s03():
        from azure.ai.projects.models import (A2AProtocolConfiguration, AgentCard, AgentCardSkill, AgentEndpointConfig,
                                              ProtocolConfiguration, ResponsesProtocolConfiguration)
        try:
            rt.projects[project].agents.update_details(
                agent_name=AGENT_A2A,
                agent_endpoint=AgentEndpointConfig(protocol_configuration=ProtocolConfiguration(
                    responses=ResponsesProtocolConfiguration(), a2a=A2AProtocolConfiguration())),
                agent_card=AgentCard(version="1.0", description="hapq A2A test agent (synthetic)", skills=[
                    AgentCardSkill(id="echo", name="Echo nonce", description="Repeats the nonce it receives")]))
        except Exception as exc:
            st, msg = platform_or_kit(exc)
            return rt.record("S03-A2A-CARD", "C03", st, "enabling a2a on a HOSTED agent: " + msg)
        rt.guard.call()
        r = rt.http.get(f"{base}/agentCard/v1.0", headers={"Authorization": f"Bearer {rt.token('https://ai.azure.com/.default')}"})
        card = r.json() if r.status_code == 200 else {}
        versions = {card.get("protocolVersion")} | {i.get("protocolVersion") for i in card.get("supportedInterfaces") or []}
        ok = r.status_code == 200 and "1.0" in versions
        return rt.record("S03-A2A-CARD", "C03", Status.PASS if ok else Status.FAIL,
                         f"a2a enabled on hosted agent; card HTTP {r.status_code}, protocol versions "
                         f"{sorted(v for v in versions if v)}",
                         card_keys=sorted(card), skills=[s.get("id") for s in card.get("skills") or []])
    rt.guarded("S03-A2A-CARD", "C03", s03)

    def s05():
        rt.guard.call()
        code = rt.http.get(f"{base}/agentCard/v1.0").status_code
        st, msg = classify.expected_refusal(code, 401)
        return rt.record("S05-A2A-NO-TOKEN-401", "C03", st, msg + " (authentication only; says nothing about network policy)")
    rt.guarded("S05-A2A-NO-TOKEN-401", "C03", s05)

    if rt.passed("S03-A2A-CARD"):
        def s04():
            rt.guard.call()
            rt.guard.add_tokens(None)  # A2A responses carry no usage data: unknown, not zero
            marker = secrets.token_hex(6)
            try:
                status, payload, text = asyncio.run(_a2a_send(base, rt.token("https://ai.azure.com/.default"), marker,
                                                              rt.cfg.limits.call_timeout_s))
            except Exception as exc:
                code = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "status_code", None)
                body = str(exc)[:300]
                payload = {}
                rpc = re.search(r"JSON-RPC Error (-?\d+): (.*)", body)
                if rpc:  # the SDK raises the platform's JSON-RPC error object as an exception
                    payload = {"error": {"code": int(rpc.group(1)), "message": rpc.group(2)}}
                if code is None and not payload.get("error"):
                    return rt.record("S04-A2A-MESSAGE", "C03", Status.NOT_TESTED,
                                     f"kit/client error {type(exc).__name__}: {body}")
                status, text = code or 200, ""
            st, msg = a2a_message_outcome(status, payload, text, marker)
            return rt.record("S04-A2A-MESSAGE", "C03", st, msg, agent_text=text[:300],
                             events=len(payload.get("events", [])) if isinstance(payload, dict) else None)
        rt.guarded("S04-A2A-MESSAGE", "C03", s04)
    else:
        rt.skip("S04-A2A-MESSAGE", "C03", "S03-A2A-CARD")

    rt.record("S06-A2A-NO-ROLE-403", "C03", Status.BLOCKED,
              "requires an identity without any Foundry role (the MCP app's managed identity, not deployed in this step); "
              "creating another identity or role assignment is out of the approved scope")
    for r in rt.results:
        r.details["kit_usage"] = rt.guard.summary()
    return rt.results


def run_external_idp(rt) -> list[CheckResult]:
    """S30: a bearer token from a non-Entra issuer (synthetic, self-signed) must be refused by the agent endpoint.

    This shows the documented limitation (inbound = Entra only) with a real request. It is NOT an integration test with
    a real third-party IdP (S31 stays BLOCKED until such a tenant exists).
    """
    import time as _t

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(_t.time())
    foreign = jwt.encode({"iss": "https://idp.example.test/", "aud": "https://ai.azure.com", "sub": "synthetic-user",
                          "iat": now, "exp": now + 600}, key, algorithm="RS256", headers={"kid": "synthetic"})
    project = rt.profile.project_names[0]
    url = f"{rt.endpoints[project]}/agents/hapq-min/endpoint/protocols/openai/responses?api-version=v1"
    rt.guard.call()
    r = rt.http.post(url, headers={"Authorization": f"Bearer {foreign}"}, json={"input": "hello"})
    if r.status_code in (401, 403):
        st, msg = Status.PASS, f"refused with HTTP {r.status_code} (empty body={not r.text.strip()})"
    elif 200 <= r.status_code < 300:
        st, msg = Status.FAIL, f"ACCEPTED with HTTP {r.status_code}"
    else:
        st, msg = Status.NOT_TESTED, f"inconclusive: HTTP {r.status_code}"
    note = ("confirms the documented Entra-only inbound limitation" if st is Status.PASS else "limitation not confirmed")
    rt.record("S30-INBOUND-NON-ENTRA", "C04.a", st,
              f"token from a non-Entra issuer (synthetic): {msg}; {note}; not an integration with a real external IdP",
              body_excerpt=r.text[:200])
    return rt.results
