# Criteria matrix — Foundry Hosted Agents prerequisites

Generated 2026-10-06T13:25:48+00:00 by `hapq report` (kit 0.1.0, config a6d14239c129, lab region `westeurope`). Do not edit by hand.

Qualification target: environment **B** (private lab). Environment A (public lab) is used to build and debug scenarios; an A result never qualifies B. Documentary status never enters a verdict. Checklist ticks are initial claims, not evidence. A success in the lab region does not validate North Europe.

## Summary

| Criterion | Checklist initial claim | Documentary status (Learn) | Preflight (subscription) | Verdict A (public) | Verdict B (target) |
|---|---|---|---|---|---|
| **C00** Lab prerequisites (not a checklist item) | n/a | - | 1 NOT_TESTED, 8 PASS | n/a (no execution scenario) | n/a (no execution scenario) |
| **C01** Hosted agents are GA, no preview enrollment needed | checked | 2 PASS, 1 contradiction | 1 PASS | PARTIAL (see limit) | PARTIAL (see limit) |
| **C02.a** New Foundry experience works with public network access disabled | checked | 1 PASS | - | n/a (qualified in B only) | PARTIAL (see limit) |
| **C02.b** Chat with models from the portal when public access is disabled | not checked, marked as failing | 1 PASS | - | n/a (qualified in B only) | QUALIFIED |
| **C02.c** Inbound: hosted agents reachable from private networks (incl. telemetry) | not checked | 1 contradiction | - | n/a (qualified in B only) | QUALIFIED |
| **C02.d** Outbound: hosted agents reach private resources (incl. telemetry) | not checked | 2 PASS | - | n/a (qualified in B only) | QUALIFIED |
| **C02.e** Class A (10.0.0.0/8) subnets supported | not checked, question mark | 1 PASS, 2 contradiction | - | n/a (qualified in B only) | QUALIFIED |
| **C02.f** Private Docker registries supported | not checked | 2 PASS | - | n/a (qualified in B only) | NOT QUALIFIED (FAIL observed) |
| **C03** Native A2A exposure for hosted agents | not checked | 1 contradiction | - | NOT QUALIFIED (FAIL observed) | NOT DEMONSTRATED |
| **C04.a** Integration with external identity providers | not checked | 1 NOT_TESTED, 2 PASS | - | NOT DEMONSTRATED | NOT DEMONSTRATED |
| **C04.b** Agent identity propagated to tool calls | not checked | 2 PASS | - | PARTIAL (see limit) | PARTIAL (see limit) |
| **C05.a** Hosted agents available in West Europe | checked, marked as passing | 1 PASS | 3 PASS | QUALIFIED | QUALIFIED |
| **C05.b** Hosted agents available in North Europe (main EU test region) | checked, marked as failing (not listed) | 2 FAIL | 1 NOT_TESTED | n/a (no execution scenario) | n/a (no execution scenario) |
| **C05.c** Hosted agents available in East US 2 | checked, marked as passing | 1 PASS | - | n/a (no execution scenario) | n/a (no execution scenario) |
| **C05.d** Hosted agents available in West US | checked, marked as passing | 1 PASS | - | n/a (no execution scenario) | n/a (no execution scenario) |
| **C06** Enterprise-wide registry of tools / agents (cross projects) | not checked | 2 PASS | - | NOT QUALIFIED (FAIL observed) | NOT DEMONSTRATED |
| **C07** Observability of hosted agent deployment | not checked | 2 PASS | - | QUALIFIED | QUALIFIED |
| **C08** LangGraph integration in a hosted agent | not checked | 1 PASS | - | QUALIFIED | QUALIFIED |

## Details

### C00 — Lab prerequisites (not a checklist item)

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| PF-AUTH | subscription@workstation-public | PASS | observed | 2026-10-02T20:55:12 | 20261002T205512Z-8a4270 | current (7 older) | state=Enabled, identity type=user |
| PF-SUB | subscription@workstation-public | PASS | observed | 2026-10-02T20:55:12 | 20261002T205512Z-8a4270 | current (7 older) | match=True; global default context is the target=True (left unchanged) |
| PF-RG | subscription@workstation-public | PASS | observed | 2026-10-02T20:55:12 | 20261002T205512Z-8a4270 | current (7 older) | RG location=westeurope (metadata only), tagged=True, 84 resource(s) inside |
| PF-PROVIDERS | subscription@workstation-public | PASS | observed | 2026-10-02T20:55:12 | 20261002T205512Z-8a4270 | current (7 older) | all registered |
| PF-POLICIES | subscription@workstation-public | PASS | observed | 2026-10-02T20:55:12 | 20261002T205512Z-8a4270 | current (7 older) | 3 assignment(s) apply (subscription + RG); location restrictions: 0; blocking: 0 |
| PF-ROLES | subscription@workstation-public | PASS | observed | 2026-10-02T20:55:12 | 20261002T205512Z-8a4270 | current (7 older) | effective roles on RG scope: Foundry User, Owner |
| PF-TOOLING | subscription@workstation-public | PASS | observed | 2026-10-02T20:55:12 | 20261002T205512Z-8a4270 | current (7 older) | azure-cli 2.86.0, azd 1.34.0, bicep 0.38.3, azure-ai-projects 2.7.0 \| warnings: Docker daemon not reachable (not required: source-code deployment builds remotely) |
| PF-ENTRA-APP-CREATE | subscription@workstation-public | PASS | observed | 2026-10-02T20:55:12 | 20261002T205512Z-8a4270 | current (4 older) | tenant allows user app registration=True; relevant directory roles=none |
| PF-CLEANUP | subscription@- | NOT_TESTED | observed | - | - | - | not run yet |

### C01 — Hosted agents are GA, no preview enrollment needed

> Coverage limit: Creating an agent on the GA API without preview enrollment is consistent with GA; the GA status itself is a documentary/contractual fact, not provable by execution.

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C01-GA-STATEMENT | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'Hosted Agents is generally available' present | [1](https://learn.microsoft.com/en-us/agent-framework/hosting/foundry-hosted-agent) |
| DOC-C01-NO-PREVIEW-HEADER | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'no longer required' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/migrate-hosted-agent-preview) |
| DOC-C01-PREVIEW-WORDING | contradiction | contradiction | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | any of ['hosted (preview) agents', 'hosted agents (preview)'] (found ['hosted (preview) agents']) present | [1](https://learn.microsoft.com/en-us/azure/foundry/how-to/configure-private-link) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| PF-PREVIEW-FEATURES | subscription@workstation-public | PASS | observed | 2026-10-02T20:55:12 | 20261002T205512Z-8a4270 | current (7 older) | 0 agent-related feature flag(s) exist, none registered |
| S01-GA-CREATE | A@workstation-public | PASS | observed | 2026-10-01T23:43:40 | 20261001T234340Z-54a033 | current (2 older) | status sequence active (v1 API, no preview header, no feature registration) |
| S01-GA-CREATE | B@in-vnet-runner | PASS | observed | 2026-10-02T15:02:26 | 20261002T150226Z-b329b5 | current (1 older) | status sequence creating -> active (from inside the VNet, account with public access disabled) |

### C02.a — New Foundry experience works with public network access disabled

> Coverage limit: Covers API/SDK use of projects and hosted agents; portal usage is criterion C02.b.

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C02-PNA-ALL-PROTOCOLS | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'These rules apply to all protocols, including REST and WebSocket' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/virtual-networks) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| S20-PUBLIC-DENIED-WITH-TOKEN | B@workstation-public | PASS | observed | 2026-10-02T14:51:28 | 20261002T145128Z-6882c0 | current | public internet, valid Entra token with Foundry User: HTTP 403 with a network-rule error |
| S02-INVOKE | A@workstation-public | PASS | observed | 2026-10-01T23:43:40 | 20261001T234340Z-54a033 | current (2 older) | 2 call(s): nonce present in the answer; nonce present in the answer; session reused=True |
| S02-INVOKE | B@in-vnet-runner | PASS | observed | 2026-10-02T15:22:57 | 20261002T152257Z-f3f1ad | current (3 older) | 2 calls from the VNet; nonce present in both answers=True; same session=True |

### C02.b — Chat with models from the portal when public access is disabled

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C02-PORTAL-ACCESS-PATH | limitation | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'Azure Bastion' present; 'ExpressRoute' present | [1](https://learn.microsoft.com/en-us/azure/foundry/how-to/configure-private-link) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| MAN-C02-PORTAL-CHAT-INVNET | B@browser-inside-vnet | PASS | manual | 2026-10-03T12:05:56 | 20261003T120556Z-0a872e | current | 2026-10-03 ~11:45 UTC. New Foundry portal (ai.azure.com, toggle on), directory of the lab, project proj1 of hapq-b-lab01 (public network access Disabled), Models > gpt-5-mini (Data Zone Standard) > Playground: message '… |
| MAN-C02-PORTAL-CHAT-PUBLIC | B@workstation-public | PASS | manual | 2026-10-03T12:06:38 | 20261003T120638Z-98ce11 | current (1 older) | 2026-10-03 ~11:50-12:05 UTC. Same project (proj1 of hapq-b-lab01) opened from the operator's normal browser on the public internet: (1) page 'Network access unavailable - This Foundry account is protected by network acc… |

### C02.c — Inbound: hosted agents reachable from private networks (incl. telemetry)

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C02-ENDPOINT-PRIVACY | contradiction | contradiction | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'stays publicly addressable' present; 'These rules apply to all protocols' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/virtual-networks) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| S21-DNS-PRIVATE | B@in-vnet-runner | PASS | observed | 2026-10-02T14:51:22 | 20261002T145122Z-b85952 | current | in-VNet resolution to 10.42.x for 5/5 names; workstation resolves them to non-private addresses for 5/5 |
| S02-INVOKE | A@workstation-public | PASS | observed | 2026-10-01T23:43:40 | 20261001T234340Z-54a033 | current (2 older) | 2 call(s): nonce present in the answer; nonce present in the answer; session reused=True |
| S02-INVOKE | B@in-vnet-runner | PASS | observed | 2026-10-02T15:22:57 | 20261002T152257Z-f3f1ad | current (3 older) | 2 calls from the VNet; nonce present in both answers=True; same session=True |
| S03-A2A-CARD | A@workstation-public | PASS | observed | 2026-10-02T07:02:44 | 20261002T070244Z-e5200c | current (2 older) | a2a enabled on hosted agent; card HTTP 200, protocol versions ['0.3', '1.0'] |
| S03-A2A-CARD | B@in-vnet-runner | PASS | observed | 2026-10-02T20:53:00 | 20261002T205300Z-332d90 | current | from the VNet: a2a enabled on a hosted agent of the private account; card HTTP 200, protocol versions ['0.3', '1.0'] |
| S20-PUBLIC-DENIED-WITH-TOKEN | B@workstation-public | PASS | observed | 2026-10-02T14:51:28 | 20261002T145128Z-6882c0 | current | public internet, valid Entra token with Foundry User: HTTP 403 with a network-rule error |
| S24-TELEMETRY-QUERY-PRIVATE | B@in-vnet-runner | PASS | observed | 2026-10-02T15:22:57 | 20261002T152257Z-f3f1ad | current (1 older) | telemetry query from the VNet: HTTP 200 |
| S24-TELEMETRY-QUERY-PRIVATE | B@workstation-public | PASS | observed | 2026-10-02T15:23:03 | 20261002T152303Z-8fee9d | current (1 older) | same query from the public internet: refused (ERROR: InsufficientAccessError: The provided credentials have insufficient access to perform the requested operation); control with the same identity on the public-query App… |

### C02.d — Outbound: hosted agents reach private resources (incl. telemetry)

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C02-OUTBOUND-VNET | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'customer-provided Azure Virtual Network for outbound traffic' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents) |
| DOC-C02-TELEMETRY-AMPLS | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'Azure Monitor Private Link Scope' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/virtual-networks) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| S22-OUTBOUND-PRIVATE-STORAGE | B@in-vnet-runner | PASS | observed | 2026-10-02T15:13:04 | 20261002T151304Z-6588f3 | current | hosted agent read the private blob with its own identity=True after 1 attempt(s); same blob from the public internet: HTTP 403 AuthorizationFailure |
| S07-STD-TOOL-AGENT-IDENTITY | B@in-vnet-runner | PASS | observed | 2026-10-02T20:29:55 | 20261002T202955Z-c6656e | current (1 older) | proj3 (Standard setup): private MCP tool validated a token whose oid equals the agent instance identity |
| S23-TELEMETRY-INGEST-PRIVATE | B@in-vnet-runner | PASS | observed | 2026-10-02T15:22:57 | 20261002T152257Z-f3f1ad | current (1 older) | 72 row(s) correlated with the tested request, queried from the VNet (ingestion public access disabled, data through AMPLS) |

### C02.e — Class A (10.0.0.0/8) subnets supported

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C02-CLASSA | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'supports private Class A address space' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/virtual-networks) |
| DOC-C02-CLASSA-ERRMSG | contradiction | contradiction | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'in the range of 172 or 192' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/virtual-networks) |
| DOC-C02-CGNAT | contradiction | contradiction | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'RFC 6598 (CGNAT)' present; 'aren't supported and cause routing failures' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agents-networking-deep-dive) [2](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/virtual-networks) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| S25-CLASSA-INJECTION | B@workstation-public | PASS | observed | 2026-10-02T15:02:33 | 20261002T150233Z-9c673f | current (1 older) | network-injected account on delegated subnet 10.42.0.0/24 (class A); hosted agent version 'active' |

### C02.f — Private Docker registries supported

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C02-PRIVATE-ACR | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'created after June 25, 2026 support a private' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents) |
| DOC-C02-3P-REGISTRY | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'JFrog Artifactory' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/private-registry-connections) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| S26-PRIVATE-ACR-IMAGE | B@in-vnet-runner | FAIL | observed | 2026-10-02T16:40:00 | 20261002T164000Z-3e5047 | current (4 older) | image-based version failed: {"code": "ProvisioningError", "message": "Agent version provisioning failed. Please retry. For troubleshooting, see https://aka.ms/hostedagents/tsg/provisioning"} |
| S26-STD-PRIVATE-ACR-IMAGE | B@in-vnet-runner | FAIL | observed | 2026-10-02T20:41:51 | 20261002T204151Z-0defe5 | current | proj3 (Standard setup), private ACR, no network exception: version failed: {"code": "ProvisioningError", "message": "Agent version provisioning failed. Please retry. For troubleshooting, see https://aka.ms/hostedagents/… |
| S27-3P-REGISTRY | B@in-vnet-runner | BLOCKED | - | - | - | - | Blocked: A third-party private registry exposed through Private Link is required. |

### C03 — Native A2A exposure for hosted agents

> **Observed conclusion:** Environment A, westeurope, 2026-10-02: incoming A2A can be enabled on a hosted agent and its v1.0 agent card is served (HTTP 200), but inbound invocation (JSON-RPC SendMessage, A2A-Version 1.0, official a2a-sdk 1.0.2) is refused by the platform: error -32099, reason HOSTED_AGENT_NOT_SUPPORTED. Native A2A exposure of a hosted agent is therefore NOT available in this lab at this date. A prompt agent or proxy would not satisfy this requirement.

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C03-A2A-HOSTED | contradiction | contradiction | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'protocols/a2a' present; 'hosted' absent | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents) [2](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/enable-agent-to-agent-endpoint) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| S03-A2A-CARD | A@workstation-public | PASS | observed | 2026-10-02T07:02:44 | 20261002T070244Z-e5200c | current (2 older) | a2a enabled on hosted agent; card HTTP 200, protocol versions ['0.3', '1.0'] |
| S03-A2A-CARD | B@in-vnet-runner | PASS | observed | 2026-10-02T20:53:00 | 20261002T205300Z-332d90 | current | from the VNet: a2a enabled on a hosted agent of the private account; card HTTP 200, protocol versions ['0.3', '1.0'] |
| S04-A2A-MESSAGE | A@workstation-public | FAIL | observed | 2026-10-02T07:02:44 | 20261002T070244Z-e5200c | current (2 older) | explicit platform refusal (A2A JSON-RPC -32099): The requested A2A operation is not supported for this hosted-agent target. |
| S04-A2A-MESSAGE | B@in-vnet-runner | NOT_TESTED | - | - | - | - | not implemented yet (phase B planned) |
| S05-A2A-NO-TOKEN-401 | A@workstation-public | PASS | observed | 2026-10-02T07:02:44 | 20261002T070244Z-e5200c | current (2 older) | HTTP 401 as expected (authentication only; says nothing about network policy) |
| S05-A2A-NO-TOKEN-401 | B@in-vnet-runner | NOT_TESTED | - | - | - | - | not implemented yet (phase B planned) |
| S06-A2A-NO-ROLE-403 | A@workstation-public | PASS | observed | 2026-10-02T10:47:30 | 20261002T104730Z-0dd9c2 | current (8 older) | access refused to an identity without Foundry role: HTTP 404 (Agent Not Found); same request with a Foundry role in this run: HTTP 200; expected 403, observed 404 (resource hidden) (agent card GET only, by the tool's ma… |
| S06-A2A-NO-ROLE-403 | B@in-vnet-runner | NOT_TESTED | - | - | - | - | not implemented yet (phase B planned) |

### C04.a — Integration with external identity providers

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C04-INBOUND-NON-ENTRA | absence | NOT_TESTED | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | any of ['OpenID Connect', 'OIDC', 'Okta', 'Auth0', 'external identity provider'] absent | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/configure-agent) |
| DOC-C04-TOOL-CUSTOM-OAUTH | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'custom OAuth' present; 'non-Microsoft' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/mcp-authentication) |
| DOC-C04-CROSS-TENANT | limitation | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'Cross-tenant token exchange isn't supported' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/mcp-authentication) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| S30-INBOUND-NON-ENTRA | A@workstation-public | PASS | observed | 2026-10-02T11:07:21 | 20261002T110721Z-f574d1 | current (1 older) | token from a non-Entra issuer (synthetic): refused with HTTP 403 (empty body=True); confirms the documented Entra-only inbound limitation; not an integration with a real external IdP |
| S31-TOOL-EXTERNAL-IDP | A@workstation-public | BLOCKED | - | - | - | - | Blocked: A third-party OAuth/OIDC provider tenant with an app registration is required. |
| S31-TOOL-EXTERNAL-IDP | B@in-vnet-runner | BLOCKED | - | - | - | - | Blocked: A third-party OAuth/OIDC provider tenant with an app registration is required. |

### C04.b — Agent identity propagated to tool calls

> Coverage limit: Proves the agent's Entra identity reaches a tool. Propagation of the END USER identity (OBO / OAuth passthrough) is not exercised. Private environment: PASS on a Standard-setup project (proj3); on the Basic network-injected setup the Foundry tool paths fail with a DataProxy DNS error (S07 B run 2026-10-02, 0/5 + sample 19 control 0/3), consistent with Learn: private MCP requires Standard setup.

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C04-AGENT-IDENTITY-TOOLS | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'AgenticIdentityToken' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-identity) |
| DOC-C04-CALL-ID | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'x-agent-foundry-call-id' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/tool-authentication) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| S14-MCP-NO-TOKEN-401 | A@workstation-public | PASS | observed | 2026-10-02T10:17:22 | 20261002T101722Z-46bd05 | current (3 older) | HTTP 401 as expected |
| S14-MCP-NO-TOKEN-401 | B@in-vnet-runner | PASS | observed | 2026-10-02T15:42:39 | 20261002T154239Z-25e580 | current | HTTP 401 as expected |
| S15-MCP-WRONG-AUDIENCE-401 | A@workstation-public | PASS | observed | 2026-10-02T10:17:22 | 20261002T101722Z-46bd05 | current (3 older) | HTTP 401 as expected |
| S15-MCP-WRONG-AUDIENCE-401 | B@in-vnet-runner | PASS | observed | 2026-10-02T20:53:00 | 20261002T205300Z-332d90 | current | HTTP 401 as expected |
| S07-TOOL-AGENT-IDENTITY | A@workstation-public | PASS | observed | 2026-10-02T10:17:22 | 20261002T101722Z-46bd05 | current (3 older) | tool validated a token whose principal is the expected agent identity |
| S07-STD-TOOL-AGENT-IDENTITY | B@in-vnet-runner | PASS | observed | 2026-10-02T20:29:55 | 20261002T202955Z-c6656e | current (1 older) | proj3 (Standard setup): private MCP tool validated a token whose oid equals the agent instance identity |

### C05.a — Hosted agents available in West Europe

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C05-WESTEUROPE | support | PASS | 2026-10-02T20:55:16 | current (7 older) | list item 'West Europe' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| PF-MODEL-AVAILABLE | subscription@workstation-public | PASS | observed | 2026-10-02T20:55:12 | 20261002T205512Z-8a4270 | current (7 older) | skus=['DataZoneProvisionedManaged', 'DataZoneStandard', 'GlobalProvisionedManaged', 'GlobalStandard'], lifecycle=['GenerallyAvailable'] |
| PF-MODEL-QUOTA | subscription@workstation-public | PASS | observed | 2026-10-02T20:55:12 | 20261002T205512Z-8a4270 | current (7 older) | used 20 / limit 300 (free 280) |
| PF-ARM-LOCATIONS | subscription@workstation-public | PASS | observed | 2026-10-02T20:55:12 | 20261002T205512Z-8a4270 | current (7 older) | offered=True |
| S01-GA-CREATE | A@workstation-public | PASS | observed | 2026-10-01T23:43:40 | 20261001T234340Z-54a033 | current (2 older) | status sequence active (v1 API, no preview header, no feature registration) |
| S01-GA-CREATE | B@in-vnet-runner | PASS | observed | 2026-10-02T15:02:26 | 20261002T150226Z-b329b5 | current (1 older) | status sequence creating -> active (from inside the VNet, account with public access disabled) |

### C05.b — Hosted agents available in North Europe (main EU test region)

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C05-NORTHEUROPE | support | FAIL | 2026-10-02T20:55:16 | current (7 older) | list item 'North Europe' absent | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents) |
| DOC-C05-NORTHEUROPE-PRIVATE-VNET | support | FAIL | 2026-10-02T20:55:16 | current (7 older) | 'North Europe' absent | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/limits-quotas-regions) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| PF-ARM-LOCATIONS-REQUIRED | subscription@workstation-public | NOT_TESTED | observed | 2026-10-02T20:55:12 | 20261002T205512Z-8a4270 | current (7 older) | offered=True; hosted-agent support is not observable without deploying there (out of scope) |
| NE-NOT-DEPLOYED | -@- | NOT_TESTED | - | - | - | - | Never executed by design: no deployment in North Europe only to probe availability. A lab success in westeurope does not validate North Europe. |

### C05.c — Hosted agents available in East US 2

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C05-EASTUS2 | support | PASS | 2026-10-02T20:55:16 | current (7 older) | list item 'East US 2' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents) |

### C05.d — Hosted agents available in West US

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C05-WESTUS | support | PASS | 2026-10-02T20:55:16 | current (7 older) | list item 'West US' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents) |

### C06 — Enterprise-wide registry of tools / agents (cross projects)

> Coverage limit: Only cross-project tool consumption and agent inventory inside ONE subscription are exercised. Tenant-wide registries (Agent 365 registry, API Center private tool/skill catalogs), governance, approval workflows and cross-subscription discovery are not covered.

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C06-TOOLBOX-PROJECT-SCOPED | limitation | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | '/api/projects/<project>/toolboxes/' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/use-toolbox-hosted-agent) |
| DOC-C06-CONTROL-PLANE | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'across all the projects' present | [1](https://learn.microsoft.com/en-us/azure/foundry/control-plane/how-to-manage-agents) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| S12-XPROJECT-TOOL | A@workstation-public | FAIL | observed | 2026-10-02T19:50:32 | 20261002T195032Z-518582 | current (2 older) | toolbox refused the cross-project call: 2026-10-02 19:43:42,687 INFO httpx: HTTP Request: POST https://<host>.services.ai.azure.com/api/projects/proj1/toolboxes/hapq-tools/mcp?api-version=v1 \u0022HTTP/1.1 403 For… |
| S12-XPROJECT-TOOL | B@in-vnet-runner | NOT_TESTED | - | - | - | - | not implemented yet (phase B planned) |
| S12-XPROJECT-TOOL-RBAC | A@workstation-public | FAIL | observed | 2026-10-02T19:59:08 | 20261002T195908Z-649b66 | current (1 older) | with Foundry User on project 1, project 1's toolbox answered the project-2 agent with HTTP 200 -> 204 -> 200 -> 403 (MCP handshake/listing vs tool call); no tool call reached the tool (0) |
| S13-AGENT-INVENTORY | A@workstation-public | PASS | observed | 2026-10-02T19:50:32 | 20261002T195032Z-518582 | current (2 older) | all agents enumerated with project attribution (aggregation of per-project calls); counts per project: {'proj1': 4, 'proj2': 1}; an inventory built by iterating projects, not an enterprise registry |
| S13-AGENT-INVENTORY | B@in-vnet-runner | NOT_TESTED | - | - | - | - | not implemented yet (phase B planned) |
| MAN-C06-CONTROL-PLANE | A@workstation-public | NOT_TESTED | - | - | - | - | implemented, not run (requires provisioned lab and approval) |
| MAN-C06-CONTROL-PLANE | B@in-vnet-runner | NOT_TESTED | - | - | - | - | not implemented yet (phase B planned) |

### C07 — Observability of hosted agent deployment

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C07-LOGS | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'azd ai agent monitor' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/monitor-hosted-agent-logs) |
| DOC-C07-TRACING | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'automatically injects an Application Insights connection string' present | [1](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| S09-OBS-STATUS | A@workstation-public | PASS | observed | 2026-10-01T23:43:40 | 20261001T234340Z-54a033 | current (2 older) | 1 status read(s), final 'active' |
| S09-OBS-STATUS | B@in-vnet-runner | PASS | observed | 2026-10-02T15:02:26 | 20261002T150226Z-b329b5 | current (1 older) | 9 status read(s) from the VNet, final 'active' |
| S10-OBS-LOGS | A@workstation-public | PASS | observed | 2026-10-01T23:43:40 | 20261001T234340Z-54a033 | current (2 older) | 11 log line(s) read for the tested session |
| S10-OBS-LOGS | B@in-vnet-runner | PASS | observed | 2026-10-02T15:02:26 | 20261002T150226Z-b329b5 | current (1 older) | 69 log line(s) read from the VNet for the tested session |
| S11-OBS-TRACES | A@workstation-public | PASS | observed | 2026-10-01T23:43:40 | 20261001T234340Z-54a033 | current (2 older) | 83 telemetry row(s) carry the tested request/session id |
| S23-TELEMETRY-INGEST-PRIVATE | B@in-vnet-runner | PASS | observed | 2026-10-02T15:22:57 | 20261002T152257Z-f3f1ad | current (1 older) | 72 row(s) correlated with the tested request, queried from the VNet (ingestion public access disabled, data through AMPLS) |

### C08 — LangGraph integration in a hosted agent

**Documentary status** (what Microsoft Learn states; not a demonstration)

| Check | Polarity | Status | Date (UTC) | Freshness | Statement found | Sources |
|---|---|---|---|---|---|---|
| DOC-C08-LANGGRAPH | support | PASS | 2026-10-01T21:42:24 | STALE: different configuration (2 older) | 'langchain-azure-ai' present | [1](https://learn.microsoft.com/en-us/azure/foundry/how-to/develop/langchain-hosted-agents) |

**Execution results** (per environment and execution point)

| Check / scenario | Environment@execution point | Status | Evidence | Date (UTC) | Run | Freshness | Observation |
|---|---|---|---|---|---|---|---|
| S08-LANGGRAPH | A@workstation-public | PASS | observed | 2026-10-02T06:57:53 | 20261002T065753Z-f5fd7a | current (2 older) | version active; nonce present in the answer |
| S08-LANGGRAPH | B@in-vnet-runner | PASS | observed | 2026-10-02T15:13:04 | 20261002T151304Z-6588f3 | current | LangGraph agent active in the private account; nonce in answer=True |
