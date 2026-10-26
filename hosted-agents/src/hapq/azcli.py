"""Thin wrapper around the az / azd / docker CLIs.

- The subscription is always passed explicitly (--subscription); the global az context is never
  changed by the kit.
- Output is parsed as JSON and never printed raw by the kit.
- A runner is injectable so the check logic can be unit-tested with canned responses
  (FakeRunner results are tagged runner="fake" and cannot become evidence).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def json(self) -> Any:
        return json.loads(self.stdout) if self.stdout.strip() else None


class Runner(Protocol):
    name: str

    def __call__(self, tool: str, args: list[str], timeout: int = 120) -> CmdResult: ...


class RealRunner:
    name = "real"

    def __call__(self, tool: str, args: list[str], timeout: int = 120) -> CmdResult:
        exe = shutil.which(tool)
        if exe is None:
            return CmdResult(127, "", f"{tool} not found on PATH")
        try:
            proc = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return CmdResult(124, "", f"{tool} timed out after {timeout}s")
        return CmdResult(proc.returncode, proc.stdout, proc.stderr)


class FakeRunner:
    """Test double: maps a predicate on (tool, args) to a canned CmdResult."""

    name = "fake"

    def __init__(self, routes: list[tuple[Callable[[str, list[str]], bool], CmdResult]]):
        self.routes = routes
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, tool: str, args: list[str], timeout: int = 120) -> CmdResult:
        self.calls.append((tool, args))
        for predicate, result in self.routes:
            if predicate(tool, args):
                return result
        return CmdResult(1, "", f"no fake route for {tool} {' '.join(args[:3])}")


class Az:
    """az calls scoped to one subscription."""

    def __init__(self, subscription_id: str, runner: Runner | None = None):
        self.subscription_id = subscription_id
        self.runner = runner or RealRunner()

    def json(self, *args: str, timeout: int = 120, scoped: bool = True) -> CmdResult:
        argv = list(args)
        if scoped:
            argv += ["--subscription", self.subscription_id]
        return self.runner("az", argv + ["-o", "json", "--only-show-errors"], timeout=timeout)

    def tool(self, tool: str, *args: str, timeout: int = 60) -> CmdResult:
        return self.runner(tool, list(args), timeout=timeout)
