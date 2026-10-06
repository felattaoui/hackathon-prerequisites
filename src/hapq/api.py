"""Entry points shared by the CLI and the notebooks (notebooks never re-implement checks)."""
from __future__ import annotations

from pathlib import Path

from .azcli import Az
from .checks import docs as docs_checks
from .checks import preflight
from .config import KitConfig, load_config
from .redact import redact_text
from .results import CheckResult, ExecutionContext, RunRecorder


def _record(cfg: KitConfig, az: Az, suite: str, results: list[CheckResult], record: bool) -> Path | None:
    if not record:
        return None
    ctx = ExecutionContext.capture(cfg, runner=az.runner.name, tools=preflight.collect_tool_versions(az))
    rec = RunRecorder(cfg.results_dir, ctx, suite=suite, protected_dir=cfg.results_dir)
    rec.extend(results)
    return rec.write()


def run_preflight(cfg: KitConfig | None = None, az: Az | None = None, record: bool = True):
    """Read-only checks against the subscription. Returns (results, run directory or None)."""
    cfg = cfg or load_config()
    az = az or Az(cfg.subscription_id)
    results = preflight.run_all(cfg, az)
    return results, _record(cfg, az, "preflight", results, record)


def run_docs(cfg: KitConfig | None = None, criteria: set[str] | None = None, record: bool = True,
             fetch: docs_checks.Fetcher = docs_checks.http_fetch):
    """Documentary checks against public Microsoft Learn pages."""
    cfg = cfg or load_config()
    results = docs_checks.run_assertions(fetch=fetch, criteria=criteria)
    if record and fetch is not docs_checks.http_fetch:
        raise PermissionError("Results from a substituted fetcher cannot be recorded as evidence.")
    return results, _record(cfg, Az(cfg.subscription_id), "docs", results, record)


def as_markdown(results: list[CheckResult], columns=("check_id", "status", "evidence", "region", "observed")) -> str:
    """Compact table for CLI and notebook display."""
    head = "| " + " | ".join(columns) + " |\n|" + "---|" * len(columns) + "\n"
    body = ""
    for r in results:
        cells = []
        for c in columns:
            v = getattr(r, c)
            v = getattr(v, "value", v)
            cells.append(str(v if v is not None else "-").replace("|", "\\|").replace("\n", " ")[:200])
        body += "| " + " | ".join(cells) + " |\n"
    return redact_text(head + body)
