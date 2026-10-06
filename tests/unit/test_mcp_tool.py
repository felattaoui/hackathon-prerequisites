"""The test MCP tool rejects missing/foreign tokens and records validated callers.

Uses a locally generated RSA key: this tests the validation logic, it is not Azure evidence.
"""
import sys
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from hapq.config import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools" / "mcp_whoami"))
import app as mcp_app  # noqa: E402
from auth import InvalidToken, ValidatorConfig, validate  # noqa: E402

TENANT = "11111111-2222-3333-4444-555555555555"
AUD = "api://00000000-0000-0000-0000-000000000000"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CFG = ValidatorConfig(TENANT, (AUD,))


def token(aud=AUD, key=KEY, iss=None, exp_delta=600, oid="agent-oid"):
    now = int(time.time())
    return jwt.encode({"aud": aud, "iss": iss or f"https://login.microsoftonline.com/{TENANT}/v2.0", "iat": now,
                       "exp": now + exp_delta, "oid": oid, "tid": TENANT, "idtyp": "app", "azp": "agent-app"},
                      key, algorithm="RS256")


def resolver(_token):
    return KEY.public_key()


def test_valid_token_returns_safe_claims_only():
    claims = validate(token(), CFG, resolver)
    assert claims["oid"] == "agent-oid" and claims["idtyp"] == "app"
    assert "tid" not in claims and TENANT not in str(claims)


@pytest.mark.parametrize("bad", [
    lambda: token(aud="https://ai.azure.com"),
    lambda: token(key=OTHER),
    lambda: token(iss="https://login.microsoftonline.com/other/v2.0"),
    lambda: token(exp_delta=-60),
    lambda: "not-a-jwt",
])
def test_invalid_tokens_rejected(bad):
    with pytest.raises(InvalidToken):
        validate(bad(), CFG, resolver)


@pytest.fixture
def client():
    from starlette.testclient import TestClient
    mcp_app.CALLS.clear()
    with TestClient(mcp_app.build_app(CFG, resolver, allowed_hosts=["testserver"])) as c:
        yield c


HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
CALL = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "whoami", "arguments": {"nonce": "n-123"}}}


def test_mcp_without_token_is_401(client):
    assert client.post("/mcp", json=CALL, headers=HEADERS).status_code == 401
    assert client.get("/calls?nonce=n-123").status_code == 401
    assert client.get("/healthz").status_code == 200


def test_mcp_wrong_audience_is_401(client):
    h = {**HEADERS, "Authorization": f"Bearer {token(aud='https://ai.azure.com')}"}
    assert client.post("/mcp", json=CALL, headers=h).status_code == 401


def test_whoami_records_validated_principal(client):
    h = {**HEADERS, "Authorization": f"Bearer {token()}"}
    init = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    assert client.post("/mcp", json=init, headers=h).status_code == 200
    resp = client.post("/mcp", json=CALL, headers=h)
    assert resp.status_code == 200, resp.text
    recorded = client.get("/calls?nonce=n-123", headers={"Authorization": f"Bearer {token(oid='reader')}"}).json()
    assert len(recorded) == 1 and recorded[0]["claims"]["oid"] == "agent-oid"


def test_probe_rejects_invalid_agent_name(client, monkeypatch):
    monkeypatch.setenv("PROBE_PROJECT_ENDPOINT", "https://x.services.ai.azure.com/api/projects/p")
    r = client.get("/probe/a2a-card?agent=../../evil", headers={"Authorization": f"Bearer {token()}"})
    assert r.status_code == 400


def test_unknown_host_header_refused():
    from starlette.testclient import TestClient
    with TestClient(mcp_app.build_app(CFG, resolver, allowed_hosts=["mcp.example.test"])) as c:
        h = {**HEADERS, "Authorization": f"Bearer {token()}"}
        assert c.post("/mcp", json=CALL, headers=h).status_code == 421
