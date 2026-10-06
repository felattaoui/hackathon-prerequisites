"""Client-side execution guard.

Counts what the kit does (versions created, sessions opened, calls, tokens) and refuses to exceed the
configured limits. This bounds the kit's own activity; it is not a billing cap and cannot stop costs that
resources accrue on their own (private endpoints, VM, registry...).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from .config import Limits


class LimitExceeded(RuntimeError):
    pass


@dataclass
class Guard:
    limits: Limits
    versions: int = 0
    sessions: set = field(default_factory=set)
    calls: int = 0
    tokens: int = 0
    unknown_usage_calls: int = 0
    polls: int = 0
    _sem: threading.BoundedSemaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._sem = threading.BoundedSemaphore(self.limits.max_concurrency)

    def _check(self, name: str, value: int, limit: int) -> None:
        if value > limit:
            raise LimitExceeded(f"kit limit reached: {name} {value} > {limit}")

    def version(self) -> None:
        self._check("agent versions", self.versions + 1, self.limits.max_agent_versions)
        self.versions += 1

    def call(self) -> None:
        self._check("calls", self.calls + 1, self.limits.max_calls)
        self._check("tokens", self.tokens, self.limits.max_total_tokens)
        self.calls += 1

    def session(self, session_id: str | None) -> None:
        if session_id and session_id not in self.sessions:
            self._check("sessions", len(self.sessions) + 1, self.limits.max_sessions)
            self.sessions.add(session_id)

    def add_tokens(self, n: int | None) -> None:
        """Record usage reported by the service. None = not reported: counted as UNKNOWN, never as zero."""
        if n is None:
            self.unknown_usage_calls += 1
        else:
            self.tokens += int(n)

    def poll(self) -> None:
        self._check("status polls", self.polls + 1, self.limits.max_polls)
        self.polls += 1

    def slot(self):
        """Context manager limiting concurrent scenario execution."""
        if not self._sem.acquire(timeout=self.limits.call_timeout_s):
            raise LimitExceeded("kit limit reached: concurrency slot not available")
        guard = self

        class _Slot:
            def __enter__(self):
                return guard

            def __exit__(self, *exc):
                guard._sem.release()
                return False
        return _Slot()

    def summary(self) -> dict:
        tokens = (f">= {self.tokens} ({self.unknown_usage_calls} call(s) without usage data: unknown)"
                  if self.unknown_usage_calls else self.tokens)
        return {"versions": self.versions, "sessions": len(self.sessions), "calls": self.calls, "tokens": tokens,
                "polls": self.polls}
