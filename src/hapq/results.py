"""Structured results.

Every check produces a CheckResult with:
- status: PASS | FAIL | BLOCKED | NOT_TESTED
- evidence: documented (Learn pages) | observed (live Azure call) | manual (operator)
- environment: "subscription" (preflight), "docs" (Learn), "A" (public lab) or "B" (private lab).
  Results of different environments are never merged: a PASS in A says nothing about B.
- region, UTC timestamp, run id, tool versions and the declared execution point.

Results produced through a fake runner (unit tests) are tagged runner="fake" and can never be
written into the repository results directory: mocks are not evidence.
"""
from __future__ import annotations

import json
import platform
import secrets
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from . import __version__
from .redact import redact


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_TESTED = "NOT_TESTED"


ENVIRONMENTS = ("subscription", "docs", "A", "B")


class Evidence(str, Enum):
    DOCUMENTED = "documented"
    OBSERVED = "observed"
    MANUAL = "manual"


@dataclass
class CheckResult:
    check_id: str
    criterion: str
    title: str
    status: Status
    evidence: Evidence
    expected: str
    observed: str
    region: str | None = None
    sources: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    environment: str = ""

    def __post_init__(self) -> None:
        self.status = Status(self.status)
        self.evidence = Evidence(self.evidence)
        if not self.environment:
            self.environment = "docs" if self.evidence is Evidence.DOCUMENTED else "subscription"
        if self.environment not in ENVIRONMENTS:
            raise ValueError(f"unknown environment {self.environment!r}")


@dataclass
class ExecutionContext:
    execution_point: str
    runner: str  # "real" or "fake"
    kit_version: str
    config_hash: str
    subscription_fingerprint: str
    python: str
    os: str
    tools: dict[str, str] = field(default_factory=dict)

    @classmethod
    def capture(cls, cfg, runner: str, tools: dict[str, str] | None = None) -> "ExecutionContext":
        return cls(
            execution_point=cfg.execution_point,
            runner=runner,
            kit_version=__version__,
            config_hash=cfg.config_hash,
            subscription_fingerprint=cfg.subscription_fingerprint,
            python=sys.version.split()[0],
            os=f"{platform.system()} {platform.release()}",
            tools=tools or {},
        )


def new_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


class RunRecorder:
    """Append-only JSONL writer for one run: results/<run_id>/results.jsonl + context.json."""

    def __init__(self, results_dir: Path, context: ExecutionContext, suite: str,
                 protected_dir: Path | None = None, run_id: str | None = None):
        if context.runner != "real" and protected_dir is not None:
            if Path(results_dir).resolve().is_relative_to(Path(protected_dir).resolve()):
                raise PermissionError("Results from a fake runner cannot be written to the evidence directory.")
        self.run_id = run_id or new_run_id()
        self.dir = Path(results_dir) / self.run_id
        self.context = context
        self.suite = suite
        self.results: list[CheckResult] = []

    def add(self, result: CheckResult) -> CheckResult:
        self.results.append(result)
        return result

    def extend(self, results: list[CheckResult]) -> list[CheckResult]:
        for r in results:
            self.add(r)
        return results

    def write(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ctx = redact({"run_id": self.run_id, "suite": self.suite, "written_at": started, **asdict(self.context)})
        (self.dir / "context.json").write_text(json.dumps(ctx, indent=2), encoding="utf-8")
        with (self.dir / "results.jsonl").open("w", encoding="utf-8") as fh:
            for r in self.results:
                row = asdict(r)
                row.update(status=r.status.value, evidence=r.evidence.value, run_id=self.run_id,
                           timestamp=started, execution_point=self.context.execution_point,
                           runner=self.context.runner)
                fh.write(json.dumps(redact(row), ensure_ascii=False) + "\n")
        return self.dir
