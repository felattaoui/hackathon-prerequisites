// Phase A — public lab: hosted-agent scenarios S01–S15 (GA, invoke, A2A, agent identity -> MCP tool,
// LangGraph, observability, cross-project). Deployed only by `hapq lab deploy --phase A --execute`.
// Two passes: deployMcpApp=false (infrastructure), then deployMcpApp=true after the image is built in ACR.
// Every resource receives `location` explicitly (the resource-group region does not determine resource regions).
// Role assignments created here: AcrPull for the MCP app identity on the lab ACR (needed to pull its image).
// No other role is assigned: the caller's existing Owner + Foundry User cover deployment, agent creation and
// trace queries (checked by preflight PF-ROLES).

targetScope = 'resourceGroup'

@description('Region of every regional resource. No default: must be chosen explicitly.')
param location string

@description('Short suffix to make global names unique (lowercase letters/digits).')
@maxLength(8)
param suffix string

param modelName string = 'gpt-5-mini'
param modelVersion string = '2025-08-07'
@allowed(['DataZoneStandard', 'GlobalStandard'])
param modelSku string = 'DataZoneStandard'
@minValue(1)
@maxValue(20)
param modelCapacity int = 10

@description('Second pass only: deploy the MCP test tool container app.')
param deployMcpApp bool = false
@description('Image reference in the lab ACR, e.g. <acr>.azurecr.io/mcp-whoami:<tag>. Required when deployMcpApp.')
param mcpImage string = ''
@description('Entra tenant id used by the MCP tool to validate tokens.')
param tenantId string = tenant().tenantId
@description('Application ID URI and app id accepted as audience by the MCP tool (from the kit app registration).')
param mcpAudiences string = ''

@description('Monthly budget amount (USD) for the resource group. Alerts only; NOT a spending cap.')
param budgetAmount int = 50
@description('E-mail addresses that receive budget alerts. Empty = no budget created.')
param budgetContactEmails array = []
@description('First day of the current month, yyyy-MM-01 (budget start date must be the first of a month).')
param budgetStartDate string = ''

var tags = { kit: 'hapq', phase: 'a', purpose: 'hosted-agent-prereq-lab' }
var accountName = 'hapq-a-${suffix}'
var acrName = 'crhapqa${suffix}'
var mcpAppName = 'ca-hapq-a-mcp-${suffix}'
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-hapq-a-${suffix}'
  location: location
  tags: tags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30, workspaceCapping: { dailyQuotaGb: 1 } }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-hapq-a-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: modelName
  sku: { name: modelSku, capacity: modelCapacity }
  properties: { model: { format: 'OpenAI', name: modelName, version: modelVersion } }
}

// Two projects: cross-project scenarios S12/S13.
resource projects 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = [for p in ['proj1', 'proj2']: {
  parent: account
  name: p
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'hapq ${p}', description: 'Hosted-agent prerequisite lab (synthetic data only)' }
  dependsOn: [deployment]
}]

// Application Insights connection (pattern of the azd hosted-agent starter; validated by S11).
// One shared connection (isSharedToAll) is visible in every project of the account; a second declaration from proj2
// fails ("can only be updated by the workspace that created it", observed 2026-10-02).
resource appiConnections 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = [for (p, i) in ['proj1']: {
  parent: projects[i]
  name: 'appinsights'
  properties: {
    category: 'AppInsights'
    target: appi.id
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: { key: appi.properties.ConnectionString }
    metadata: { ApiType: 'Azure', ResourceId: appi.id }
  }
}]

// ---------------- MCP test tool ----------------
resource mcpIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-hapq-a-mcp-${suffix}'
  location: location
  tags: tags
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
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

// Workload-profiles environment with the Consumption profile only, no private endpoint:
// no environment or plan-management fee per Learn (billed per app usage, monthly free grant applies).
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
      // scale to zero; a single replica bounds cost and keeps the in-memory call record consistent
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
  dependsOn: [acrPull]
}

// ---------------- Budget (alerts only, never a cap) ----------------
resource budget 'Microsoft.Consumption/budgets@2023-11-01' = if (!empty(budgetContactEmails)) {
  name: 'budget-hapq-a-${suffix}'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual50: { enabled: true, operator: 'GreaterThan', threshold: 50, thresholdType: 'Actual', contactEmails: budgetContactEmails }
      actual100: { enabled: true, operator: 'GreaterThan', threshold: 100, thresholdType: 'Actual', contactEmails: budgetContactEmails }
      forecast100: { enabled: true, operator: 'GreaterThan', threshold: 100, thresholdType: 'Forecasted', contactEmails: budgetContactEmails }
    }
  }
}

output accountName string = account.name
output accountId string = account.id
output accountLocation string = account.location
output projectNames array = ['proj1', 'proj2']
output projectEndpoints array = [for p in ['proj1', 'proj2']: 'https://${accountName}.services.ai.azure.com/api/projects/${p}']
output projectPrincipalIds array = [for (p, i) in ['proj1', 'proj2']: projects[i].identity.principalId]
output appInsightsAppId string = appi.properties.AppId
output appInsightsId string = appi.id
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output mcpIdentityPrincipalId string = mcpIdentity.properties.principalId
output mcpUrl string = deployMcpApp ? 'https://${mcpAppName}.${acaEnv.properties.defaultDomain}' : ''
