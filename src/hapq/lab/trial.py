"""First Azure trial: the smallest path that produces real hosted-agent evidence.

Scope (approved): one Foundry account, one project, gpt-5-mini DataZoneStandard, Log Analytics + App Insights
(infra/trial/main.bicep), ONE hosted agent version without tool/toolbox deployed from source, at most two
synthetic calls in one reused session, then a bounded log read and a correlated trace query.
Not in scope: MCP tool, Container Apps, ACR, second project, Entra objects, role assignments, Phase B.

Enabled scenarios: S01, S09, S02, S10, S11. Everything else stays disabled here.
"""
from __future__ import annotations

import dataclasses
import secrets
from datetime import datetime, timezone

from ..azcli import Az
from ..checks import preflight
from ..config import REPO_ROOT, KitConfig
from ..ledger import Ledger
from ..results import CheckResult, Status
from ..tracking import tracked_deploy
from . import classify
from .profile import LabProfile

BICEP_TRIAL = REPO_ROOT / "infra" / "trial" / "main.bicep"
AGENT = "hapq-min"
GATE = ("PF-AUTH", "PF-SUB", "PF-RG", "PF-PROVIDERS", "PF-POLICIES", "PF-ROLES", "PF-MODEL-AVAILABLE", "PF-MODEL-QUOTA")
ENABLED = ("S01-GA-CREATE", "S09-OBS-STATUS", "S02-INVOKE", "S10-OBS-LOGS", "S11-OBS-TRACES")


def trial_limits(cfg: KitConfig) -> KitConfig:
    """Trial-specific caps: 1 version, 1 session, 4 calls (2 agent calls + up to 2 log reads)."""
    lim = dataclasses.replace(cfg.limits, max_agent_versions=1, max_sessions=1, max_calls=4,
                              max_total_tokens=min(cfg.limits.max_total_tokens, 20000))
    return dataclasses.replace(cfg, limits=lim)


def plan(cfg: KitConfig, suffix: str) -> list[str]:
    return [
        f"gate: read-only preflight {', '.join(GATE)}",
        f"what-if guard: refuse any change to an object not created by the kit ({cfg.resource_group})",
        f"deploy infra/trial/main.bicep in {cfg.lab_region}: account hapq-a-{suffix}, proj1, "
        f"{cfg.model.name} {cfg.model.sku} {cfg.model.capacity}K TPM, Log Analytics (1 GB/day cap), App Insights",
        f"agent: 1 version of '{AGENT}' from agents/minimal (no toolbox), {cfg.limits.cpu} vCPU / {cfg.limits.memory}, "
        f"idle timeout {cfg.limits.idle_timeout_s}s",
        f"calls: at most 2 synthetic prompts in one session, max_output_tokens={cfg.limits.max_output_tokens}",
        f"reads: session log stream (<=60 s), correlated trace query (<= {cfg.limits.trace_wait_s} s)",
    ]


def deploy(cfg: KitConfig, az: Az, ledger: Ledger, suffix: str, confirm: str, run_id: str, log=print) -> LabProfile:
    if confirm != cfg.resource_group:
        raise PermissionError("confirmation does not match the resource-group name; nothing created")
    gate = {r.check_id: r for r in preflight.run_all(cfg, az) if r.check_id in GATE}
    bad = [f"{k}: {r.status.value} ({r.observed})" for k, r in gate.items() if r.status is not Status.PASS]
    bad += [f"{k}: not run" for k in set(GATE) - set(gate)]
    if bad:
        raise RuntimeError("preflight gate not passed: " + "; ".join(bad))
    log("gate: PASS")
    params = {"location": cfg.lab_region, "suffix": suffix, "modelName": cfg.model.name,
              "modelVersion": cfg.model.version, "modelSku": cfg.model.sku, "modelCapacity": cfg.model.capacity}
    out = tracked_deploy(cfg, az, ledger, "A", f"hapq-trial-{suffix}", BICEP_TRIAL, params, run_id)
    state = ledger.state()
    new = [r for r in out["recorded"]["created"] if state.get(("arm", r)) and state[("arm", r)].run_id == run_id]
    log(f"infra: {len(new)} object(s) newly created in this run, "
        f"{len(out['recorded']['created']) + len(out['recorded']['preexisting']) - len(new)} already kit-owned or untouched; "
        f"foreign objects modified: 0 (what-if guard)")
    o = out["outputs"]
    profile = LabProfile(environment="A", region=o["accountLocation"], account_name=o["accountName"],
                         account_id=o["accountId"], project_names=[o["projectName"]],
                         project_endpoints=[o["projectEndpoint"]], project_principal_ids=[o["projectPrincipalId"]],
                         model_deployment=cfg.model.name, app_insights_app_id=o["appInsightsAppId"],
                         app_insights_id=o["appInsightsId"], mcp_url="", mcp_audience="", mcp_app_id="")
    log(f"profile: {profile.write().name}")
    return profile


def run(rt) -> list[CheckResult]:
    """Enabled scenarios only. rt is a scenarios.Runtime built with trial_limits()."""
    from .scenarios import AGENTS_DIR

    project = rt.profile.project_names[0]
    start = datetime.now(timezone.utc)

    def s01():
        d = rt.deploy(project, AGENT, AGENTS_DIR / "minimal", {"AZURE_AI_MODEL_DEPLOYMENT_NAME": rt.profile.model_deployment})
        rt.state["min"] = d
        st, msg = classify.version_outcome(d["history"], d["final"], d["error"])
        rt.record("S01-GA-CREATE", "C01", st, msg + " (v1 API, no preview header, no feature registration)",
                  version=d["version"], status_history=d["history"], error=d["error"])
        return rt.record("S09-OBS-STATUS", "C07",
                         Status.PASS if d["history"] and d["final"] in ("active", "failed") else Status.FAIL,
                         f"{len(d['history'])} status read(s), final '{d['final']}'")
    rt.guarded("S01-GA-CREATE", "C01", s01)

    if rt.passed("S01-GA-CREATE"):
        def s02():
            n1, n2 = secrets.token_hex(6), secrets.token_hex(6)
            first = rt.invoke(project, AGENT, f"NONCE:{n1} Say hello in five words.")
            rt.state.update(session=first["session"], response_id=first["id"])
            calls = [{"nonce_in_answer": classify.contains_nonce(first["text"], n1), "answer": first["text"][:300],
                      "status": first["status"], "usage": first["usage"], "session_id_returned": bool(first["session"])}]
            second = None
            if first["session"]:
                second = rt.invoke(project, AGENT, f"NONCE:{n2} Say goodbye in five words.",
                                   session=first["session"], previous_response_id=first["id"])
                calls.append({"nonce_in_answer": classify.contains_nonce(second["text"], n2),
                              "answer": second["text"][:300], "status": second["status"], "usage": second["usage"],
                              "same_session": second["session"] in (None, first["session"])})
            checks = [classify.answer_check(first["text"], n1, first["usage"], first["max_output_tokens"])]
            if second:
                checks.append(classify.answer_check(second["text"], n2, second["usage"], second["max_output_tokens"]))
            for c, (st_i, msg_i) in zip(calls, checks):
                c["check"] = f"{st_i.value}: {msg_i}"
            statuses = [st_i for st_i, _ in checks]
            st = (Status.FAIL if Status.FAIL in statuses else
                  Status.NOT_TESTED if Status.NOT_TESTED in statuses else Status.PASS)
            return rt.record("S02-INVOKE", "C02.a", st,
                             f"{len(calls)} call(s): " + "; ".join(m for _, m in checks) +
                             f"; session reused={bool(second) and calls[-1].get('same_session')}", calls=calls)
        def s02_diagnosed():
            try:
                return s02()
            except Exception as exc:
                code = getattr(exc, "status_code", None)
                if code == 424:
                    sid = classify.session_id_from_error(str(exc))
                    lines = rt.session_logs(project, AGENT, rt.state["min"]["version"], sid, wait_s=30) if sid else []
                    st, msg = classify.session_not_ready(lines)
                    return rt.record("S02-INVOKE", "C02.a", st, f"HTTP 424 session_not_ready; {msg}")
                raise
        rt.guarded("S02-INVOKE", "C02.a", s02_diagnosed)
    else:
        rt.skip("S02-INVOKE", "C02.a", "S01-GA-CREATE")

    if rt.state.get("session"):
        def s10():
            lines = rt.session_logs(project, AGENT, rt.state["min"]["version"], rt.state["session"])
            return rt.record("S10-OBS-LOGS", "C07", Status.PASS if lines else Status.FAIL,
                             f"{len(lines)} log line(s) read for the tested session", line_count=len(lines))
        rt.guarded("S10-OBS-LOGS", "C07", s10)
    else:
        rt.skip("S10-OBS-LOGS", "C07", "S02-INVOKE (no session id)")

    if rt.passed("S02-INVOKE"):
        def s11():
            try:
                corr, recent, err = rt.correlated_traces(start, [rt.state.get("response_id"), rt.state.get("session")])
            except PermissionError as exc:
                return rt.record("S11-OBS-TRACES", "C07", Status.BLOCKED, str(exc))
            st, msg = classify.trace_correlation(corr, recent)
            return rt.record("S11-OBS-TRACES", "C07", st, msg + (f"; last query error {err}" if err else ""))
        rt.guarded("S11-OBS-TRACES", "C07", s11)
    else:
        rt.skip("S11-OBS-TRACES", "C07", "S02-INVOKE")

    for r in rt.results:
        r.details["kit_usage"] = rt.guard.summary()
        r.details["trial"] = True
    return rt.results
