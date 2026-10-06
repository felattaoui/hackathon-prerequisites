# Foundry Hosted Agents — prerequisite qualification kit

Re-executable tests, documentary checks and short notebooks that qualify a workshop's entry criteria for
**Microsoft Foundry hosted agents**: GA status, private networking, A2A, external identity, regional availability,
cross-project registry, observability and LangGraph.

Two dedicated test environments are deployed by the kit in West Europe:

| Environment | What it is | Used for |
|---|---|---|
| **A — public** | Foundry account reachable from the internet (Microsoft Entra ID sign-in required) | Make each feature work and debug the scenarios |
| **B — private** | Public network access **disabled**, agent network injection into a VNet (`10.42.0.0/16`), private endpoints and private DNS, AMPLS for telemetry | Qualify the network criteria. A result in A never qualifies B |

Results are `PASS`, `FAIL`, `BLOCKED` or `NOT_TESTED`. Each result is dated and records its region, environment,
execution point, versions and context. Three kinds of evidence are kept apart:

| Evidence | Meaning | Proves capability on Azure? |
|---|---|---|
| `documented` | Microsoft Learn states it on the fetch date | No |
| `observed` | A live call made by this kit | Only for what the call exercises |
| `manual` | An operator performed and recorded a portal step | Within the recorded scope |

Unit tests use fakes for development only. Their results can never be written as evidence.

## Results (October 2026)

Summary per checklist item: [docs/checklist-status.md](docs/checklist-status.md). Per-scenario evidence and verdicts per
environment: [docs/criteria-matrix.md](docs/criteria-matrix.md) (generated). Contradictions between Microsoft sources and
diagnostics: [docs/documentary-evidence.md](docs/documentary-evidence.md).

| Area | Private environment (B) |
|---|---|
| GA without preview enrollment, inbound/outbound private access incl. telemetry, class A subnets, observability, LangGraph, portal chat | Demonstrated |
| Agent identity to a private MCP tool | Demonstrated on a **Standard** agent setup project (BYO Cosmos DB, AI Search, Storage); fails on the Basic setup, as documented |
| Hosted agent image from a private ACR | Not qualified (`ProvisioningError` on Basic, Standard and with the trusted-services exception); cause not established |
| Native A2A (`SendMessage`) | Refused by the platform (agent card served) |
| External IdP, third-party registry | Not tested (no third-party resources) |

Region rationale: [docs/region-decision.md](docs/region-decision.md). Plan and authorizations:
[docs/provisioning-plan.md](docs/provisioning-plan.md).

## Layout

```
config/kit.toml              lab settings and execution limits (no secrets); subscription id comes from .env
config/criteria.toml         criteria -> documentary checks -> scenarios per environment (single source of truth)
config/doc_assertions.toml   documentary assertions on Learn pages
src/hapq/                    config, redaction, results, ledger, tracking, cleanup, limits, checks, report, CLI
src/hapq/lab/                deployment and scenarios: public (A), private (B), MCP, A2A, LangGraph, cross-project
agents/                      hosted agents deployed from source (minimal, toolbox, LangGraph, outbound, container)
agents/netdiag/              Microsoft network diagnostic agent (foundry-samples, MIT), ZIP mode
tools/mcp_whoami/            test MCP tool (validates Entra tokens, records the caller)
tools/vnet_runner/           runner executed on the in-VNet VM (driven by az vm run-command)
tools/sample19/              foundry-samples MCP test script (MIT), used as a control
infra/phase-a-public/        Bicep, environment A
infra/phase-b-private/       Bicep, environment B (+ Standard-setup project, temporary portal access)
scripts/                     notebooks build/run, extra tracked deployments, resource group helpers
tests/unit/                  offline tests (fakes, never evidence)
tests/azure/                 live read-only tests (-m azure, -m docs)
notebooks/                   00 preflight & region, 01 public environment, 02 private environment (output-free)
results/                     run outputs, ledger.jsonl, diagnostics (git-ignored)
```

## Prerequisites

- Azure CLI ≥ 2.80 with Bicep. Python 3.11+ (developed with a local conda env `.conda/`, Python 3.13).
- `.env` containing `AZURE_SUBSCRIPTION_ID=<guid>`: copy [.env.example](.env.example). `.env` is never committed.
- `az login` with an identity on that subscription. The kit passes `--subscription` explicitly and never changes the default.
- Live labs: `pip install -e ".[lab]"`. Neither azd nor Docker is needed locally: agents are deployed from source and built
  by Foundry; container images are built by ACR Tasks or on the in-VNet runner.

### Permissions

| Step | Role | Scope |
|---|---|---|
| Preflight, docs, report | Reader (+ Graph read of own user and tenant app policy) | subscription |
| Lab deployment | Owner (deploys resources and role assignments) | resource group |
| Entra app registration (MCP tool audience) | user app registration allowed, or an app-registration directory role | tenant |
| Create hosted-agent versions | Foundry User or higher (Azure Owner alone does not grant it) | project |
| Read traces | Reader on Application Insights (Monitoring Reader if not) | Application Insights |

## Commands

Every command that creates, changes or deletes Azure resources is a **dry-run by default** and acts only with
`--execute --confirm <resource-group>`. Created objects are recorded in `results/ledger.jsonl` before creation.

```bash
# install and offline tests
./.conda/python.exe -m pip install -e ".[dev,notebooks]"
./.conda/python.exe -m pytest -q

# read-only checks (results recorded)
./.conda/python.exe -m pytest -m "azure or docs" -q
./.conda/python.exe -m hapq preflight
./.conda/python.exe -m hapq docs
./.conda/python.exe -m hapq report          # regenerate docs/criteria-matrix.md

# environment A (public)
./.conda/python.exe -m hapq lab deploy --phase A --budget-email <recipient>
./.conda/python.exe -m hapq lab run --phase A
./.conda/python.exe -m hapq lab xproject --phase A          # cross-project tool use and inventory

# environment B (private)
./.conda/python.exe -m hapq lab private-deploy --phase B
./.conda/python.exe -m hapq lab private-run --phase B --steps dns,public,minimal,invoke,logs,langgraph,outbound,telemetry
./.conda/python.exe scripts/deploy_extra.py proj3 [cosmos-region]   # Standard-setup project (private MCP)
./.conda/python.exe -m hapq lab private-run --phase B --steps s07-std,s03-s15

# manual checks (portal): list the steps, then record the observed result
./.conda/python.exe -m hapq manual list
./.conda/python.exe -m hapq manual record --id MAN-C02-PORTAL-CHAT-INVNET --env B --status PASS --observed "..."

# objects created by the kit, and their cleanup (dependency order, final verification; purge only on request)
./.conda/python.exe -m hapq ledger
./.conda/python.exe -m hapq cleanup --env B

# notebooks: rebuild from source, execute top to bottom in a fresh kernel
./.conda/python.exe scripts/build_notebooks.py
./.conda/python.exe scripts/run_notebook.py notebooks/02_phase_b_private.ipynb
```

Other `private-run` steps (diagnostics): `mcp`, `image`, `mcp-diag`, `netdiag`, `image-bypass`, `s07-retry`,
`s07-control`, `s26-tls`, `s26-tls-slow`, `s26-std`.

Notebooks never provision or clean up: a "Run All" performs only read-only calls (live checks are opt-in with
`HAPQ_NOTEBOOK_LIVE=1`). A test enforces it.

## Execution points

| Execution point | Where | Used for |
|---|---|---|
| `workstation-public` | Operator machine, public internet | Preflight, docs, environment A, **expected refusals** against B |
| `in-vnet-runner` | VM without public IP inside the B VNet, driven by `az vm run-command` | Private-path tests (DNS, invoke, outbound, telemetry, tools, registry) |
| `browser-inside-vnet` | A browser whose traffic reaches the B VNet | Manual portal checks (tested with a managed laptop routed through an Azure Bastion tunnel and a SOCKS proxy) |

The execution point is declared, never inferred. A network success does not prove the private path: private tests
also assert that DNS resolves to a private address in the lab VNet, and public-access denial is tested with a **valid**
token (a `401` without token proves only authentication).

## Costs

Environment B has fixed costs while it exists (NAT gateway, private endpoints, ACR Premium, AI Search, Cosmos DB,
Container Apps with one replica). The runner VM is deallocated when idle. Kit limits bound the kit's own activity;
Azure budget alerts notify, they do not cap spending. Clean up with `hapq cleanup` when the labs are no longer needed.

## Data and confidentiality

- Synthetic data only. Outputs are redacted (GUIDs, tokens, keys, UPNs) before display or write; published documents also
  mask live endpoint hostnames.
- Confidential source documents (`inputs/`) and customer-facing deliverables (`deliverables/`) are git-ignored. A test
  checks that inputs are never copied into deliverables.
- Put identifying terms from confidential sources in `config/forbidden_terms.local.txt` (git-ignored). A test fails if
  any appears in a versioned document.
- The kit itself never pushes or publishes anything.

## License

MIT, see [LICENSE](LICENSE). Code copied from
[microsoft-foundry/foundry-samples](https://github.com/microsoft-foundry/foundry-samples) (`agents/netdiag`,
`tools/sample19`) keeps its own MIT notice (Copyright (c) 2025 Microsoft Corporation).
