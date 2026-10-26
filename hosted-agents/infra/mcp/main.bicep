// MCP step (agent identity -> tool, criterion C04.b), reusing the existing lab: account hapq-a-<suffix>, proj1,
// gpt-5-mini and Log Analytics are NOT redeployed. Adds only what the test tool needs:
//   user-assigned identity, ACR Basic, AcrPull (identity -> ACR), Container Apps environment (Consumption profile,
//   no private endpoint), and in pass 2 the Container App running tools/mcp_whoami.
// Deployed only by `hapq lab mcp --execute --confirm <rg>` (what-if guard + ledger). Not deployed yet.

targetScope = 'resourceGroup'

param location string
@maxLength(8)
param suffix string
param deployMcpApp bool = false
param mcpImage string = ''
param tenantId string = tenant().tenantId
@description('Audiences accepted by the tool: Application ID URI and app id of the kit app registration.')
param mcpAudiences string = ''

var tags = { kit: 'hapq', phase: 'a', purpose: 'hosted-agent-prereq-lab' }
var accountName = 'hapq-a-${suffix}'
var mcpAppName = 'ca-hapq-a-mcp-${suffix}'
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: 'log-hapq-a-${suffix}'
}

resource mcpIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-hapq-a-mcp-${suffix}'
  location: location
  tags: tags
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'crhapqa${suffix}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false, publicNetworkAccess: 'Enabled' }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, mcpIdentity.id, acrPullRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: mcpIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-hapq-a-${suffix}'
  location: location
  tags: tags
  properties: {
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey }
    }
  }
}

resource mcpApp 'Microsoft.App/containerApps@2024-03-01' = if (deployMcpApp) {
  name: mcpAppName
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${mcpIdentity.id}': {} } }
  properties: {
    environmentId: acaEnv.id
    workloadProfileName: 'Consumption'
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      registries: [{ server: acr.properties.loginServer, identity: mcpIdentity.id }]
    }
    template: {
      containers: [{
        name: 'mcp'
        image: mcpImage
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: [
          { name: 'MCP_TENANT_ID', value: tenantId }
          { name: 'MCP_AUDIENCES', value: mcpAudiences }
          { name: 'MCP_ALLOWED_HOSTS', value: '${mcpAppName}.${acaEnv.properties.defaultDomain}' }
          { name: 'PROBE_PROJECT_ENDPOINT', value: 'https://${accountName}.services.ai.azure.com/api/projects/proj1' }
          { name: 'AZURE_CLIENT_ID', value: mcpIdentity.properties.clientId }
        ]
        probes: [{ type: 'Readiness', httpGet: { path: '/healthz', port: 8000 } }]
      }]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
  dependsOn: [acrPull]
}

output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output mcpIdentityPrincipalId string = mcpIdentity.properties.principalId
output mcpUrl string = deployMcpApp ? 'https://${mcpAppName}.${acaEnv.properties.defaultDomain}' : ''
