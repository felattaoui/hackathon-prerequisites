"""Test MCP tool for the hosted-agent lab: `whoami(nonce)` reports who called it.

Every route except /healthz requires an Entra bearer token for the kit audience (validated in auth.py).
- /mcp                    MCP streamable HTTP endpoint (tool `whoami`)
- /calls?nonce=...        calls recorded with that nonce (non-secret claims only)
- /probe/a2a-card?agent=  the tool's own managed identity (no Foundry role) requests the agent card of the
                          configured project; returns the HTTP status only (scenario S06, expected 403)
Configuration (environment): MCP_TENANT_ID, MCP_AUDIENCES (comma separated), PROBE_PROJECT_ENDPOINT,
AZURE_CLIENT_ID (user-assigned identity of the container app). Synthetic data only.
"""
from __future__ import annotations

import collections
import os
import re
import time

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from auth import InvalidToken, ValidatorConfig, bearer, default_key_resolver, validate

CALLS: collections.deque = collections.deque(maxlen=200)
_AGENT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

def create_server(allowed_hosts: list[str] | None = None) -> FastMCP:
    """A fresh MCP server (its session manager can run only once).

    Host-header protection: only MCP_ALLOWED_HOSTS (the app FQDN set by the Bicep) are accepted.
    """
    if allowed_hosts is None:
        allowed_hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed_hosts,
                                         allowed_origins=[])
    mcp = FastMCP("hapq-whoami", stateless_http=True, json_response=True, transport_security=security)

    @mcp.tool()
    def whoami(nonce: str, ctx: Context) -> dict:
        """Return the identity of the caller as validated by this tool. Pass the nonce you were given."""
        request = ctx.request_context.request
        claims = request.scope.get("state", {}).get("hapq_claims", {}) if request is not None else {}
        record = {"nonce": nonce[:64], "at": time.time(), "claims": claims}
        CALLS.append(record)
        return record


    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})


    @mcp.custom_route("/calls", methods=["GET"])
    async def calls(request: Request) -> JSONResponse:
        nonce = request.query_params.get("nonce", "")
        return JSONResponse([c for c in CALLS if c["nonce"] == nonce])


    @mcp.custom_route("/probe/a2a-card", methods=["GET"])
    async def probe(request: Request) -> JSONResponse:
        agent = request.query_params.get("agent", "")
        base = os.environ.get("PROBE_PROJECT_ENDPOINT", "").rstrip("/")
        if not _AGENT_NAME.match(agent) or not base.startswith("https://"):
            return JSONResponse({"error": "invalid agent or probe not configured"}, status_code=400)
        import httpx
        from azure.identity.aio import ManagedIdentityCredential

        async with ManagedIdentityCredential(client_id=os.environ.get("AZURE_CLIENT_ID")) as cred:
            token = (await cred.get_token("https://ai.azure.com/.default")).token
        url = f"{base}/agents/{agent}/endpoint/protocols/a2a/agentCard/v1.0"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}", "A2A-Version": "1.0"})
        problem = {}
        try:  # RFC 7807 problem fields only (non-secret): type/title/status
            body = resp.json()
            problem = {k: body.get(k) for k in ("type", "title", "status") if isinstance(body, dict)}
        except ValueError:
            pass
        return JSONResponse({"status": resp.status_code, "identity": "container-app-managed-identity", "problem": problem})

    return mcp


class EntraAuthMiddleware:
    """Pure ASGI middleware: 401 unless a valid token is presented (except /healthz)."""

    def __init__(self, app, cfg: ValidatorConfig, resolve_key):
        self.app, self.cfg, self.resolve_key = app, cfg, resolve_key

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/healthz":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        try:
            claims = validate(bearer(headers.get("authorization")), self.cfg, self.resolve_key)
        except InvalidToken as exc:
            response = JSONResponse({"error": "invalid_token", "reason": str(exc)}, status_code=401,
                                    headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
            return await response(scope, receive, send)
        scope.setdefault("state", {})["hapq_claims"] = claims
        return await self.app(scope, receive, send)


def build_app(cfg: ValidatorConfig | None = None, resolve_key=None, allowed_hosts: list[str] | None = None):
    cfg = cfg or ValidatorConfig(os.environ["MCP_TENANT_ID"],
                                 tuple(a.strip() for a in os.environ["MCP_AUDIENCES"].split(",") if a.strip()))
    return EntraAuthMiddleware(create_server(allowed_hosts).streamable_http_app(), cfg, resolve_key or default_key_resolver(cfg))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
