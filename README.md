# Foundry Hosted Agents — prerequisite qualification kit

Re-executable tests, documentary checks and short notebooks that qualify the workshop entry criteria for
**Microsoft Foundry hosted agents**: GA status, private networking, A2A, external identity, regional
availability (including North Europe), cross-project registry, observability and LangGraph.

Results are `PASS`, `FAIL`, `BLOCKED` or `NOT_TESTED`. Each result is dated and records its region, versions and
execution context. Three kinds of evidence are kept apart:

| Evidence | Meaning | Proves capability on Azure? |
|---|---|---|
| `documented` | Microsoft Learn states it on the fetch date | No |
| `observed` | A live call made by this kit | Only for what the call exercises |
| `manual` | An operator performed and recorded a portal step | Within the recorded scope |

Unit tests use fakes for development only. Their results can never be written as evidence.

## Status

| Item | State |
|---|---|
| Resource group `rg-hackathon-prerequisites-eaf` | Created in `westeurope` by the kit (ledgered), tagged `kit=hapq`, empty |
| Preflight, documentary checks, notebooks 00/01 | Executed, recorded (read-only) |
| Phase A (public lab) | **Implemented, not deployed** (awaiting approval): infra, MCP tool, agents, scenarios S01-S15 |
| Phase B (private lab) | **Planned, not implemented**: draft Bicep only |

Results: [docs/criteria-matrix.md](docs/criteria-matrix.md). Plan, costs and authorizations:
[docs/provisioning-plan.md](docs/provisioning-plan.md). Region: [docs/region-decision.md](docs/region-decision.md).
Contradictions: [docs/documentary-evidence.md](docs/documentary-evidence.md).

## Layout

```
config/kit.toml              lab settings and execution limits (no secrets); subscription id comes from .env
config/criteria.toml         criteria -> documentary checks -> scenarios per environment (single source of truth)
config/doc_assertions.toml   documentary assertions on Learn pages
src/hapq/                    config, redaction, results, ledger, cleanup, limits, checks, report, CLI
src/hapq/lab/                Phase A deployment, Entra app, scenarios, pass/fail rules
tools/mcp_whoami/            test MCP tool (validates Entra tokens, records the caller)
agents/                      hosted agents deployed from source (minimal + toolbox, LangGraph)
infra/phase-a-public/        Phase A Bicep (not deployed)
infra/phase-b-private/       Phase B Bicep draft (not deployed, not wired)
tests/unit/                  offline tests (fakes, never evidence)
tests/azure/                 live read-only tests (-m azure, -m docs)
notebooks/                   presentation notebooks (output-free in the repo)
results/                     run outputs and ledger.jsonl (git-ignored)
```

## Prerequisites

- Azure CLI ≥ 2.80 with Bicep. Python 3.11+ (the repo uses the local conda env `.conda/`, Python 3.13).
- `.env` containing `AZURE_SUBSCRIPTION_ID=<guid>`. Copy it from [.env.example](.env.example); `.env` is never committed.
- `az login` with an identity on that subscription. The kit passes `--subscription` explicitly and never changes the default subscription.
- Phase A: `pip install -e ".[lab]"` (azure-ai-projects >= 2.5). Neither azd nor Docker is used: agents are deployed from source and built by Foundry; the MCP image is built by ACR Tasks.

### Permissions

| Step | Role | Scope |
|---|---|---|
| Preflight, docs, report | Reader (+ Graph read of own user and tenant app policy) | subscription |
| Phase A deployment | Owner (deploys resources and the AcrPull assignment) | resource group |
| Entra app registration | tenant allows user app registration, or an app-registration directory role | tenant |
| Create hosted-agent versions | Foundry User or higher (Azure Owner alone does not grant it) | project |
| Read traces | Owner/Reader on Application Insights (Monitoring Reader if not) | Application Insights |

## Commands

```bash
# install (into the repo conda env)
./.conda/python.exe -m pip install -e ".[dev,notebooks]"

# offline tests (default selection)
./.conda/python.exe -m pytest -q

# live read-only tests: subscription and Learn pages (results recorded)
./.conda/python.exe -m pytest -m "azure or docs" -q

# individual runs
./.conda/python.exe -m hapq preflight        # read-only Azure checks
./.conda/python.exe -m hapq docs             # documentary checks
./.conda/python.exe -m hapq report           # regenerate docs/criteria-matrix.md
./.conda/python.exe -m hapq manual list      # manual verification steps

# Phase A: dry-run by default; acts only with --execute --confirm <rg> after approval
./.conda/python.exe -m hapq lab deploy --phase A --budget-email <recipient>
./.conda/python.exe -m hapq lab run --phase A

# objects created by the kit, and their cleanup (dry-run by default, dependency order, final verification)
./.conda/python.exe -m hapq ledger
./.conda/python.exe -m hapq cleanup --env A
./.conda/python.exe -m hapq cleanup --env A --execute --confirm rg-hackathon-prerequisites-eaf --purge hapq-a-lab01

# notebooks: rebuild from source, execute top to bottom in a fresh kernel
./.conda/python.exe scripts/build_notebooks.py
./.conda/python.exe scripts/run_notebook.py notebooks/01_phase_a_hosted_agents.ipynb
```

Notebooks never provision or clean up. A "Run All" performs only read-only calls and writes local results.
A test (`test_notebooks_have_no_outputs_and_no_provisioning`) enforces this.

## Execution points

| Execution point | Where | Used for |
|---|---|---|
| `workstation-public` | Your machine, public internet | Preflight, docs, Phase A tests, **expected refusals** against the private lab |
| `in-vnet-runner` | VM without public IP inside the Phase B VNet, driven by `az vm run-command` | Private-path tests (DNS, invoke, outbound, telemetry, private ACR) |
| `browser-inside-vnet` | Browser on a VPN/ExpressRoute/Bastion path | Manual portal checks. BLOCKED until available |

The execution point is declared (`config/kit.toml` or `HAPQ_EXECUTION_POINT`), never inferred. A network success
does not prove the private path: private tests also assert that DNS resolves to a private IP in the lab VNet. A
`401` without a token proves only authentication: public-access denial is tested with a **valid** token.

## Data and confidentiality

- Synthetic data only. Outputs are redacted (GUIDs, tokens, keys, UPNs) before display or write.
- `inputs/` (source checklist, confidential) is git-ignored and never copied into deliverables. A test checks for copies.
- Put identifying terms from confidential sources in `config/forbidden_terms.local.txt` (git-ignored). A test fails if any appears in a deliverable.
- Nothing is pushed or published by the kit.
