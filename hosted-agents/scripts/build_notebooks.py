"""Builds the presentation notebooks from source cells (keeps them output-free and reviewable).

Run: python scripts/build_notebooks.py
"""
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]


def md(text):
    return nbf.v4.new_markdown_cell(text.strip())


def code(text):
    return nbf.v4.new_code_cell(text.strip())


NB00 = [
    md("""
# 00 — Preflight and lab region

**Requirement.** Before any hosted-agent test, establish (1) that the lab subscription, identity, policies,
providers, model and quota allow the scenario in the chosen lab region, and (2) what Microsoft Learn states
about hosted-agent regional availability, including North Europe (checklist criterion C05).

**What this notebook does.** Read-only calls to the subscription in `.env` and public Learn pages.
It provisions nothing, cleans up nothing, and changes no Azure configuration. Every check comes from the
`hapq` package; this notebook only calls it and displays results.

**How to read results.** `observed` = live call by this kit, `documented` = Learn statement on the fetch date.
Neither is a demonstration of hosted-agent capacity: that requires the Phase A lab (pending approval).
"""),
    md("## 1. Configuration"),
    code(r"""
from datetime import datetime, timezone
from IPython.display import Markdown, display

from hapq import api, report
from hapq.config import load_config
from hapq.redact import redact_text

cfg = load_config()  # subscription id from .env; only a fingerprint is ever shown
RECORD = True        # write results to results/<run_id>/ (local, git-ignored)

display(Markdown(f'''
| Setting | Value |
|---|---|
| Subscription | `{cfg.subscription_fingerprint}` (fingerprint, the id is never displayed) |
| Resource group | `{cfg.resource_group}` |
| Lab region | `{cfg.lab_region}` |
| Region tracked separately | `{cfg.required_region}` |
| Model | `{cfg.model.name}` {cfg.model.version}, {cfg.model.sku}, {cfg.model.capacity}K TPM |
| Declared execution point | `{cfg.execution_point}` |
| Config hash | `{cfg.config_hash}` |
| Executed at (UTC) | {datetime.now(timezone.utc).isoformat(timespec="seconds")} |
'''))
"""),
    md("""
## 2. Execution — read-only preflight

Expected: identity authenticated on the `.env` subscription, kit resource group present and tagged, providers
registered, no policy excluding the lab region, provisioning + Foundry data-plane roles, model offered with
enough quota. `PF-ARM-LOCATIONS-REQUIRED` is expected to stay `NOT_TESTED`: ARM metadata cannot prove
hosted-agent support in North Europe.
"""),
    code(r"""
preflight_results, preflight_run = api.run_preflight(cfg, record=RECORD)
display(Markdown(api.as_markdown(preflight_results)))
print("run:", preflight_run.name if preflight_run else "not recorded")
"""),
    md("""
## 3. Execution — documentary region checks (C05)

Fetches the current Learn pages and evaluates the region assertions from `config/doc_assertions.toml`.
"""),
    code(r"""
doc_results, doc_run = api.run_docs(cfg, criteria={"C05.a", "C05.b", "C05.c", "C05.d"}, record=RECORD)
display(Markdown(api.as_markdown(doc_results, columns=("check_id", "status", "evidence", "observed"))))
print("run:", doc_run.name if doc_run else "not recorded")
"""),
    md("## 4. Expected vs observed"),
    code(r"""
expected = {
    "PF-AUTH": "PASS", "PF-RG": "PASS", "PF-POLICIES": "PASS", "PF-ROLES": "PASS",
    "PF-MODEL-AVAILABLE": "PASS", "PF-MODEL-QUOTA": "PASS",
    "PF-ARM-LOCATIONS-REQUIRED": "NOT_TESTED",
    "DOC-C05-WESTEUROPE": "PASS",
    "DOC-C05-NORTHEUROPE": "FAIL",   # initial checklist claim: not listed
}
observed = {r.check_id: r.status.value for r in preflight_results + doc_results}
rows = "\n".join(f"| {k} | {v} | {observed.get(k, 'missing')} | {'yes' if observed.get(k) == v else '**NO**'} |"
                  for k, v in expected.items())
display(Markdown("| Check | Expected | Observed | Match |\n|---|---|---|---|\n" + rows))
"""),
    md("""
## 5. Criteria matrix excerpt and freshness

The matrix keeps the documentary layer and the execution layer apart; verdicts use execution results only,
per environment. Results older than the configured window, or produced with another configuration or kit
version, are flagged STALE.
"""),
    code(r"""
table, _ = report.build_matrix(cfg)
lines = ["| Criterion | Layer | Check | Env@point | Status | Run | Freshness |", "|---|---|---|---|---|---|---|"]
for c in table:
    if c["id"].startswith("C05"):
        for layer, rows in (("documentary", c["docs"]), ("execution", c["preflight"] + c["cells"])):
            for r in rows:
                fresh = "-" if not r.timestamp else ("STALE: " + ", ".join(r.stale) if r.stale else "current")
                lines.append(f"| {c['id']} | {layer} | {r.check_id} | {r.run_label} | {r.status} | {r.run_id or '-'} | {fresh} |")
display(Markdown(redact_text("\n".join(lines))))
display(Markdown("**Verdicts (execution only):** " + "; ".join(
    f"{c['id']}: A = {c['verdict_A']}, B = {c['verdict_B']}" for c in table if c["id"].startswith("C05"))))
"""),
    md("""
## 6. Limits

- Documented availability is not capacity: West Europe becomes *demonstrated* only when a hosted agent version
  reaches `active` there (scenario `S01-GA-CREATE`, after approval).
- A success in West Europe does not validate North Europe. North Europe is not deployed to by design; its status
  rests on documentation and is re-evaluated each time this notebook runs.
- Quota is not guaranteed regional capacity; catalog listing is not deployment success.
- The policy check evaluates location restrictions only; network-related deny policies surface at deployment.
- Learn pages change: each documentary result stores the page hash and fetch time.
"""),
]


NB01 = [
    md("""
# 01 — Phase A: hosted-agent scenarios on the public lab

**Requirement.** Build and debug the hosted-agent scenarios (GA path, invoke, A2A, agent identity to a tool,
LangGraph, observability, cross-project) on a public lab, so that Phase B can replay them in the private
configuration that must be qualified.

**What this notebook does.** Read-only: deployment plan (dry-run), execution limits, pass/fail definitions and
recorded results per environment. It never deploys, never creates agents, never calls paid APIs and never cleans
up. Those actions run from the CLI after approval (`hapq lab deploy|run --phase A`, `hapq cleanup`), each dry-run
unless `--execute --confirm <resource-group>` is given.
"""),
    md("## 1. Configuration and limits"),
    code(r"""
from IPython.display import Markdown, display

from hapq import report
from hapq.config import load_config, load_toml
from hapq.lab import deploy
from hapq.lab.profile import LabProfile
from hapq.ledger import Ledger
from hapq.redact import redact_text

cfg = load_config()
lim = cfg.limits
rows = [("Agent versions per run", lim.max_agent_versions), ("Sessions per run", lim.max_sessions),
        ("Calls per run", lim.max_calls), ("Tokens per run", lim.max_total_tokens),
        ("Output tokens per call", lim.max_output_tokens), ("Concurrency", lim.max_concurrency),
        ("Sandbox", f"{lim.cpu} vCPU / {lim.memory}, idle timeout {lim.idle_timeout_s}s"),
        ("Timeouts", f"call {lim.call_timeout_s}s, deploy {lim.deploy_timeout_s}s, traces {lim.trace_wait_s}s")]
display(Markdown("| Limit (client side, NOT a billing cap) | Value |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)))
"""),
    md("## 2. Deployment plan (dry-run, nothing is created)"),
    code(r"""
steps = deploy.plan_phase_a(cfg, suffix="lab01", budget_emails=["<recipient>"])
display(Markdown("| # | Step | What | Creates |\n|---|---|---|---|\n" + "\n".join(
    f"| {i} | {s.name} | {s.description} | {s.creates} |" for i, s in enumerate(steps, 1))))
try:
    LabProfile.load("A")
    print("lab A profile present: lab A has been deployed by this kit")
except FileNotFoundError:
    print("lab A not deployed (no profile)")
print(f"objects owned by the kit in A (ledger): {len(Ledger(cfg.ledger_path).owned('A'))}")
"""),
    md("## 3. Pass/fail definitions of the Phase A scenarios"),
    code(r"""
scen = [s for s in load_toml("criteria.toml")["scenario"] if any(r.startswith("A@") for r in s["runs"])]
display(Markdown("| Scenario | Implemented for A | PASS if | FAIL if |\n|---|---|---|---|\n" + "\n".join(
    f"| {s['id']} | {'yes' if any(r.startswith('A@') for r in s.get('implemented', [])) else 'no'} | {s['pass_if']} | {s['fail_if']} |"
    for s in scen)))
"""),
    md("""
## 4. Results: documentary status and execution verdicts, kept apart

Each environment's verdict uses only its own execution results. Network criteria (C02.*) are judged on B only.
Criteria with partial coverage cannot exceed PARTIAL.
"""),
    code(r"""
table, _ = report.build_matrix(cfg)
out = ["| Criterion | Documentary | Verdict A | Verdict B (target) | Coverage limit |", "|---|---|---|---|---|"]
for c in table:
    doc = ", ".join(sorted({("contradiction" if r.details.get("contradiction_present") else r.status) for r in c["docs"]})) or "-"
    out.append(f"| {c['id']} | {doc} | {c['verdict_A']} | {c['verdict_B']} | {c['coverage_limit'] or '-'} |")
display(Markdown(redact_text("\n".join(out))))
"""),
    md("""
## 5. Recorded Azure evidence (environment A, westeurope)

Read from `results/` (redacted JSONL written by the CLI at execution time). Nothing below is re-executed:
each row shows the run id and UTC time of the recorded execution.
"""),
    code(r"""
latest = report.latest_results(cfg)
show = ["S01-GA-CREATE", "S09-OBS-STATUS", "S02-INVOKE", "S10-OBS-LOGS", "S11-OBS-TRACES", "S08-LANGGRAPH",
        "S03-A2A-CARD", "S05-A2A-NO-TOKEN-401", "S04-A2A-MESSAGE", "S06-A2A-NO-ROLE-403",
        "S14-MCP-NO-TOKEN-401", "S15-MCP-WRONG-AUDIENCE-401", "S07-TOOL-AGENT-IDENTITY"]
rows = ["| Scenario | Status | Recorded (UTC) | Run | Observation |", "|---|---|---|---|---|"]
for sid in show:
    for (cid, _, env, _pt), r in latest.items():
        if cid == sid and env == "A":
            rows.append(f"| {sid} | {r.status} | {r.timestamp[:19]} | {r.run_id} | {r.observed[:150]} |")
display(Markdown(redact_text("\n".join(rows))))
extras = {sid: r.details for (sid, _, env, _), r in latest.items() if env == "A"}
s02 = extras.get("S02-INVOKE", {}).get("calls", [])
lg = extras.get("S08-LANGGRAPH", {})
display(Markdown(redact_text("\n".join(
    ["**Recorded answers (synthetic prompts):**"]
    + [f"- S02 call {i + 1}: `{c.get('answer')}` (usage {c.get('usage')})" for i, c in enumerate(s02)]
    + [f"- S08 LangGraph: `{lg.get('answer')}`",
       f"- Versions installed by the platform for the LangGraph agent (container log): `{lg.get('platform_installed_versions')}`",
       f"- S07 token seen by the MCP tool: idtyp `{extras.get('S07-TOOL-AGENT-IDENTITY', {}).get('idtyp')}`, "
       f"claims `{extras.get('S07-TOOL-AGENT-IDENTITY', {}).get('claim_names')}` (values not shown)"]))))
"""),
    md("""
## 6. Live check (read-only, opt-in)

Disabled by default so that "Run All" only reads recorded evidence. Set the environment variable
`HAPQ_NOTEBOOK_LIVE=1` before starting the kernel to read, live: the status of the lab agents and the A2A agent card.
No agent is created, no model is called, nothing is modified.
"""),
    code(r"""
import os

RUN_LIVE = os.environ.get("HAPQ_NOTEBOOK_LIVE") == "1"
if not RUN_LIVE:
    print("live check skipped (HAPQ_NOTEBOOK_LIVE != 1): the tables above are recorded evidence only")
else:
    import httpx
    from azure.ai.projects import AIProjectClient
    from azure.identity import AzureCliCredential
    from datetime import datetime, timezone

    profile = LabProfile.load("A")
    cred = AzureCliCredential(process_timeout=60)
    client = AIProjectClient(endpoint=profile.project_endpoints[0], credential=cred, read_timeout=60, retry_total=1)
    live = ["| Agent | Latest version | Status (live) |", "|---|---|---|"]
    for name in ("hapq-min", "hapq-lg", "hapq-a2a", "hapq-mcp"):
        d = client.agents.get(agent_name=name).as_dict()
        v = ((d.get("versions") or {}).get("latest") or {})
        live.append(f"| {name} | {v.get('version')} | {v.get('status')} |")
    token = cred.get_token("https://ai.azure.com/.default").token
    card = httpx.get(f"{profile.project_endpoints[0]}/agents/hapq-a2a/endpoint/protocols/a2a/agentCard/v1.0",
                     headers={"Authorization": f"Bearer {token}"}, timeout=60)
    print("live read at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    tool = httpx.get(f"{profile.mcp_url}/healthz", timeout=60).status_code if profile.mcp_url else "not deployed"
    display(Markdown(redact_text("\n".join(live) + f"\n\nA2A agent card (live): HTTP {card.status_code}"
                                 + f"\n\nMCP tool /healthz (live): HTTP {tool}")))
"""),
    md("""
## 7. Limits

- Phase A is public: it cannot qualify a network criterion, and an A success never qualifies B.
- S01 shows creation works on the GA API without preview enrollment; the GA status itself stays documentary.
- S08: LangGraph works with the agent identity through the Responses API; the chat-completions path of the Learn sample
  was refused (401) in this lab. Observation, not a documented rule.
- S03-S05: incoming A2A can be enabled and the card is served for a hosted agent, but SendMessage is refused by the
  platform (HOSTED_AGENT_NOT_SUPPORTED) as of 2026-10-02.
- S07 proves the agent's own (instance) identity reaches a tool through the toolbox (token validated by the tool, oid =
  instance identity, idtyp=app). It does not cover end-user propagation (OBO / OAuth passthrough).
- S07 uses agent-framework-foundry-hosting 1.0.0b260918 with the package's own single-instance pattern; the Learn
  per-request factory pattern was not accepted by any of the 5 published versions examined (documented divergence).
- S06: an identity without Foundry role gets 404 (resource hidden) where the same request with a role gets 200.
- S12/S13 cover cross-project consumption and inventory inside one subscription; no enterprise registry.
- Kit limits bound the kit's own activity; they are not a billing cap. Budget alerts notify, they do not stop spend.
"""),
]


NB02 = [
    md("""
# 02 — Phase B: private environment (network-injected account, private endpoints, in-VNet runner)

**Requirement.** Qualify the private-network criteria of the checklist on a private environment (West Europe):
hosted agents deployed and invoked over private endpoints, private DNS, refusal from the public internet with a valid
token, private outbound, private telemetry ingestion and query, private registry image, private MCP tool, portal access.

**What this notebook does.** Read-only. It shows the recorded results of environment B per execution point
(`in-vnet-runner`: a VM inside the VNet driven by run-command; `workstation-public`: the operator workstation on the
public internet) and the diagnostic evidence of the open blockers. It never deploys and never calls paid APIs.
An opt-in live section reads DNS and the public refusal from this workstation only.
"""),
    md("## 1. Environment B (from the local profile, identifiers redacted)"),
    code(r"""
import json
from IPython.display import Markdown, display

from hapq import report
from hapq.config import REPO_ROOT, load_config, load_toml
from hapq.lab.profile import LabProfile
from hapq.ledger import Ledger
from hapq.redact import redact_text

cfg = load_config()
try:
    pb = LabProfile.load("B")
    print(f"region {pb.region}; projects {pb.project_names} (Basic setup) + proj3 (Standard setup, private MCP); "
          "account public network access: Disabled (by template)")
except FileNotFoundError:
    pb = None
    print("environment B not deployed (no profile)")
print(f"objects owned by the kit in B (ledger): {len(Ledger(cfg.ledger_path).owned('B'))}")
"""),
    md("## 2. Scenarios of environment B and where they run"),
    code(r"""
scen = [s for s in load_toml("criteria.toml")["scenario"] if any(r.startswith("B@") for r in s["runs"])]
display(Markdown("| Scenario | Execution points | Implemented | PASS if |\n|---|---|---|---|\n" + "\n".join(
    f"| {s['id']} | {', '.join(r for r in s['runs'] if r.startswith('B@'))} | "
    f"{', '.join(r for r in s.get('implemented', []) if r.startswith('B@')) or 'no'} | {s['pass_if'][:160]} |" for s in scen)))
"""),
    md("""
## 3. Recorded results of environment B, per execution point

Each row is the latest recorded execution (redacted JSONL under `results/`). An A result never qualifies B.
"""),
    code(r"""
latest = report.latest_results(cfg)
rows = ["| Scenario | Point | Status | Recorded (UTC) | Observation |", "|---|---|---|---|---|"]
for (cid, _, env, point), r in sorted(latest.items(), key=lambda kv: (kv[0][0], kv[0][3])):
    if env == "B":
        rows.append(f"| {cid} | {point} | {r.status} | {r.timestamp[:19]} | {r.observed[:170]} |")
display(Markdown(redact_text("\n".join(rows))))
"""),
    md("""
## 4. Diagnostic evidence of the open blockers (S07 private MCP, S26 private registry)

Files under `results/diag/` (git-ignored, redacted). Summaries only; see `docs/documentary-evidence.md`.
"""),
    code(r"""
diag = sorted((REPO_ROOT / "results" / "diag").glob("*/*"))
out = []
for f in diag:
    if f.name == "s07-retry.json":
        d = json.loads(f.read_text(encoding="utf-8"))
        out.append(f"- **S07 retries** ({f.parent.name}): {sum(x.get('success', False) for x in d)}/{len(d)} succeeded")
    elif f.name.startswith("sample19"):
        t = f.read_text(encoding="utf-8")
        setup = "Standard proj3" if f.name.endswith("-std.txt") else "Basic proj1"
        reached = "customer-managed downstream service responded with 401" in t
        out.append(f"- **Sample 19 control, {setup}** ({f.parent.name}): script {'PASSED' if 'agent_private: ✓ PASSED' in t else 'FAILED'}; "
                   f"DataProxy DNS failure: {'could not be resolved' in t}; DataProxy reached the private tool "
                   f"(tool answered 401 to the unauthenticated sample call): {reached}")
    elif f.name.startswith("netdiag") or f.name == "tls-vm.json":
        out.append(f"- {f.name} ({f.parent.name}): {f.stat().st_size} bytes")
display(Markdown(redact_text("\n".join(out) or "no diagnostic files")))
"""),
    md("""
## 5. Live check from this workstation (read-only, opt-in)

Set `HAPQ_NOTEBOOK_LIVE=1` before starting the kernel. Resolves the account host (expected: public addresses from
the internet) and calls the project with a VALID token (expected: 403 network refusal, not 401). No agent is created.
"""),
    code(r"""
import os
import socket

if os.environ.get("HAPQ_NOTEBOOK_LIVE") != "1" or pb is None:
    print("live check skipped (HAPQ_NOTEBOOK_LIVE != 1 or no environment B)")
else:
    import httpx
    from azure.identity import AzureCliCredential
    host = pb.project_endpoints[0].split("/")[2]
    ips = sorted({a[4][0] for a in socket.getaddrinfo(host, 443)})
    tok = AzureCliCredential(process_timeout=60).get_token("https://ai.azure.com/.default").token
    r = httpx.get(f"{pb.project_endpoints[0]}/agents?api-version=v1", headers={"Authorization": f"Bearer {tok}"}, timeout=60)
    private = all(ip.startswith("10.") for ip in ips)
    display(Markdown(redact_text(f"- DNS from this workstation: {'private' if private else 'public'} addresses ({len(ips)})\n"
                                 f"- Call with a valid token from the public internet: HTTP {r.status_code}")))
"""),
    md("""
## 6. Limits

- B results come from a VM inside the VNet (positive paths) and from the public workstation (refusals). A portal check
  needs a browser inside the VNet (Bastion + VM) and is manual.
- Private MCP (S07): the kit's private tool is reachable from the VNet and from the agent sandbox, but the Foundry tool
  paths (Toolbox and Tool Server) report a DNS resolution failure on the Basic network-injected setup. Learn states
  private MCP requires the Standard setup; the Standard project (proj3) is the qualification path for it.
- Private registry (S26): image versions fail with `ProvisioningError`; TLS from the agent runtime to the registry
  endpoints times out while other private endpoints of the same subnet answer. Cause not established: support dossier.
- Workarounds (public endpoints, trusted-services bypass) are never counted as compliance.
"""),
]


def build():
    meta = {"kernelspec": {"name": "python3", "display_name": "Python 3"}}
    for name, cells in (("00_preflight_and_region", NB00), ("01_phase_a_hosted_agents", NB01),
                        ("02_phase_b_private", NB02)):
        out = ROOT / "notebooks" / f"{name}.ipynb"
        nbf.write(nbf.v4.new_notebook(cells=cells, metadata=meta), out)
        print("written", out)


if __name__ == "__main__":
    build()
