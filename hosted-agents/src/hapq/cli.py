"""Command line: `python -m hapq <command>`.

Read-only by default. Anything that creates, calls paid APIs or deletes requires --execute AND --confirm
<resource-group>. Purging a soft-deleted Foundry account additionally requires --purge <account-name>.
"""
from __future__ import annotations

import argparse
import sys

from . import api, cleanup, manual, report
from .azcli import Az
from .config import load_config
from .ledger import Ledger
from .redact import redact_text
from .results import ExecutionContext, RunRecorder, Status


def _print(text: str) -> None:
    print(redact_text(text))


def _record(cfg, az, suite, results):
    from .checks.preflight import collect_tool_versions
    rec = RunRecorder(cfg.results_dir, ExecutionContext.capture(cfg, "real", collect_tool_versions(az)), suite)
    rec.extend(results)
    return rec.write()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hapq", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("preflight", help="read-only checks against the subscription in .env")
    sp.add_argument("--no-record", action="store_true")
    sd = sub.add_parser("docs", help="documentary checks against Microsoft Learn")
    sd.add_argument("--criterion", action="append")
    sd.add_argument("--no-record", action="store_true")
    sub.add_parser("report", help="regenerate docs/criteria-matrix.md from recorded results")
    sm = sub.add_parser("manual", help="list or record manual verifications")
    sm.add_argument("action", choices=["list", "record"])
    sm.add_argument("--id")
    sm.add_argument("--env", choices=["A", "B"])
    sm.add_argument("--status", choices=["PASS", "FAIL", "BLOCKED", "NOT_TESTED"])
    sm.add_argument("--observed", default="")
    sm.add_argument("--operator", default="operator")
    sl = sub.add_parser("lab", help="deploy a lab or run its scenarios (dry-run by default)")
    sl.add_argument("action", choices=["trial", "langgraph", "a2a", "a2a-repro", "mcp", "private-deploy", "private-run", "external-idp", "deploy", "run", "xproject"])
    sl.add_argument("--phase", choices=["A", "B"], required=True)
    sl.add_argument("--suffix", default="lab01")
    sl.add_argument("--budget-email", action="append", default=[], help="budget alert recipient (repeatable)")
    sl.add_argument("--scenarios-only", action="store_true", help="mcp: reuse deployed tool, run gate + scenarios only")
    sl.add_argument("--skip", action="append", default=[], help="mcp: scenario prefix to skip, e.g. S06")
    sl.add_argument("--steps", default="", help="private-run: comma-separated steps (default: all)")
    sl.add_argument("--execute", action="store_true")
    sl.add_argument("--confirm", default="")
    sub.add_parser("ledger", help="list objects created by the kit and still owned")
    sc = sub.add_parser("cleanup", help="delete kit-created objects in dependency order (dry-run by default)")
    sc.add_argument("--env", choices=["A", "B"])
    sc.add_argument("--execute", action="store_true")
    sc.add_argument("--confirm", default="")
    sc.add_argument("--include-resource-group", action="store_true",
                    help="also delete the kit-created resource group, only if it is empty afterwards")
    sc.add_argument("--purge", action="append", default=[], metavar="ACCOUNT",
                    help="IRREVERSIBLY purge this soft-deleted Foundry account (exact name, repeatable)")
    args = p.parse_args(argv)

    cfg = load_config()
    ledger = Ledger(cfg.ledger_path)
    if args.cmd == "preflight":
        results, run_dir = api.run_preflight(cfg, record=not args.no_record)
        _print(api.as_markdown(results))
        _print(f"recorded: {run_dir.name}" if run_dir else "not recorded")
        return 0
    if args.cmd == "docs":
        results, run_dir = api.run_docs(cfg, criteria=set(args.criterion) if args.criterion else None,
                                        record=not args.no_record)
        _print(api.as_markdown(results, columns=("check_id", "status", "evidence", "observed")))
        _print(f"recorded: {run_dir.name}" if run_dir else "not recorded")
        return 0
    if args.cmd == "report":
        _print(f"written: {report.write_matrix(cfg)}")
        return 0
    if args.cmd == "manual":
        if args.action == "list":
            for cid, m in manual.manual_checks().items():
                _print(f"{cid} [{', '.join(m.get('runs', []))}]: {m.get('pass_if', '')}")
                for i, s in enumerate(m["steps"], 1):
                    _print(f"   {i}. {s}")
            return 0
        if not (args.id and args.status and args.env):
            p.error("manual record needs --id, --env and --status")
        _print(f"recorded run {manual.record(cfg, args.id, args.env, args.status, args.observed, args.operator)}")
        return 0
    if args.cmd == "ledger":
        for e in ledger.owned():
            _print(f"{e.environment}  {e.kind:10} {e.event:14} {e.name}")
        return 0
    if args.cmd == "lab":
        return _lab(args, cfg, ledger)
    if args.cmd == "cleanup":
        return _cleanup(args, cfg, ledger)
    return 1


def _lab(args, cfg, ledger) -> int:
    if args.phase == "B" and args.action in ("deploy", "run"):
        _print("Phase B is planned but NOT implemented yet (infra/phase-b-private is a draft; runner orchestration, "
               "B scenarios S20-S26 and replay of S01-S15 from the in-VNet runner are pending).")
        return 2
    from .lab import deploy
    from .results import new_run_id
    if args.action == "trial":
        return _trial(args, cfg, ledger)
    if args.action == "xproject":
        return _xproject(args, cfg, ledger)
    if args.action in ("langgraph", "a2a", "external-idp"):
        return _qualify(args, cfg, ledger)
    if args.action == "a2a-repro":
        return _a2a_repro(args, cfg)
    if args.action == "mcp":
        return _mcp(args, cfg, ledger)
    if args.action == "private-deploy":
        return _private_deploy(args, cfg, ledger)
    if args.action == "private-run":
        return _private_run(args, cfg, ledger)
    if args.action == "deploy":
        steps = deploy.plan_phase_a(cfg, args.suffix, args.budget_email)
        for i, s in enumerate(steps, 1):
            _print(f"{i}. {s.name:8} {s.description}\n             creates: {s.creates}")
        if not args.execute:
            _print("dry-run only. Re-run with --execute --confirm <resource-group> after approval.")
            return 0
        deploy.execute_phase_a(cfg, Az(cfg.subscription_id), ledger, args.suffix, args.budget_email, args.confirm,
                               new_run_id(), log=_print)
        return 0
    # run
    lim = cfg.limits
    _print(f"Phase A scenarios S01-S15 against config/lab-a.local.toml. Limits: {lim.max_agent_versions} versions, "
           f"{lim.max_sessions} sessions, {lim.max_calls} calls, {lim.max_total_tokens} tokens, concurrency "
           f"{lim.max_concurrency}, {lim.max_output_tokens} output tokens/call, deploy timeout {lim.deploy_timeout_s}s.")
    if not args.execute:
        _print("dry-run only. Re-run with --execute --confirm <resource-group> (creates agent versions, paid calls).")
        return 0
    if args.confirm != cfg.resource_group:
        _print("confirmation does not match the resource-group name; nothing run")
        return 2
    from .lab.profile import LabProfile
    from .lab.scenarios import Runtime, run_phase_a
    rt = Runtime(cfg, LabProfile.load("A"), ledger, new_run_id())
    results = run_phase_a(rt)
    run_dir = _record(cfg, rt.az, "lab-A", results)
    _print(api.as_markdown(results))
    _print(f"recorded: {run_dir.name}; kit usage {rt.guard.summary()}")
    return 0


def _trial(args, cfg, ledger) -> int:
    """First Azure trial: minimal infra + one hosted agent without tools + at most two calls."""
    from .lab import trial
    from .lab.profile import LabProfile
    from .results import new_run_id
    tcfg = trial.trial_limits(cfg)
    for i, step in enumerate(trial.plan(tcfg, args.suffix), 1):
        _print(f"{i}. {step}")
    if not args.execute:
        _print("dry-run only. Re-run with --execute --confirm <resource-group>.")
        return 0
    if args.confirm != cfg.resource_group:
        _print("confirmation does not match the resource-group name; nothing done")
        return 2
    az, run_id = Az(cfg.subscription_id), new_run_id()
    try:
        profile = trial.deploy(tcfg, az, ledger, args.suffix, args.confirm, run_id, log=_print)
    except Exception as exc:
        _print(f"infra step failed: {type(exc).__name__}: {exc}")
        _print("objects created before the failure are in the ledger: `python -m hapq ledger`")
        return 1
    from .lab.scenarios import Runtime
    rt = Runtime(tcfg, profile, ledger, run_id, az)
    results = trial.run(rt)
    run_dir = _record(tcfg, az, "trial-A", results)
    _print(api.as_markdown(results))
    _print(f"recorded: {run_dir.name}; kit usage {rt.guard.summary()}")
    return 0


def _qualify(args, cfg, ledger) -> int:
    """Separate limited runs on the existing lab: `lab langgraph` (S08) or `lab a2a` (S03-S06)."""
    from .lab import qualify
    from .lab.profile import LabProfile
    from .lab.scenarios import Runtime
    from .results import new_run_id
    qcfg = qualify.limits_for(cfg, "a2a" if args.action == "external-idp" else args.action)
    agent = {"langgraph": qualify.AGENT_LG, "a2a": qualify.AGENT_A2A, "external-idp": "hapq-min (read-only call)"}[args.action]
    lim = qcfg.limits
    _print(f"{args.action}: reuse lab A profile (account, proj1, model, observability); test agent '{agent}' "
           f"(reference agent hapq-min untouched); limits {lim.max_agent_versions} version, {lim.max_sessions} session(s), "
           f"{lim.max_calls} calls, {lim.max_output_tokens} output tokens/call")
    if not args.execute:
        _print("dry-run only. Re-run with --execute --confirm <resource-group>.")
        return 0
    if args.confirm != cfg.resource_group:
        _print("confirmation does not match the resource-group name; nothing done")
        return 2
    az = Az(cfg.subscription_id)
    rt = Runtime(qcfg, LabProfile.load("A"), ledger, new_run_id(), az)
    results = {"langgraph": qualify.run_langgraph, "a2a": qualify.run_a2a,
               "external-idp": qualify.run_external_idp}[args.action](rt)
    run_dir = _record(qcfg, az, f"{args.action}-A", results)
    _print(api.as_markdown(results))
    _print(f"recorded: {run_dir.name}; kit usage {rt.guard.summary()}")
    return 0


def _xproject(args, cfg, ledger) -> int:
    """C06 on lab A: create proj2 alone (ledgered), then S12 (cross-project tool) and S13 (inventory)."""
    from .lab import xproject
    from .lab.profile import LabProfile
    from .lab.scenarios import Runtime
    from .results import new_run_id
    _print("xproject: proj2 on the A account if missing (ARM, ledgered), agent hapq-xp in proj2 consuming proj1's "
           "toolbox (S12), inventory across projects (S13); existing agents untouched")
    if not args.execute:
        _print("dry-run only. Re-run with --execute --confirm <resource-group>.")
        return 0
    if args.confirm != cfg.resource_group:
        _print("confirmation does not match the resource-group name; nothing done")
        return 2
    az, run_id = Az(cfg.subscription_id), new_run_id()
    xproject.ensure_proj2(cfg, az, ledger, run_id, log=_print)
    rt = Runtime(cfg, LabProfile.load("A"), ledger, run_id, az)
    import os
    results = xproject.run_rbac_only(rt) if os.environ.get("HAPQ_XP_STEP") == "rbac" else xproject.run(rt)
    run_dir = _record(cfg, az, "xproject-A", results)
    _print(api.as_markdown(results))
    _print(f"recorded: {run_dir.name}; kit usage {rt.guard.summary()}")
    return 0


def _a2a_repro(args, cfg) -> int:
    """Single full capture of the A2A request already observed, for a review dossier (nothing submitted)."""
    import dataclasses
    from .lab import a2a_repro
    from .lab.profile import LabProfile
    from .limits import Guard
    _print("a2a-repro: 1 Responses call + agent card GET + 1 SendMessage on hapq-a2a (same request as observed); "
           "writes results/a2a-repro/<stamp>/ (full local diagnostics + redacted dossier); submits nothing")
    if not args.execute:
        _print("dry-run only. Re-run with --execute --confirm <resource-group>.")
        return 0
    if args.confirm != cfg.resource_group:
        _print("confirmation does not match the resource-group name; nothing done")
        return 2
    rcfg = dataclasses.replace(cfg, limits=dataclasses.replace(cfg.limits, max_calls=3))
    out = a2a_repro.capture(rcfg, LabProfile.load("A"), Guard(rcfg.limits))
    _print(f"written: {out}")
    return 0


def _mcp(args, cfg, ledger) -> int:
    """Agent identity -> MCP tool on the existing lab A (C04.b). Requires approval of its Entra and Azure additions."""
    import dataclasses
    from .lab import mcp_step
    from .lab.scenarios import Runtime
    from .results import new_run_id
    for i, step in enumerate(mcp_step.plan(cfg, args.suffix), 1):
        _print(f"{i}. {step}")
    if not args.execute:
        _print("dry-run only. Re-run with --execute --confirm <resource-group> after approval.")
        return 0
    if args.confirm != cfg.resource_group:
        _print("confirmation does not match the resource-group name; nothing done")
        return 2
    az, run_id = Az(cfg.subscription_id), new_run_id()
    mcfg = dataclasses.replace(cfg, limits=dataclasses.replace(cfg.limits, max_agent_versions=1, max_sessions=1, max_calls=6))
    if args.scenarios_only:
        from .lab.profile import LabProfile
        profile = LabProfile.load("A")
        _print("scenarios only: reusing the deployed tool and profile")
    else:
        profile = mcp_step.deploy(mcfg, az, ledger, args.suffix, run_id, log=_print)
    rt = Runtime(mcfg, profile, ledger, run_id, az)
    rt.state["skip"] = tuple(args.skip)
    results = mcp_step.run(rt)
    run_dir = _record(mcfg, az, "mcp-A", results)
    _print(api.as_markdown(results))
    _print(f"recorded: {run_dir.name}; kit usage {rt.guard.summary()}")
    return 0


def _private_deploy(args, cfg, ledger) -> int:
    """Phase B infrastructure (pass 1). Pass 2 (MCP app) is run by the private scenarios once the image is pushed."""
    from .lab import phase_b
    from .results import new_run_id
    for i, step in enumerate(phase_b.plan(cfg, args.suffix), 1):
        _print(f"{i}. {step}")
    if not args.execute:
        _print("dry-run only. Re-run with --execute --confirm <resource-group>.")
        return 0
    if args.confirm != cfg.resource_group:
        _print("confirmation does not match the resource-group name; nothing done")
        return 2
    try:
        profile = phase_b.deploy(cfg, Az(cfg.subscription_id), ledger, args.suffix, new_run_id(), log=_print)
    except Exception as exc:
        _print(f"phase B deployment failed: {type(exc).__name__}: {exc}")
        _print("objects created before the failure are in the ledger: `python -m hapq ledger`")
        return 1
    _print(f"profile written for environment {profile.environment}: account {profile.account_name}")
    return 0


def _private_run(args, cfg, ledger) -> int:
    """Phase B scenarios: in-VNet runner (positive paths) and workstation (refusals with a valid token)."""
    from .lab import private_run
    from .results import new_run_id
    steps = [s for s in args.steps.split(",") if s]
    _print(f"private-run: steps {steps or 'all'} against config/lab-b.local.toml")
    if not args.execute:
        _print("dry-run only. Re-run with --execute --confirm <resource-group>.")
        return 0
    if args.confirm != cfg.resource_group:
        _print("confirmation does not match the resource-group name; nothing done")
        return 2
    az = Az(cfg.subscription_id)
    results = private_run.run(cfg, az, ledger, new_run_id(), steps, log=_print)
    run_dir = private_run.record(cfg, az, results, "private-B")
    _print(api.as_markdown(results))
    _print(f"recorded (last run dir): {run_dir.name if run_dir else '-'}")
    return 0


def _cleanup(args, cfg, ledger) -> int:
    az = Az(cfg.subscription_id)
    try:
        plan = cleanup.plan(cfg, az, ledger, args.env)
    except (cleanup.CleanupReadError, Exception) as exc:
        _print(f"BLOCKED: cannot build the cleanup plan ({type(exc).__name__}: {exc}). Nothing deleted.")
        return 2
    if not plan.steps:
        _print("ledger: no kit-created object owned" + (f" in environment {args.env}" if args.env else ""))
    for s in plan.steps:
        action = s.action.upper()
        if s.entry.kind == "resource_group" and not args.include_resource_group:
            action, s.reason = "KEEP", "(delete only with --include-resource-group, and only if empty)"
        _print(f"  {action:15} {s.entry.environment} {s.entry.kind:14} {s.entry.name}  {s.reason}")
    if not args.execute:
        _print("dry-run only. Re-run with --execute --confirm <resource-group> [--purge <account>].")
        return 0
    data_plane = None
    try:
        from .lab.scenarios import delete_data_plane
        data_plane = lambda e: delete_data_plane(cfg, e)  # noqa: E731
    except ImportError:
        pass
    report_ = cleanup.execute(cfg, az, ledger, plan, args.confirm, set(args.purge), data_plane,
                              include_rg=args.include_resource_group)
    for s in plan.steps:
        _print(f"  {s.outcome:15} {s.entry.kind:10} {s.entry.name} {s.error}")
    result = cleanup.as_result(report_, args.env or "subscription")
    _record(cfg, az, "cleanup", [result])
    _print(f"cleanup status: {result.status.value} — {result.observed}")
    return {Status.PASS: 0, Status.FAIL: 1}.get(result.status, 2)


if __name__ == "__main__":
    sys.exit(main())
