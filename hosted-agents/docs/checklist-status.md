# Entry criteria — qualification status

Status on **2026-10-03, 12:10 UTC**. All tests ran in dedicated Azure test environments created by this kit (resource
group `rg-hackathon-prerequisites-eaf`, region `westeurope`), separate from any customer environment. Synthetic data only.

## Read this first: public vs. private test environment

| | Public test environment (A) | Private test environment (B) |
|---|---|---|
| What it is | Foundry account reachable from the internet (Entra authentication required) | Foundry account with public network access **disabled**, agent network injection (VNet `10.42.0.0/16`), private endpoints and private DNS; tests run from a VM inside the VNet |
| Matches the customer's configuration | **No** | **Yes**: this is the configuration that must be qualified |
| Projects | proj1 (reference agents), proj2 (cross-project) | proj1, proj2 (Basic setup); **proj3 (Standard setup: BYO Cosmos DB, AI Search, Storage)** |

**How to read the table**
- A public result never qualifies an item for the customer; network items (2.x) are judged on B only.
- A success in West Europe does not validate North Europe.
- A workaround (public endpoint, trusted-services bypass) is never counted as compliance.
- ✅ demonstrated on Azure · 🟡 partial or documented only · ❌ refused / not working · ⛔ blocked (missing access, tenant or third party) · ⏳ waiting for a manual step · — not applicable.
- "Initial" is the original checklist mark: a starting claim, not evidence.

## Summary

| # | Checklist item | Initial | Public (A) | **Private (B, target)** | In one line |
|---|---|---|---|---|---|
| 1 | Hosted Agent in GA, no need to ask for preview | ☑ ✅ | ✅ | ✅ | Created and invoked on the GA API without preview enrollment, in both environments. GA itself stays documentary (no published GA date). |
| 2 | Private network integration | ☐ | — | 🟡 | All sub-items demonstrated except the private registry. |
| 2.1 | "New Foundry" with public access disabled | ☑ | — | ✅ | From the internet a **valid** token gets a network 403; from the VNet, deploy/invoke/logs work. |
| 2.1.1 | Chat with models from the portal | ☐ ❌ | — | ✅ | New Foundry Playground answers through the private network; from the internet the portal shows "Network access unavailable" and the chat returns "Public access is disabled". |
| 2.2 | Inbound from private networks (incl. telemetry) | ☐ | — | ✅ | Private DNS (10.42.x), calls and sessions from the VNet, A2A card from the VNet, telemetry query private (refused from the internet). |
| 2.3 | Outbound to private resources (incl. telemetry) | ☐ | — | ✅ | Agent reads a private blob with its own identity; telemetry ingested through AMPLS; private MCP tool reached (Standard setup). |
| 2.4 | Class A subnets (?) | ☐ | — | ✅ | Agent subnet in `10.42.0.0/16`: versions active. |
| 2.5 | Private Docker registries | ☐ | — | ❌ / ⛔ | Private ACR: image versions fail (`ProvisioningError`) on Basic and Standard projects; third-party registry: blocked (none available). |
| 3 | Native A2A exposure for hosted agents | ☐ | ❌ | ❌ (card ✅) | Card served (A and B), but `SendMessage` refused by the platform: `HOSTED_AGENT_NOT_SUPPORTED`. A proxy would not be native exposure. |
| 4.1 | Integration with external identity providers | ☐ | ⛔ | ⛔ | Inbound access documented as Entra only; a third-party IdP tenant is needed for the tool-side test. |
| 4.2 | Propagate agent identity to tool calls | ☐ | ✅ | ✅ (Standard) | The tool validated a token whose `oid` is the agent's own instance identity. Private: only on a **Standard-setup** project (Basic: DataProxy DNS failure). End-user (OBO) propagation not covered. |
| 5.1 | Region: West Europe | ☑ ✅ | ✅ | ✅ | Hosted agents deployed and active in both environments. |
| 5.2 | Region: North Europe | ☑ ❌ | — | — | Not listed by Microsoft for hosted agents; not deployed by design. |
| 5.3 | Region: East US 2 | ☑ ✅ | 🟡 | — | Listed; not tested with hosted agents. |
| 5.4 | Region: West US | ☑ ✅ | 🟡 | — | Listed; not tested. |
| 6 | Enterprise-wide registry of tools / agents (cross projects) | ☐ | 🟡 | — | Inventory can be built across projects (✅); a toolbox is project-scoped (403 to another project's agent; with a role, listing passes, tool call still 403). No enterprise registry demonstrated. |
| 7 | Observability of hosted agent deployment | ☐ | ✅ | ✅ | Status transitions, container logs and correlated traces, in both environments (private ingestion and query). |
| 8 | LangGraph integration in hosted agent | ☐ | ✅ | ✅ | LangGraph agent active and answering, public and private. |

## Detail per item

### 1. GA
- Created with `azure-ai-projects` 2.7.0, no preview header or feature registration (S01, A and B); invoked (S02, A and B).
- Documentary contradiction kept: a private-link page still says "hosted (preview) agents".

### 2. Private network integration (environment B)

| Sub-item | Evidence | Status |
|---|---|---|
| 2.1 Public access disabled | S20: from the internet, a valid Entra token with Foundry User → HTTP 403 network rule. S01/S02/S10 from the VNet. | ✅ |
| 2.1.1 Portal chat | Manual, 2026-10-03: New Foundry (proj1 of the private account, gpt-5-mini Playground) answered "Hello" from the operator's managed browser routed into the VNet (Bastion Standard tunnel + SOCKS proxy on the in-VNet VM, equivalent to a VPN client; account FQDN resolved to 10.42.1.22; same-token API control 200 vs 403 direct). Control from the internet: "Network access unavailable", then "Authentication error Public access is disabled. Please configure private endpoint." Sign-in from an unmanaged Windows VM in the VNet failed (AADSTS50012, corporate sign-in policy): an identity constraint, not a network result. | ✅ |
| 2.2 Inbound | S21 DNS 5/5 private from the VNet (public from the internet); S02 two calls same session; S03 A2A card 200 from the VNet; S24 telemetry query OK from the VNet, refused from the internet (control query on A succeeds with the same identity). | ✅ |
| 2.3 Outbound | S22 private blob read by the agent identity (internet: 403 AuthorizationFailure); S23 72 telemetry rows ingested privately; S07-STD private MCP tool reached through the DataProxy. | ✅ |
| 2.4 Class A | S25: agent subnet `10.42.0.0/24`, versions active. | ✅ |
| 2.5 Private ACR | S26 (Basic project): `ProvisioningError` with AcrPull for project and agent identities, ACR policy and RBAC mode compliant; S26-BYPASS with `AzureServices` (operator-approved, reverted): same error; S26-STD (Standard project, created 2026-10-02 > 2026-06-25): same error. Diagnostic: from the agent runtime, TLS to the ACR registry endpoint takes 11.3 s (other private endpoints < 0.1 s); from the VM all endpoints answer at once. | ❌ support dossier |
| 2.5 Third-party registry | No third-party registry available. | ⛔ |

### 3. A2A
Card v1.0 served (A and B, 401 without token); `SendMessage` with the official `a2a-sdk` refused: `-32099 HOSTED_AGENT_NOT_SUPPORTED`
(A). Not retried in B without a new technical hypothesis. Reproduction dossier prepared, not submitted.

### 4. Security
- 4.1 External IdP: inbound access is Entra only per documentation; tool-side custom OAuth needs a third-party IdP tenant (⛔).
- 4.2 Agent identity: A ✅ (S07); B ✅ on proj3 Standard setup (S07-STD: oid = agent instance identity). On the Basic setup both Foundry tool
  paths fail with "Name or service not known … could not be resolved from the selected network path" (0/5 retries + sample 19 control 0/3);
  on Standard the platform states "the customer-managed downstream service responded with 401" for the unauthenticated sample call, i.e. the
  DataProxy reaches the private tool. Learn: "Private MCP requires Standard Agent Setup". The tool rejects missing (S14) and foreign-audience
  (S15) tokens in A and B.

### 5. Regions
West Europe demonstrated (A and B). North Europe not listed for hosted agents. East US 2 / West US listed, not tested.
Side observation: Cosmos DB account creation was refused in West Europe and North Europe on 2026-10-02 ("high demand … request region
access"); the Standard project uses Cosmos DB in East US 2 (Learn allows another region for BYO resources). A region-access request is an
operator decision.

### 6. Cross-project (environment A, one subscription)
- S13 ✅: agents of proj1 (4) and proj2 (1) enumerated with project attribution by iterating projects. An inventory, not a registry.
- S12 ❌ by default: proj1's toolbox answers the proj2 agent with HTTP 403.
- S12-RBAC: with Foundry User for the proj2 agent identity on proj1 only, toolbox statuses `200 → 204 → 200 → 403`; no tool call.
- Control Plane view (MAN-C06): manual, not performed. Tenant-wide registries (Agent 365, API Center) not covered.

### 7. Observability
Status (S09), container logs (S10), correlated traces (S11 in A, S23 in B, private) ✅.

### 8. LangGraph
`langchain-azure-ai[hosting]` 1.2.10 with the agent instance identity and the Responses API (S08 ✅ A and B).

## Blockers and minimal interventions

| Item | Cause | Minimal intervention |
|---|---|---|
| 2.5 Private ACR | `ProvisioningError` on every project type; runtime TLS to the registry endpoint ≈ 11 s | Support case (dossier prepared, not submitted: `results/support/`) |
| 2.5 Third-party registry | No registry available | Provide a test registry (e.g. JFrog trial) |
| 4.1 External IdP | Third-party tenant needed | Provide an IdP test tenant |
| 3 A2A | Platform refusal | Product decision; dossier prepared, not submitted |
| Cosmos DB in EU regions | Subscription region access | Optional region-access request (only to keep BYO data in the EU) |

## Resources and cost

Billable resources (both environments): Foundry accounts (pay per use), gpt-5-mini deployments (tokens), Log Analytics + App Insights,
ACR Basic (A) and Premium (B), Container Apps (MCP tool, `min-replicas 1` in B), NAT gateway + public IP, private endpoints (account,
ACR, blob x2, monitor, Cosmos DB, AI Search), private DNS zones, storage accounts, AI Search Basic, Cosmos DB (East US 2), in-VNet runner VM
D2as_v6 (deallocated when idle). Temporary portal-check resources (Bastion, browser VM, tunnel key) were removed on 2026-10-03.
Model usage stays in the thousands of tokens; some calls return no usage data (reported as unknown). The hosted-agent runtime price is not
published. Budget alerts notify only; they are not a spending cap.

Evidence: `docs/criteria-matrix.md`, `docs/documentary-evidence.md`, notebooks `00`, `01`, `02`, results under `results/` (git-ignored, redacted).
