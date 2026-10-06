# Provisioning plan

Status on 2026-10-01: **Phase A implemented, not approved, not deployed. Phase B planned, not implemented.**
Prices: Azure Retail Prices API, `westeurope`, USD list prices on 2026-10-01.

## Phase A — what the code will do

`python -m hapq lab deploy --phase A` and `python -m hapq lab run --phase A` print their plan and stop.
They act only with `--execute --confirm rg-hackathon-prerequisites-eaf`.

| Step | Source file | Creates (ledgered, tag `kit=hapq`, `westeurope`) |
|---|---|---|
| gate | `src/hapq/checks/preflight.py` | nothing. These checks must PASS: identity, subscription, resource group, providers, policies, roles, quota, Entra app creation |
| entra | `src/hapq/lab/entra.py` | 1 app registration `hapq-mcp-<suffix>` + its service principal, tag `hapq-kit`. Exposes one scope and pre-authorizes the Azure CLI client |
| infra | `infra/phase-a-public/main.bicep` (pass 1) | Foundry account S0 (public), `proj1`, `proj2`, `gpt-5-mini` DataZoneStandard 10K TPM, Log Analytics (1 GB/day cap), App Insights, AppInsights project connections, ACR Basic, user-assigned identity, **AcrPull** for it on the ACR, Container Apps environment (Consumption profile, no private endpoint), budget (if an e-mail is given) |
| image | `tools/mcp_whoami/` | 1 image built by ACR Tasks (no local Docker) |
| mcp | `infra/phase-a-public/main.bicep` (pass 2) | Container App: min 0 / max 1 replica, 0.25 vCPU / 0.5 GiB, public HTTPS ingress, Entra token validation |
| run | `src/hapq/lab/scenarios.py` | toolbox `hapq-tools` and connection `hapq-mcp` (AgenticIdentityToken) in `proj1`; hosted agents `hapq-min`, `hapq-lg` (proj1) and `hapq-xp` (proj2), from source |

**Roles.** The only role assignment created is AcrPull: the MCP app identity must pull its image.

The following roles are not assigned:
- **Monitoring Reader:** you are Owner on the subscription, which includes trace queries. If S11 is refused anyway, it reports BLOCKED.
- **Foundry Agent Consumer:** you already hold Foundry User.
- **Roles for the project identity:** Learn documents them for image pulls, not for source deployments. If S01 fails on authorization, the result says so and the role is requested then.

The S06 identity has no Foundry role, on purpose: it must be refused.

**Execution limits** (`config/kit.toml [limits]`, enforced by `src/hapq/limits.py`):
- at most 6 versions, 12 sessions and 40 calls per run;
- at most 150,000 tokens per run and 300 output tokens per call;
- concurrency 1;
- 0.5 vCPU / 1 GiB per session, with a 120 s idle timeout;
- call timeout 120 s, deploy timeout 900 s.

These limits bound the kit's own activity. They are **not** a billing cap. The budget sends alerts after the fact
and does not stop spending. The 48-hour window is a target, not an automatic deletion.

### Phase A costs (48-hour target window)

| Item | Unit price | Assumption | Estimate (USD) |
|---|---|---|---|
| gpt-5-mini DataZone, input / output | 0.495 / 3.96 per 1M tokens | ≤ 150K tokens (kit limit) | ≤ 0.60 |
| Defender for AI (enabled on the subscription) | 0.0008 / 1K tokens | ≤ 150K tokens | ≤ 0.12 |
| **Hosted-agent runtime** | **no published meter** | ≤ 12 sessions, 0.5 vCPU / 1 GiB, 120 s idle | **not priceable** |
| ACR Basic | 0.1666 / day | 2 days | 0.33 |
| Defender for Containers image assessment | 0.29 / image | 1–2 images, if it applies to ACR | 0–0.58 |
| Container Apps app | 0.000034 / vCPU-s active; monthly free grant | a few minutes active | ≈ 0 |
| Container Apps environment | Learn: no environment or plan-management fee for a Consumption profile without private endpoint | — | 0 |
| Log Analytics / App Insights | 5 GB/month free (billing account), then 2.99 / GB; 1 GB/day cap | < 0.5 GB | 0–1.50 |
| Entra app, identity, budget, Foundry account | free / no fixed fee | — | 0 |
| **Total priced** | | | **≈ 0.5–3.2 + hosted-agent runtime** |

**Not priceable in advance:**
- the hosted-agent runtime;
- whether the free Log Analytics allowance is still available;
- whether the Defender per-image charge applies.

These are measured afterwards in Cost Management.

### Phase A cleanup

Run `python -m hapq cleanup --env A`. It is a dry-run by default.

- **Execute:** add `--execute --confirm rg-hackathon-prerequisites-eaf`.
- **Purge:** add `--purge hapq-a-<suffix>` to irreversibly purge the soft-deleted account.
- **Scope:** only objects in `results/ledger.jsonl` that the kit created. Pre-existing objects are never touched, and
  objects that lost their tag or Entra marker are not deleted but reported.
- **Order:**
  1. data plane objects (agents, toolboxes, connections), unless they cascade with their account;
  2. role assignments;
  3. budget;
  4. Container App, then the Container Apps environment;
  5. Foundry account (then purge, if requested);
  6. ACR, App Insights, Log Analytics, identity;
  7. service principal, then app registration.
- **Resource group:** kept unless `--include-resource-group` is given, and deleted only if empty.
- **Status:**
  - PASS only when every object is verified absent.
  - FAIL if a deletion failed, an object remains, or an account is soft-deleted and not purged.
  - BLOCKED if the state cannot be read.
  - A read error never counts as "nothing".

## Phase B — planned, not implemented

**Already written:**
- `infra/phase-b-private/main.bicep` (draft; compiles; never deployed);
- the B scenario definitions S20–S27 in `config/criteria.toml`;
- the classification rules in `src/hapq/lab/classify.py`.

**Not implemented yet:**
- `lab deploy/run --phase B`;
- runner bootstrap through `az vm run-command`;
- the B scenarios;
- replay of S01–S15 from the runner;
- the bundled packaging of agents.

The matrix shows these as `NOT_TESTED (not implemented)`.

**Telemetry in B.** Ingestion and consultation are two separate controls, both private by default in the draft:

| Control | Setting | Qualified by |
|---|---|---|
| Ingestion | `publicNetworkAccessForIngestion=Disabled` on App Insights and Log Analytics, data through the AMPLS private endpoint. Learn: Log Analytics ingestion ignores AMPLS access modes, so the resource flag is what enforces it | S23: agent telemetry arrives |
| Consultation | `publicNetworkAccessForQuery=Disabled` + AMPLS `queryAccessMode=PrivateOnly` (parameter `telemetryQueryAccess`) | S24: query succeeds from the runner **and** is refused from the workstation |

With `telemetryQueryAccess=Open`, consultation is public by configuration and S24 reports NOT_TESTED, never PASS.
Queries through the ARM API cannot use private links.

**Container Apps in B.** The MCP tool runs in an internal environment injected in the VNet, without a private
endpoint. Learn states that a private endpoint on an environment triggers a Dedicated Plan Management charge even on
Consumption, so this design avoids it. For your own VNet, Learn says only that "additional charges might apply".

**Indicative cost**, to be re-estimated with Phase A measurements before asking for approval:
- private endpoints: 4 × 0.01/h;
- NAT Gateway: 0.045/h + 0.045/GB, plus its public IP at 0.005/h;
- VM B2s: 0.048/h, plus Defender for Servers P2 at 0.02/h;
- ACR Premium: 1.6666/day;
- private DNS zones: 0.50/zone/month;
- storage with Defender for Storage: 0.0134/h;
- AI Search Basic: 0.101/h, only if BYO data resources prove mandatory.

That is about 20–45 USD for 72 h, excluding the hosted-agent runtime.

## Authorizations needed to launch Phase A

1. **Entra (tenant):** create 1 app registration and 1 service principal, both tagged `hapq-kit` and deleted at cleanup.
2. **Azure:** deploy `infra/phase-a-public/main.bicep` (passes 1 and 2) into `rg-hackathon-prerequisites-eaf`, `westeurope`.
3. **Role assignment:** AcrPull for the MCP app's user-assigned identity on the lab ACR (created by the Bicep).
4. **Paid calls:** model inference and hosted-agent sessions within the kit limits; one ACR Tasks build.
5. **Budget:** a resource-group budget with alerts. Give the recipient e-mail, or decline the budget.
6. **Agent identity:** creation of hosted-agent identities by the platform (automatic when an agent is created).
