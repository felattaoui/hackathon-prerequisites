# Documentary evidence, contradictions and manual verifications

Documentary checks are re-executable (`python -m hapq docs`): each one fetches the Learn page, evaluates the
assertions in `config/doc_assertions.toml`, and stores the page hash and fetch time. A documentary result says
what Microsoft states. **It is not a demonstration on Azure.**

Only hosted-agent documentation is used as evidence for hosted agents. Statements about prompt agents or
classic agents are never used to infer hosted-agent support.

## Official support statements (as of 2026-10-01)

| Criterion | Statement | Source |
|---|---|---|
| C01 | "Microsoft Foundry Hosted Agents is generally available." | [Agent Framework — Foundry hosted agents](https://learn.microsoft.com/agent-framework/hosting/foundry-hosted-agent) |
| C01 | On the GA `v1` API (azure-ai-projects ≥ 2.3.0) the preview feature header is no longer required. | [Migrate from the hosted agent preview](https://learn.microsoft.com/azure/foundry/agents/how-to/migrate-hosted-agent-preview) |
| C02.a | With public network access disabled, "these rules apply to all protocols, including REST and WebSocket". | [Virtual networks](https://learn.microsoft.com/azure/foundry/agents/how-to/virtual-networks) |
| C02.d | Hosted agents can use a customer-provided VNet for outbound traffic. | [Hosted agents](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents#private-networking) |
| C02.e | Every Agent Service region supports private class A (`10.0.0.0/8`). | [Virtual networks — limitations](https://learn.microsoft.com/azure/foundry/agents/how-to/virtual-networks#limitations) |
| C02.f | Projects created after 2026-06-25 support a network-secured ACR; non-ACR registries are supported through registry connections. | [Hosted agents](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents), [Bring your own registry](https://learn.microsoft.com/azure/foundry/agents/how-to/private-registry-connections) |
| C04.a | Tool connections support custom OAuth for non-Microsoft OAuth servers; cross-tenant token exchange isn't supported. | [MCP authentication](https://learn.microsoft.com/azure/foundry/agents/how-to/mcp-authentication) |
| C04.b | MCP and A2A connections support agent-identity auth (`AgenticIdentityToken`); `x-agent-foundry-call-id` lets downstream Foundry services resolve the caller. | [Agent identity](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-identity), [Tool authentication](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/tool-authentication) |
| C06 | Toolbox endpoints are project-scoped; the Control Plane lists agents across the projects of a subscription (preview pane). | [Use a toolbox with a hosted agent](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/use-toolbox-hosted-agent), [Manage agents](https://learn.microsoft.com/azure/foundry/control-plane/how-to-manage-agents) |
| C07 | Container logs (`azd ai agent monitor`); the Application Insights connection string is injected automatically. | [Monitor hosted agent logs](https://learn.microsoft.com/azure/foundry/agents/how-to/monitor-hosted-agent-logs) |
| C08 | LangGraph agents are hosted with `langchain-azure-ai[hosting]`. | [LangChain hosted agents](https://learn.microsoft.com/azure/foundry/how-to/develop/langchain-hosted-agents) |

## Contradictions (reported, not resolved)

| # | Topic | Statement A | Statement B | How the kit settles it |
|---|---|---|---|---|
| 1 | Hosted-agent endpoint privacy (C02.c) | azd pivot: the agent endpoint "stays publicly addressable" "in this preview" | templates pivot: public access disabled "applies to all protocols" | `S20-PUBLIC-DENIED-WITH-TOKEN`: a **valid** token from the public internet must get a network 403, not 401 |
| 2 | A2A for hosted agents (C03) | Hosted-agent page lists `…/protocols/a2a` for hosted agents | Enable-A2A page lists only prompt agents as supported types | `S03-A2A-CARD` on a hosted agent |
| 3 | Class A (C02.e) | Class A supported in every region | Documented error text: "range of 172 or 192" | `S25-CLASSA-INJECTION` with a `10.42.0.0/16` VNet |
| 4 | CGNAT (outside checklist) | `100.64.0.0/10` allowed (virtual networks) | CGNAT "aren't supported and cause routing failures" (deep dive) | Not tested; recorded for address planning |
| 5 | GA wording (C01) | Hosted agents are GA | configure-private-link still says "hosted (preview) agents" | `S01-GA-CREATE` without preview header or feature registration |
| 6 | Deployment role | Foundry User suffices (hosted-agent permissions) | LangGraph page: requires Foundry Project Manager | Observed during Phase A with the caller's actual roles |
| 7 | Private-lab data resources | Templates: secured standard setup requires BYO Cosmos DB, AI Search, Storage | Networking options: platform-managed data resources are possible for many hosted-agent scenarios | Phase B deployment outcome |
| 8 | Toolbox in a Python hosted agent (C04.b) | [Agent Framework — Foundry Toolbox](https://learn.microsoft.com/agent-framework/integrations/by-component/tools/foundry-toolbox) and the toolbox page point to a per-request factory: `ResponsesHostServer(agent=create_agent, history_source="agent_server")` (package flagged beta, installed with `--pre`, no version pinned) | Of the published `agent-framework-foundry-hosting` versions, 5 were examined (1.0.0a260709, 1.0.0b260730, 1.0.0b260827, 1.0.0b260910, 1.0.0b260918 = latest on 2026-10-02; the 19 others were not): each types `agent` as an instance and calls `agent.create_session()`: the factory fails at request time (`'function' object has no attribute 'create_session'`). The package's own `FoundryToolbox` docstring shows one `Agent` instance with `tools=FoundryToolbox(credential)`; it injects `x-agent-foundry-call-id` per outbound request | Kit uses the package pattern (agents/toolbox); S07 PASS on 2026-10-02. Not a Foundry limitation |
| 9 | Identity that pulls a hosted-agent image (C02.f) | [Deploy with a private ACR](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent-private-azure-container-registry): the per-agent **agent identity** needs **AcrPull**; network reachability of the registry by the runtime is "a platform concern beyond this article" | [Deploy a hosted agent — troubleshooting](https://learn.microsoft.com/azure/foundry/agents/how-to/deploy-hosted-agent#troubleshooting): the **project** managed identity needs **Container Registry Repository Reader** and the ACR `azureADAuthenticationAsArmPolicy` must be enabled. foundry-samples template 15 grants AcrPull to the project identity only and sets `networkRuleBypassOptions: 'AzureServices'` | `S26-PRIVATE-ACR-IMAGE` (B, 2026-10-02): project identities AcrPull + agent identity AcrPull, policy enabled, legacy RBAC mode, `networkRuleBypassOptions: None` → generic `ProvisioningError` twice. Operator-approved retry with `networkRuleBypassOptions: AzureServices` (public access still disabled, same image and roles): same `ProvisioningError` (`S26-PRIVATE-ACR-IMAGE-BYPASS` FAIL); setting restored to `None` |
| 10 | Private MCP prerequisites (C04.b, environment B) | [MCP tool — Known limitations](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/model-context-protocol#known-limitations): "Private MCP requires Standard Agent Setup … Basic agent setup doesn't support private MCP endpoints." Microsoft Q&A [answers/a/12721949](https://learn.microsoft.com/answers/a/12721949) (accepted answer, forum, not documentation): the DataProxy resolves DNS from Microsoft-managed infrastructure; internal ACA + private DNS "not fully supported today", workaround = public endpoint or public proxy (APIM/Front Door/App Gateway) | Same MCP page, § Public and private endpoints: use template 19 "or 11-private-network-basic-project if you dont want to bring your own resources". foundry-samples issue #955 (2026-09-04, community): a clean template 19 deployment passed private MCP enumeration and invocation | Environment B is network-injected **Basic** (capability host without BYO connections): it does not meet the Standard prerequisite. Untested: a Standard project (project capability host with Cosmos DB, Storage and AI Search connections; capability settings are only in UK South/Canada Central) would require operator approval (cost) |

Sub-features still marked preview inside the GA service: A2A v0.3 (v1.0 is GA), draft versions, MCP endpoint
protocol, the Control Plane Operate pane, AI Gateway, `az cognitiveservices agent logs` (CLI marked preview).
The Python `agent-framework-foundry-hosting` package is prerelease. Learn publishes no GA date and no hosted-agent
price rates (pricing page only).

## Private-network diagnostics (environment B, 2026-10-02)

Evidence files are kept under `results/diag/` (git-ignored, redacted).

| Observation | Source | What it shows | What it does not show |
|---|---|---|---|
| Toolbox `tools/list` on the private MCP tool fails: `INVALID_ARGUMENT … Name or service not known. The host name could not be resolved from the selected network path … verify the capability host, VNet, DataProxy, private DNS zone…` | Full session log of `hapq-b-mcp` (`mcp-session.log`) | The Toolbox server (DataProxy path) does not resolve the internal Container Apps FQDN. The agent reaches the Toolbox (HTTP 200/204) | Why the DataProxy resolver does not use the VNet zone |
| Deployed configuration matches foundry-samples 19 §4.2–4.4: internal environment in a dedicated delegated subnet, app ingress `external`, private zone = `defaultDomain`, wildcard `*` → `staticIp`, VNet link `Completed`; account capability host present (`customerSubnet` = agent subnet, `Succeeded`); no custom NSG rule | Read-only `az` queries | Not a missing zone, record, link, capability host or NSG rule | Only difference: `minReplicas` 0 (sample: 1). Not relevant to a name-resolution error |
| From the hosted-agent sandbox (Microsoft diagnostic agent, ZIP mode, no ACR, no model): DNS via `168.63.129.16` resolves the MCP FQDN to `10.42.5.144`, the ACR registry to `10.42.1.5` and the ACR data endpoint to `10.42.1.4`; TCP connects (0.2 ms); TLS handshake **times out** to the MCP tool and to the ACR registry endpoint; TLS **succeeds** to the ACR data endpoint (HTTP 403 DENIED without token) | `netdiag.json` | The agent sandbox resolves private names; its egress to two private endpoints of the same VNet fails at TLS while a third succeeds (same subnet, no NSG). Sandbox network: loopback address, default route via `169.254.0.1`: traffic goes through a platform component | It does not test the Toolbox proxy path. The cause of the TLS timeouts (platform egress filtering by name is a hypothesis, not documented on Learn) |

### Targeted retry of S07 and S26 prerequisites (environment B, 2026-10-02, 18:21–19:06 UTC)

| Item | Source | Observation |
|---|---|---|
| Known issue, sample 19 | foundry-samples `infrastructure/infrastructure-setup-bicep/19-private-network-agent-tools/tests/TESTING-GUIDE.md` (identical at commit `d807d9a` and on `main`), §8.9, Troubleshooting and Known Limitations | "Agent tests may fail ~50% of the time with `TaskCanceledException` due to Hyena cluster routing. The Data Proxy is only deployed on one of two scale units, and the load balancer routes in round-robin fashion. Use `--retry`." The same guide marks "MCP Tool via Agent (private server)" as passing ("C# RemoteMcpConnector — VNet-aware") and states that the DataProxy reaches internal Container Apps "via VNet DNS natively" |
| Second known issue, in the sample script itself | `tests/test_mcp_tools_agents_v2.py` (same commit), exception handler | On `424` / `Failed Dependency`: "Known Issue: DNS Resolution — Data Proxy cannot resolve private Container Apps DNS." Not described in the guide |
| proj1 creation date (B) | ARM `systemData.createdAt` of `accounts/hapq-b-lab01/projects/proj1` (read 2026-10-02) | `2026-10-02T11:06:42Z` (account `2026-10-02T11:02:20Z`) |
| Private ACR support rule | Learn `concepts/hosted-agent-permissions#azure-resource-setup`, `concepts/hosted-agents#platform-details`, `how-to/deploy-hosted-agent#prerequisites` | "Projects created after June 25, 2026 support a private registry." proj1 is after that date: per Learn, the private ACR is supported for this project. S26 not retried (operator review first) |
| `S07-PRIVATE-MCP-RETRY` | `results/diag/20261002T182420Z-6f4ecd/s07-retry.json` | 0/5 identical attempts succeeded (18:25:39, 18:28:50, 18:29:58, 18:30:30, 18:31:08 UTC; `x-ms-region` West Europe); each attempt has its own `response_id` and `trace-id`; all five fail with the same toolbox `tools/list` error ("Name or service not known … could not be resolved from the selected network path"). No `TaskCanceledException`: the pattern does not match the documented 50 % routing issue. The original `S07-TOOL-AGENT-IDENTITY` FAIL is kept |
| `min-replicas` of the private MCP app | `az containerapp update --min-replicas 1` (18:40 UTC) + `infra/phase-b-private/main.bicep` | Set to 1 as in sample 19 §4.3: alignment on the reference, not a fix. Network, DNS and ACR settings unchanged |
| `S07-CONTROL-PROMPT-AGENT` | Sample 19 `test_mcp_tools_agents_v2.py` copied verbatim (`tools/sample19/`), run from the in-VNet runner with `--test private --retry 3 --keep-agent` against the kit private MCP tool; `results/diag/20261002T183932Z-ecb4ac/sample19-mcp.txt` | 3/3 attempts fail with `Error code: 400 … Error encountered while enumerating tools from remote server … Name or service not known. The host name could not be resolved from the selected network path`. Same message as the hosted-agent Toolbox path. The prompt-agent → Tool Server → DataProxy path does not reach the server either. Request ids are GUID-shaped and masked by the kit redaction; the response ids and trace ids of the S07 retries are kept. The script's direct-HTTP connectivity step (from the runner VM, unauthenticated) timed out on a read during the revision update; it says nothing about the DataProxy path |

Conclusion (2026-10-02): the S07 failure is reproducible (0/5 + 0/3), not intermittent, and not specific to the Toolbox: both Foundry tool paths (hosted agent → Toolbox, prompt agent → Tool Server) fail at name resolution of the internal Container Apps FQDN through the DataProxy, while the deployment matches sample 19 and the private zone resolves from the VNet and from the agent sandbox. This matches the DNS known issue printed by the sample script itself; it contradicts the guide's "Validated" row for private MCP. Next step outside the kit: support case with the response ids, trace ids and region above.

### Standard setup project (proj3) and remaining scenarios (environment B and A, 2026-10-02, 19:14–20:30 UTC)

| Item | Source / evidence | Observation |
|---|---|---|
| proj3 in Standard agent setup on the existing network-injected account | `infra/phase-b-private/standard-proj3.bicep` (mirrors foundry-samples 15 at `d807d9a`: BYO resources with public access disabled + private endpoints, AAD connections, pre/post caphost roles, project capability host `caphostproj` = Succeeded) | Deviations: AI Search SKU `basic`; account capability host reused (auto-created; a second one returns 409 per the template); Cosmos DB region below |
| Cosmos DB region | ARM errors 19:19 and 19:44 UTC; Learn virtual-networks, Limitations: "Other Azure resources, such as Azure Cosmos DB, Azure AI Search, and Azure Storage, can be deployed in different regions" | West Europe and North Europe refused account creation: `ServiceUnavailable … high demand … To request region access for your subscription … aka.ms/cosmosdbquota` (a quota request: operator decision, not made). Created in **East US 2** (a region where the subscription already runs Cosmos DB) with its private endpoint in the West Europe VNet. Synthetic data only. Two failed accounts (West/North Europe) are kit-owned and removed at cleanup |
| `S07-STD-CONTROL-PROMPT-AGENT` | `results/diag/20261002T*/sample19-mcp-std.txt` (sample 19 script, unmodified) | 3/3: `Data proxy returned 424 because the customer-managed downstream service responded with 401. This indicates a customer-managed downstream issue rather than a data proxy failure.` The DataProxy **resolved and reached** the private MCP tool on proj3; the tool refused the unauthenticated sample call (it requires Entra). Same tool, VNet and DNS returned "Name or service not known" on the Basic-setup project |
| `S07-STD-TOOL-AGENT-IDENTITY` | run 20261002T202955Z | **PASS**: hosted agent → Toolbox (connection `RemoteTool`, `AgenticIdentityToken`) → DataProxy → internal Container App; the tool validated a token whose `oid` equals the agent instance identity. Confirms Learn "Private MCP requires Standard Agent Setup" by observation; the template-11 suggestion on the same page (basic, no BYO resources) is contradicted for private MCP |
| `S12-XPROJECT-TOOL` (A) | run 20261002T195032Z, container log line `POST …/projects/proj1/toolboxes/hapq-tools/mcp … HTTP/1.1 403 Forbidden` | Default: project 1's toolbox refuses the project-2 agent (403). Matches the documentary hypothesis (project-scoped toolbox). A first run had recorded false refusals (chunk-level log matching): classifier fixed and covered by a regression test |
| `S12-XPROJECT-TOOL-RBAC` (A) | run 20261002T195908Z | With Foundry User granted to the project-2 agent identity on project 1 only: toolbox statuses `200 -> 204 -> 200 -> 403` (MCP initialize, notification, listing accepted; next request refused); no tool call reached the tool |
| `S13-AGENT-INVENTORY` (A) | run 20261002T195032Z | PASS: agents of proj1 (4) and proj2 (1) enumerated with project attribution by iterating projects. An inventory can be built; this is not an enterprise registry |
| `S26-DIAG-TLS` | `results/diag/*/tls-vm.json`, `netdiag-s26.json` | VM: all six private endpoints answer TLS 1.3 immediately. Runtime with 5 s timeout: both ACR endpoints time out; with 20 s timeout: registry endpoint handshake **11.3 s**, data endpoint 53 ms, others 55–95 ms. Earlier MCP-tool TLS timeouts from the runtime disappeared after `min-replicas 1` (cold start) |

## Manual verifications

Some points cannot be proven by code.

**GA (C01).** Code can show that (a) no agent-related preview feature is registered in the subscription
(`PF-PREVIEW-FEATURES`) and (b) a version is created on the GA API without a preview header (`S01-GA-CREATE`).
The legal GA status itself is documentary. Manual confirmation: check the
[Foundry Agent Service pricing page](https://azure.microsoft.com/pricing/details/foundry-agent-service/) and the
Product Terms / Supplemental Preview Terms for any preview mention of hosted agents, and record the result with
`python -m hapq manual record` if your process requires it.

**Portal chat with public access disabled (C02.b).** Steps are listed by `python -m hapq manual list`
(`MAN-C02-PORTAL-CHAT-INVNET`, `MAN-C02-PORTAL-CHAT-PUBLIC`). Recording example:

```bash
python -m hapq manual record --id MAN-C02-PORTAL-CHAT-PUBLIC --status PASS \
  --observed "403 Virtual Network/Firewall rules on playground from public internet" --operator <alias>
```

The in-VNet case is BLOCKED until a browser inside the lab network exists (VPN, ExpressRoute or Bastion jump box).
