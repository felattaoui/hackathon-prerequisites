"""Entra bearer-token validation for the test MCP tool.

Validates signature (tenant JWKS), issuer (v1 or v2 for the configured tenant), audience (the kit app id URI or
app id) and expiry. Returns only non-secret claims. Tokens are never logged or stored.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

import jwt

# Claims safe to expose for evidence (identifiers of the caller, never the token itself).
SAFE_CLAIMS = ("oid", "appid", "azp", "idtyp", "aud", "ver", "xms_act_fct", "xms_sub_fct", "xms_par_app_azp",
               "roles", "scp")


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class ValidatorConfig:
    tenant_id: str
    audiences: tuple[str, ...]  # e.g. ("api://<appId>", "<appId>")

    @property
    def issuers(self) -> tuple[str, str]:
        return (f"https://login.microsoftonline.com/{self.tenant_id}/v2.0", f"https://sts.windows.net/{self.tenant_id}/")

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"


KeyResolver = Callable[[str], object]  # token -> signing key


def default_key_resolver(cfg: ValidatorConfig) -> KeyResolver:
    client = jwt.PyJWKClient(cfg.jwks_url, cache_keys=True)
    return lambda token: client.get_signing_key_from_jwt(token).key


def bearer(header_value: str | None) -> str:
    if not header_value or not header_value.lower().startswith("bearer "):
        raise InvalidToken("missing bearer token")
    return header_value.split(" ", 1)[1].strip()


def validate(token: str, cfg: ValidatorConfig, resolve_key: KeyResolver) -> dict:
    try:
        key = resolve_key(token)
        claims = jwt.decode(token, key=key, algorithms=["RS256"], audience=list(cfg.audiences),
                            issuer=list(cfg.issuers), options={"require": ["exp", "iat", "aud", "iss"]})
    except InvalidToken:
        raise
    except Exception as exc:  # signature, audience, issuer, expiry, malformed
        raise InvalidToken(type(exc).__name__) from None
    safe = {k: claims[k] for k in SAFE_CLAIMS if k in claims}
    safe["tid_sha256"] = hashlib.sha256(str(claims.get("tid", "")).encode()).hexdigest()[:12]
    safe["claim_names"] = sorted(claims)
    return safe
