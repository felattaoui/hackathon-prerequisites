"""A2A reproduction capture for a product-group review (nothing is submitted anywhere).

One capture of the SAME request already observed (agent card GET + JSON-RPC SendMessage via the official a2a-sdk),
plus one Responses call to the same agent to show it is healthy outside A2A. Outputs, both under results/
(git-ignored, local):
  full-diagnostics.json  complete headers and bodies, correlation ids and agent identifiers; Authorization headers
                         and any token/secret are removed. Protected local file: do not share as is.
  dossier.md             review dossier, fully redacted (GUIDs, tokens, keys, UPNs), pointing to the local file.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import re
import secrets
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from ..config import REPO_ROOT, KitConfig
from ..redact import _PATTERNS, redact_text
from .profile import LabProfile

AGENT = "hapq-a2a"
DROP_HEADERS = {"authorization", "cookie", "set-cookie", "proxy-authorization"}
SECRET_PATTERNS = [(p, r) for p, r in _PATTERNS if r not in ("<guid>", "<upn>")]  # keep correlation GUIDs


def scrub_secrets(text: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _headers(h) -> dict:
    return {k: scrub_secrets(v) for k, v in h.items() if k.lower() not in DROP_HEADERS}


def _versions() -> dict:
    out = {"python": platform.python_version(), "os": f"{platform.system()} {platform.release()}"}
    for pkg in ("a2a-sdk", "azure-ai-projects", "azure-identity", "azure-core", "httpx", "openai", "protobuf"):
        try:
            out[pkg] = version(pkg)
        except Exception:
            out[pkg] = "absent"
    return out


async def _capture_a2a(base: str, token: str, marker: str, exchanges: list) -> str:
    import httpx
    from a2a.client import A2ACardResolver, ClientConfig, create_client
    from a2a.helpers import new_text_message
    from a2a.types.a2a_pb2 import Role, SendMessageRequest

    async def on_request(req):
        req.headers["x-ms-client-request-id"] = str(uuid.uuid4())
        exchanges.append({"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                          "request": {"method": req.method, "url": str(req.url), "headers": _headers(req.headers),
                                      "body": scrub_secrets(req.content.decode("utf-8", "replace")) if req.content else ""}})

    async def on_response(resp):
        await resp.aread()
        exchanges[-1]["response"] = {"status": resp.status_code, "headers": _headers(resp.headers),
                                     "body": scrub_secrets(resp.text)}

    outcome = "no exception"
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=120,
                                 event_hooks={"request": [on_request], "response": [on_response]}) as http:
        card = await A2ACardResolver(httpx_client=http, base_url=base, agent_card_path="agentCard/v1.0").get_agent_card()
        client = await create_client(agent=card, client_config=__import__("a2a.client", fromlist=["ClientConfig"])
                                     .ClientConfig(streaming=False, httpx_client=http))
        try:
            async for _ in client.send_message(SendMessageRequest(
                    message=new_text_message(f"NONCE:{marker} Say hello in five words.", role=Role.ROLE_USER))):
                pass
        except Exception as exc:
            outcome = f"{type(exc).__name__}: {scrub_secrets(str(exc))}"
        finally:
            await client.close()
    return outcome


def capture(cfg: KitConfig, profile: LabProfile, guard) -> Path:
    from azure.ai.projects import AIProjectClient
    from azure.identity import AzureCliCredential

    lim = cfg.limits
    cred = AzureCliCredential(process_timeout=lim.call_timeout_s)
    client = AIProjectClient(endpoint=profile.project_endpoints[0], credential=cred, connection_timeout=lim.call_timeout_s,
                             read_timeout=lim.call_timeout_s, retry_total=lim.max_retries)
    agent = client.agents.get(agent_name=AGENT).as_dict()
    latest = (agent.get("versions") or {}).get("latest") or {}
    lock = REPO_ROOT / "agents" / "locks" / "minimal.requirements.txt"

    # health check on the agent's own Responses protocol (one model call)
    guard.call()
    r = client.get_openai_client(agent_name=AGENT).with_options(timeout=lim.call_timeout_s, max_retries=lim.max_retries)\
        .responses.create(input=f"NONCE:{secrets.token_hex(6)} Say hello in five words.", max_output_tokens=lim.max_output_tokens)
    guard.add_tokens(getattr(getattr(r, "usage", None), "total_tokens", None))
    health = {"response_id": r.id, "status": r.status, "answer": r.output_text,
              "agent_session_id": (r.model_extra or {}).get("agent_session_id")}

    # the A2A request already observed, captured in full
    guard.call(); guard.call()
    guard.add_tokens(None)
    exchanges: list = []
    base = f"{profile.project_endpoints[0]}/agents/{AGENT}/endpoint/protocols/a2a"
    outcome = asyncio.run(_capture_a2a(base, cred.get_token("https://ai.azure.com/.default").token,
                                       secrets.token_hex(6), exchanges))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = cfg.results_dir / "a2a-repro" / stamp
    out.mkdir(parents=True, exist_ok=True)
    full = {
        "captured_at": stamp, "region": profile.region, "account": profile.account_name,
        "project": profile.project_names[0], "agent_name": AGENT,
        "agent_kind": (latest.get("definition") or {}).get("kind"), "agent_version": latest.get("version"),
        "agent_details": agent, "agent_lock_file": lock.read_text(encoding="utf-8"),
        "agent_code_zip_sha256": ((latest.get("definition") or {}).get("code_configuration") or {}).get("content_hash"),
        "client_versions": _versions(), "responses_health_check": health,
        "a2a_exchanges": exchanges, "a2a_client_outcome": outcome,
    }
    text = scrub_secrets(json.dumps(full, indent=1, default=str))
    assert not re.search(r"eyJ[A-Za-z0-9_-]{8,}\.", text), "token pattern found in capture"
    (out / "full-diagnostics.json").write_text(text, encoding="utf-8")
    (out / "dossier.md").write_text(render_dossier(full, out / "full-diagnostics.json"), encoding="utf-8")
    return out


def render_dossier(full: dict, full_path: Path) -> str:
    agent = full["agent_details"]
    latest = (agent.get("versions") or {}).get("latest") or {}
    definition = latest.get("definition") or {}
    endpoint = agent.get("agent_endpoint") or {}
    ex = full["a2a_exchanges"]
    sha = hashlib.sha256(full_path.read_bytes()).hexdigest()[:16]

    def block(obj) -> str:
        return "```json\n" + redact_text(json.dumps(obj, indent=1, default=str)) + "\n```"

    parts = [
        "# A2A reproduction — hosted agent inbound invocation refused (HOSTED_AGENT_NOT_SUPPORTED)", "",
        "Prepared for a product-group review. Not submitted. Synthetic data only. Identifiers are redacted here; the",
        f"correlation ids are in the protected local file `{full_path.name}` (sha256 prefix {sha}).", "",
        "## Context", "",
        f"- Captured (UTC): {full['captured_at']}",
        f"- Region: {full['region']}",
        f"- Agent: kind `{full['agent_kind']}`, version `{full['agent_version']}`, deployed from source "
        f"(runtime `{(definition.get('code_configuration') or {}).get('runtime')}`, "
        f"dependency resolution `{(definition.get('code_configuration') or {}).get('dependency_resolution')}`)",
        f"- Version protocols (definition.protocol_versions): `{json.dumps(definition.get('protocol_versions'))}`",
        f"- Endpoint protocol configuration: `{redact_text(json.dumps(endpoint.get('protocol_configuration')))}`",
        f"- Client versions: `{json.dumps(full['client_versions'])}`", "",
        "## Steps", "",
        "1. Deploy a hosted agent from source on the Responses protocol (`create_version_from_code`, azure-ai-projects).",
        "2. Enable incoming A2A with `agents.update_details(agent_endpoint=...protocol_configuration(responses, a2a), agent_card=...)`.",
        "3. GET `<project>/agents/<agent>/endpoint/protocols/a2a/agentCard/v1.0` with an Entra token (scope https://ai.azure.com/.default).",
        "4. Send `SendMessage` (JSON-RPC, header `A2A-Version: 1.0`) with the official `a2a-sdk` client resolved from that card.", "",
        "## Expected", "",
        "Per the hosted-agents concept page, the A2A endpoint `{project_endpoint}/agents/{name}/endpoint/protocols/a2a`",
        "is listed for hosted agents; per the enable-A2A page, incoming A2A requires the responses protocol, which is",
        "enabled. Expected: a JSON-RPC result containing the agent's answer.", "",
        "## Observed", "",
        f"- Responses protocol on the same agent: status `{full['responses_health_check']['status']}`, answer "
        f"`{redact_text(str(full['responses_health_check']['answer']))}` (agent healthy).",
        f"- A2A client outcome: `{redact_text(full['a2a_client_outcome'])}`", "",
    ]
    for i, e in enumerate(ex, 1):
        parts += [f"### Exchange {i} — {e['request']['method']} at {e['at']}", "", "Request:", block(e["request"]), "",
                  "Response:", block(e.get("response")), ""]
    parts += ["## Not done on purpose", "",
              "- No retry with variations, no prompt agent and no proxy: they would not validate native exposure of the hosted agent.",
              "- Single capture of the request already observed on 2026-10-02 (run 20261002T070244Z-e5200c)."]
    return "\n".join(parts)
