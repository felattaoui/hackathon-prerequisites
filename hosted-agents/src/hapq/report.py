"""Aggregate recorded runs and render the criteria matrix.

Rules
- Only results written by the real runner are considered.
- Results are keyed by (check, region, environment, execution point): A and B are never merged.
- Latest result per key is shown; older ones are counted as superseded.
- A result is STALE if older than `stale_after_days`, or produced with another config hash or kit version.
- Documentary status and execution results are rendered in separate tables. Documentary results never
  enter a verdict. The verdict for an environment uses only that environment's execution results.
- Criteria with coverage "partial" can at best be "PARTIAL", with the limit printed.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import REPO_ROOT, KitConfig, load_toml


@dataclass
class Row:
    check_id: str
    status: str
    evidence: str
    environment: str = "-"
    execution_point: str = "-"
    title: str = ""
    region: str | None = None
    timestamp: str | None = None
    run_id: str | None = None
    observed: str = ""
    sources: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    superseded: int = 0
    details: dict = field(default_factory=dict)

    @property
    def run_label(self) -> str:
        return f"{self.environment}@{self.execution_point}"


def load_runs(results_dir: Path) -> list[dict]:
    rows = []
    for run in sorted(Path(results_dir).glob("*/results.jsonl")):
        ctx_file = run.parent / "context.json"
        ctx = json.loads(ctx_file.read_text(encoding="utf-8")) if ctx_file.exists() else {}
        if ctx.get("runner") != "real":
            continue
        for line in run.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                row["_ctx"] = ctx
                row.setdefault("environment", "docs" if row.get("evidence") == "documented" else "subscription")
                row.setdefault("execution_point", ctx.get("execution_point", "-"))
                rows.append(row)
    return rows


def latest_results(cfg: KitConfig, now: datetime | None = None) -> dict[tuple, Row]:
    now = now or datetime.now(timezone.utc)
    grouped: dict[tuple, list[dict]] = {}
    for r in load_runs(cfg.results_dir):
        key = (r["check_id"], r.get("region"), r["environment"], r["execution_point"])
        grouped.setdefault(key, []).append(r)
    out = {}
    for key, rows in grouped.items():
        rows.sort(key=lambda r: r["timestamp"])
        last = rows[-1]
        stale = []
        if (now - datetime.fromisoformat(last["timestamp"])).days >= cfg.stale_after_days:
            stale.append(f"older than {cfg.stale_after_days} days")
        if last["_ctx"].get("config_hash") != cfg.config_hash:
            stale.append("different configuration")
        if last["_ctx"].get("kit_version") != __version__:
            stale.append("different kit version")
        out[key] = Row(last["check_id"], last["status"], last["evidence"], last["environment"], last["execution_point"],
                       last.get("title", ""), last.get("region"), last["timestamp"], last["run_id"],
                       last.get("observed", ""), last.get("sources", []), stale, len(rows) - 1, last.get("details", {}))
    return out


def _find(latest: dict, check_id: str, environment: str | None = None, point: str | None = None) -> list[Row]:
    return [r for (cid, _, env, pt), r in latest.items()
            if cid == check_id and (environment is None or env == environment) and (point is None or pt == point)]


def scenario_cells(scenario: dict, latest: dict) -> list[Row]:
    """One row per declared run of a scenario: recorded result, or a synthesized BLOCKED/NOT_TESTED."""
    cells = []
    for run in scenario.get("runs", []):
        env, point = run.split("@", 1)
        found = _find(latest, scenario["id"], env, point)
        if found:
            cells.extend(found)
            continue
        if scenario.get("blocked_by"):
            cells.append(Row(scenario["id"], "BLOCKED", "-", env, point, observed=f"Blocked: {scenario['blocked_by']}"))
        elif run not in scenario.get("implemented", []):
            cells.append(Row(scenario["id"], "NOT_TESTED", "-", env, point,
                             observed="not implemented yet" + (" (phase B planned)" if env == "B" else "")))
        else:
            cells.append(Row(scenario["id"], "NOT_TESTED", "-", env, point,
                             observed="implemented, not run (requires provisioned lab and approval)"))
    if not scenario.get("runs"):
        cells.append(Row(scenario["id"], "NOT_TESTED", "-", "-", "-", observed=scenario.get("pass_if", "")))
    return cells


def verdict(criterion: dict, cells: list[Row], environment: str) -> str:
    if environment not in criterion.get("qualify_in", ["A", "B"]):
        return f"n/a (qualified in {'/'.join(criterion['qualify_in'])} only)"
    env_cells = [c for c in cells if c.environment == environment]
    if not env_cells:
        return "n/a (no execution scenario)"
    if any(c.status == "FAIL" for c in env_cells):
        return "NOT QUALIFIED (FAIL observed)"
    if all(c.status == "PASS" and not c.stale for c in env_cells):
        if criterion.get("coverage") == "partial":
            return "PARTIAL (see limit)"
        return "QUALIFIED"
    return "NOT DEMONSTRATED"


def _counts(rows: list[Row]) -> str:
    if not rows:
        return "-"
    c = Counter(("contradiction" if r.details.get("contradiction_present") else r.status) for r in rows)
    return ", ".join(f"{n} {k}" for k, n in sorted(c.items()))


def build_matrix(cfg: KitConfig, now: datetime | None = None) -> tuple[list[dict], dict]:
    catalog = load_toml("criteria.toml")
    scenarios = {s["id"]: s for s in catalog.get("scenario", [])}
    target = catalog.get("target_environment", "B")
    latest = latest_results(cfg, now)
    table = []
    for c in catalog["criterion"]:
        docs = [r for cid in c.get("docs", []) for r in (_find(latest, cid, "docs")
                or [Row(cid, "NOT_TESTED", "documented", "docs", "-", observed="not run yet")])]
        pre = [r for cid in c.get("preflight", []) for r in (_find(latest, cid, "subscription")
               or [Row(cid, "NOT_TESTED", "observed", "subscription", "-", observed="not run yet")])]
        cells = [cell for sid in c.get("scenarios", []) for cell in scenario_cells(scenarios[sid], latest)]
        table.append({"id": c["id"], "title": c["title"], "initial_claim": c["initial_claim"],
                      "coverage": c.get("coverage", "full"), "coverage_limit": c.get("coverage_limit", ""),
                      "conclusion": c.get("conclusion", ""),
                      "docs": docs, "preflight": pre, "cells": cells,
                      "verdict_A": verdict(c, cells, "A"), "verdict_B": verdict(c, cells, "B")})
    return table, {"scenarios": scenarios, "target": target}


def _cell(text: str, limit: int = 220) -> str:
    text = (text or "").replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fresh(r: Row) -> str:
    if not r.timestamp:
        return "-"
    out = ("STALE: " + ", ".join(r.stale)) if r.stale else "current"
    return out + (f" ({r.superseded} older)" if r.superseded else "")


def render_markdown(cfg: KitConfig, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    table, meta = build_matrix(cfg, now)
    out = [
        "# Criteria matrix — Foundry Hosted Agents prerequisites", "",
        f"Generated {now.isoformat(timespec='seconds')} by `hapq report` (kit {__version__}, config {cfg.config_hash}, "
        f"lab region `{cfg.lab_region}`). Do not edit by hand.", "",
        f"Qualification target: environment **{meta['target']}** (private lab). Environment A (public lab) is used to "
        "build and debug scenarios; an A result never qualifies B. Documentary status never enters a verdict. "
        "Checklist ticks are initial claims, not evidence. A success in the lab region does not validate North Europe.", "",
        "## Summary", "",
        f"| Criterion | Checklist initial claim | Documentary status (Learn) | Preflight (subscription) | Verdict A (public) | Verdict {meta['target']} (target) |",
        "|---|---|---|---|---|---|",
    ]
    for c in table:
        out.append(f"| **{c['id']}** {_cell(c['title'])} | {c['initial_claim']} | {_counts(c['docs'])} | "
                   f"{_counts(c['preflight'])} | {c['verdict_A']} | {c['verdict_B']} |")
    out += ["", "## Details", ""]
    for c in table:
        out += [f"### {c['id']} — {c['title']}", ""]
        if c["conclusion"]:
            out += [f"> **Observed conclusion:** {c['conclusion']}", ""]
        if c["coverage"] == "partial":
            out += [f"> Coverage limit: {c['coverage_limit']}", ""]
        if c["docs"]:
            out += ["**Documentary status** (what Microsoft Learn states; not a demonstration)", "",
                    "| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |", "|---|---|---|---|---|---|---|"]
            for r in c["docs"]:
                st = "contradiction" if r.details.get("contradiction_present") else r.status
                srcs = " ".join(f"[{i + 1}]({u})" for i, u in enumerate(r.sources))
                out.append(f"| {r.check_id} | {r.details.get('polarity', '-')} | {st} | {(r.timestamp or '-')[:19]} | "
                           f"{_fresh(r)} | {_cell(r.observed)} | {srcs} |")
            out.append("")
        if c["preflight"] or c["cells"]:
            out += ["**Execution results** (per environment and execution point)", "",
                    "| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |",
                    "|---|---|---|---|---|---|---|---|"]
            for r in c["preflight"] + c["cells"]:
                out.append(f"| {r.check_id} | {r.run_label} | {r.status} | {r.evidence} | {(r.timestamp or '-')[:19]} | "
                           f"{r.run_id or '-'} | {_fresh(r)} | {_cell(r.observed)} |")
            out.append("")
    return "\n".join(out)


def write_matrix(cfg: KitConfig, path: Path | None = None) -> Path:
    path = path or REPO_ROOT / "docs" / "criteria-matrix.md"
    from .redact import redact_for_publication
    path.write_text(redact_for_publication(render_markdown(cfg)), encoding="utf-8")
    return path
