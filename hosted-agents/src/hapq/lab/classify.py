"""Pure decision rules turning observations into statuses (unit-tested, no Azure).

They encode the pass/fail definitions of config/criteria.toml so the rules are reviewable in one place.
"""
from __future__ import annotations

import re

from ..results import Status


def contains_nonce(text: str | None, nonce: str) -> bool:
    return bool(text) and nonce in text


def expected_refusal(code: int | None, expected: int) -> tuple[Status, str]:
    """A refusal test: exact expected code -> PASS; 2xx -> FAIL; anything else is inconclusive."""
    if code is None:
        return Status.BLOCKED, "no HTTP answer (network error)"
    if code == expected:
        return Status.PASS, f"HTTP {code} as expected"
    if 200 <= code < 300:
        return Status.FAIL, f"HTTP {code}: access granted where a refusal ({expected}) was expected"
    return Status.NOT_TESTED, f"inconclusive: HTTP {code}, expected {expected}"


_NETWORK_HINTS = re.compile(r"virtual network|firewall|private endpoint|public network access|not allowed from", re.I)


def network_refusal(code: int | None, body: str) -> tuple[Status, str]:
    """B: valid token from the public internet. Only a 403 that names network rules proves network denial."""
    if code is None:
        return Status.NOT_TESTED, "no HTTP answer: cannot distinguish a network denial from a local connectivity problem"
    if 200 <= code < 300:
        return Status.FAIL, f"HTTP {code}: endpoint reachable from the public internet with a valid token"
    if code == 403 and _NETWORK_HINTS.search(body or ""):
        return Status.PASS, "HTTP 403 with a network-rule error"
    if code == 401:
        return Status.FAIL, "HTTP 401: authentication refusal, says nothing about network policy (test invalid)"
    return Status.NOT_TESTED, f"inconclusive: HTTP {code} without network-rule error"


def agent_identity(calls: list[dict], expected_principal: str | None) -> tuple[Status, str]:
    """S07: PASS only if the tool validated a token whose principal IS the expected agent identity."""
    if not expected_principal:
        return Status.NOT_TESTED, "expected agent identity not available from the platform: cannot compare"
    if not calls:
        return Status.FAIL, "no call with this nonce recorded by the MCP tool"
    oids = [c.get("claims", {}).get("oid") for c in calls]
    if any(o == expected_principal for o in oids if o):
        return Status.PASS, "tool validated a token whose principal is the expected agent identity"
    if not any(oids):
        return Status.FAIL, "token validated but carries no principal id (oid): identity not established"
    return Status.FAIL, f"tool received another principal ({len(set(o for o in oids if o))} distinct), not the agent identity"


def cross_project_tool(version_status: str, tool_calls: list[dict], refusals: list[str]) -> tuple[Status, str, dict]:
    """S12. Status answers the NEED (can a tool of project 1 be consumed from project 2?).

    Evidence is technical only: MCP-side call records and HTTP refusals in container logs, never model text.
    Returns (status for the need, observation, {observed, documentary_hypothesis, matches_documentation}).
    """
    hyp = "documentation suggests toolboxes are project-scoped (refusal expected)"
    if tool_calls:
        return (Status.PASS, "the tool received a call from the project-2 agent through project 1's toolbox",
                {"observed": "consumed", "documentary_hypothesis": hyp, "matches_documentation": False})
    if version_status != "active":
        return (Status.NOT_TESTED, f"inconclusive: project-2 agent version is '{version_status}'",
                {"observed": "unknown", "documentary_hypothesis": hyp, "matches_documentation": None})
    if refusals:
        return (Status.FAIL, "toolbox refused the cross-project call: " + "; ".join(refusals[:2]),
                {"observed": "refused", "documentary_hypothesis": hyp, "matches_documentation": True})
    return (Status.NOT_TESTED, "inconclusive: no call reached the tool and no technical refusal was captured",
            {"observed": "unknown", "documentary_hypothesis": hyp, "matches_documentation": None})


# An HTTP status, not any number or word: "HTTP/1.1 403", "403 Forbidden", "Client error '403"
_REFUSAL_LINE = re.compile(r"HTTP/1\.[01]\W{0,8}(401|403)\b|\b(401 Unauthorized|403 Forbidden)\b|Client error\W{0,8}(401|403)\b",
                           re.I)


def technical_refusals(log_lines: list[str], marker: str) -> list[str]:
    """HTTP refusals in container logs that mention the toolbox endpoint (marker); model text is never used.

    Log streams arrive as multi-line chunks: each chunk is split into single log messages first, so the status and
    the marker must appear in the SAME message (2026-10-02: chunk-level matching produced false refusals)."""
    out = []
    for chunk in log_lines:
        for ln in re.split(r'\n|"message":"', chunk):
            if marker in ln and _REFUSAL_LINE.search(ln):
                out.append(ln[:220])
    return out


def a2a_agent_text(payload: dict) -> str:
    """Text produced by the agent in an A2A JSON-RPC result (agent message parts or task artifacts).

    History and user-authored messages (including the echo of the request) are excluded.
    """
    result = (payload or {}).get("result") or {}
    texts: list[str] = []

    def parts_text(parts):
        return [p.get("text", "") for p in parts or [] if isinstance(p, dict) and p.get("text")]

    def is_agent(m):
        return str((m or {}).get("role", "")).lower() in ("role_agent", "agent")

    msg = result.get("message") if isinstance(result.get("message"), dict) else (result if "parts" in result else None)
    if msg and is_agent(msg):
        texts += parts_text(msg.get("parts"))
    task = result.get("task") if isinstance(result.get("task"), dict) else (result if "artifacts" in result else None)
    if task:
        for art in task.get("artifacts") or []:
            texts += parts_text(art.get("parts"))
        status_msg = (task.get("status") or {}).get("message")
        if is_agent(status_msg):
            texts += parts_text(status_msg.get("parts"))
    return "\n".join(texts)


def trace_correlation(correlated_rows: int, recent_rows: int) -> tuple[Status, str]:
    """S11: only telemetry carrying the tested request's id counts."""
    if correlated_rows > 0:
        return Status.PASS, f"{correlated_rows} telemetry row(s) carry the tested request/session id"
    return Status.NOT_TESTED, (f"inconclusive: no row carries the tested ids within the wait limit "
                               f"({recent_rows} uncorrelated recent row(s) ignored)")


def inventory(found: dict[str, list[str]], expected: dict[str, list[str]]) -> tuple[Status, str]:
    """S13: every expected agent listed under its own project."""
    missing = {p: sorted(set(a) - set(found.get(p, []))) for p, a in expected.items()}
    missing = {p: a for p, a in missing.items() if a}
    if missing:
        return Status.FAIL, f"agents missing from enumeration: {missing}"
    return Status.PASS, "all agents enumerated with project attribution (aggregation of per-project calls)"


def version_outcome(history: list[str], final: str, error: str | None) -> tuple[Status, str]:
    if final == "active":
        return Status.PASS, f"status sequence {' -> '.join(dict.fromkeys(history))}"
    if final == "failed" and error and '"CodeError"' in error:  # our package/code, not the platform
        return Status.NOT_TESTED, f"kit defect: the platform rejected the agent package: {error[:200]}"
    if final == "failed":
        msg = (error or "")[:200]
        preview = "preview" in msg.lower() or "feature" in msg.lower()
        return Status.FAIL, f"version failed{' (mentions preview/feature)' if preview else ''}: {msg}"
    return Status.NOT_TESTED, f"timeout while in status '{final}'"


def session_not_ready(log_lines: list[str]) -> tuple[Status, str]:
    """HTTP 424 session_not_ready: separate a crash of the kit's agent code from a platform problem."""
    crash = [ln for ln in log_lines if "Traceback" in ln or "Error:" in ln]
    if crash:
        last = next((ln for ln in reversed(log_lines) if "Error" in ln), crash[-1])
        return Status.NOT_TESTED, f"kit defect: the agent container crashed at startup ({last[:200]})"
    return Status.FAIL, "platform: session did not become ready and the container logs show no crash"


def session_id_from_error(text: str) -> str | None:
    m = re.search(r"Session '([0-9a-zA-Z_-]+)'", text or "")
    return m.group(1) if m else None


def answer_check(text: str, nonce: str, usage: dict | None, max_output_tokens: int) -> tuple[Status, str]:
    """An empty answer whose output budget was consumed is a kit limit problem, not an agent failure."""
    if contains_nonce(text, nonce):
        return Status.PASS, "nonce present in the answer"
    out = (usage or {}).get("output")
    if not text and out is not None and out >= int(max_output_tokens * 0.8):
        return Status.NOT_TESTED, (f"empty answer: output budget exhausted ({out}/{max_output_tokens} tokens, "
                                   f"reasoning {(usage or {}).get('reasoning')}): kit limit too low")
    if not text:
        return Status.FAIL, "empty answer although the output budget was not exhausted"
    return Status.FAIL, "answer without the nonce"
