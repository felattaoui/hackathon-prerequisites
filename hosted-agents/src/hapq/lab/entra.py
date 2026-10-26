"""Entra app registration used as the audience of the MCP test tool (AgenticIdentityToken connection).

Created only by `hapq lab deploy --execute`. Each created object is tagged "hapq-kit" and written to the
ledger immediately; cleanup deletes only ledger objects that still carry the tag. An existing app with the
same display name that the kit did not create is never adopted.

The app exposes one delegated scope and pre-authorizes the Azure CLI client so that the operator can obtain
a token for this audience (to read /calls and trigger the S06 probe) without a consent prompt.
"""
from __future__ import annotations

import json
import uuid

from ..azcli import Az
from ..ledger import Ledger

GRAPH = "https://graph.microsoft.com/v1.0"
AZURE_CLI_APP_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"  # well-known first-party Azure CLI client
MARKER = "hapq-kit"


class EntraError(RuntimeError):
    pass


def _graph(az: Az, method: str, path: str, body: dict | None = None):
    args = ["rest", "--method", method, "--url", f"{GRAPH}/{path}"]
    if body is not None:
        args += ["--headers", "Content-Type=application/json", "--body", json.dumps(body)]
    return az.json(*args, scoped=False)


def find_by_name(az: Az, display_name: str) -> list[dict]:
    res = _graph(az, "GET", f"applications?$filter=displayName eq '{display_name}'&$select=id,appId,tags")
    if not res.ok:
        raise EntraError("cannot read applications: " + (res.stderr or "")[-300:])
    return (res.json() or {}).get("value", [])


def ensure_mcp_app(az: Az, ledger: Ledger, display_name: str, environment: str, run_id: str = "") -> dict:
    """Create (or reuse a ledger-owned) app + service principal. Returns {appId, objectId, audience}."""
    owned = {e.id.lower(): e for e in ledger.owned() if e.kind == "entra_app"}
    existing = find_by_name(az, display_name)
    for app in existing:
        if app["id"].lower() in owned and MARKER in app.get("tags", []):
            return {"appId": app["appId"], "objectId": app["id"], "audience": f"api://{app['appId']}"}
    if existing:
        raise EntraError(f"an application named {display_name!r} exists and was not created by this kit; not adopted")

    scope_id = str(uuid.uuid4())
    created = _graph(az, "POST", "applications", {
        "displayName": display_name, "signInAudience": "AzureADMyOrg", "tags": [MARKER],
        "notes": "Created by the hapq lab kit for a hosted-agent test MCP tool. Safe to delete after the lab.",
        "api": {"requestedAccessTokenVersion": 2, "oauth2PermissionScopes": [{
            "id": scope_id, "value": "user_impersonation", "type": "User", "isEnabled": True,
            "adminConsentDisplayName": "Access hapq test tool", "adminConsentDescription": "Lab test tool access",
            "userConsentDisplayName": "Access hapq test tool", "userConsentDescription": "Lab test tool access"}]},
    })
    if not created.ok:
        raise EntraError("application creation failed: " + (created.stderr or "")[-300:])
    app = created.json()
    ledger.created("entra_app", app["id"], display_name, environment, run_id=run_id, appId=app["appId"])
    patch = _graph(az, "PATCH", f"applications/{app['id']}", {
        "identifierUris": [f"api://{app['appId']}"],
        "api": {"requestedAccessTokenVersion": 2,
                "oauth2PermissionScopes": [{"id": scope_id, "value": "user_impersonation", "type": "User",
                                            "isEnabled": True, "adminConsentDisplayName": "Access hapq test tool",
                                            "adminConsentDescription": "Lab test tool access"}],
                "preAuthorizedApplications": [{"appId": AZURE_CLI_APP_ID, "delegatedPermissionIds": [scope_id]}]},
    })
    if not patch.ok:
        raise EntraError("application update failed (app recorded in ledger for cleanup): " + (patch.stderr or "")[-300:])
    sp = _graph(az, "POST", "servicePrincipals", {"appId": app["appId"], "tags": [MARKER]})
    if not sp.ok:
        raise EntraError("service principal creation failed (app recorded for cleanup): " + (sp.stderr or "")[-300:])
    ledger.created("entra_sp", sp.json()["id"], display_name, environment, run_id=run_id, appId=app["appId"])
    return {"appId": app["appId"], "objectId": app["id"], "audience": f"api://{app['appId']}"}
