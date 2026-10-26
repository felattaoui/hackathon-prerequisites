"""Redaction applied to everything the kit prints or writes.

Nothing that leaves a check (results, logs, notebook output) may contain a subscription or
tenant id, an access token, a key or a connection string. Redaction is applied on write, so a
check author cannot forget it.
"""
from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # JWT access tokens (header.payload.signature, base64url)
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"), "<token>"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer <token>"),
    # Connection strings and key-value secrets
    (re.compile(r"(?i)(InstrumentationKey|AccountKey|SharedAccessKey|ApiKey|Password|sig)=[^;\s\"']+"), r"\1=<secret>"),
    (re.compile(r"(?i)(\"(?:key1|key2|primaryKey|secondaryKey|accessToken|refresh_token|client_secret)\"\s*:\s*\")[^\"]+"), r"\1<secret>"),
    # GUIDs (subscription, tenant, object ids). No left boundary: in JSON logs a GUID can follow an escape such as
    # "`", whose last character is a hex digit.
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9a-fA-F])"), "<guid>"),
    # User principal names / e-mail addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<upn>"),
]


def redact_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# Hostnames of live Azure endpoints (account, Container Apps, registry, storage). Masked only in published documents
# (the generated matrix): kit steps compare hostnames inside their own result files, so the runtime redaction keeps them.
_PUBLIC_HOSTS = re.compile(
    r"\b[a-z0-9][a-z0-9-]*\.(?:(?:services\.ai|cognitiveservices|openai)\.azure\.com|"
    r"[a-z0-9-]+\.[a-z0-9-]+\.azurecontainerapps\.io|azurecr\.io|blob\.core\.windows\.net)", re.I)


def redact_for_publication(text: str) -> str:
    """redact_text + live endpoint hostnames, for documents meant to be shared (e.g. docs/criteria-matrix.md)."""
    return _PUBLIC_HOSTS.sub(lambda m: "<host>." + m.group(0).split(".", 1)[1], redact_text(text))


def redact(value: Any) -> Any:
    """Recursively redact strings inside dicts, lists and tuples."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {redact_text(str(k)): redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value
