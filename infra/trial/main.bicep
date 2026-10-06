// First Azure trial (approved scope): one Foundry account, ONE project, the gpt-5-mini DataZoneStandard deployment,
// Log Analytics + Application Insights and the project's AppInsights connection. Nothing else: no MCP tool, no
// Container Apps, no ACR, no second project, no role assignment, no Entra object.
// Names match infra/phase-a-public so a later Phase A deployment reuses these kit-owned resources.
// Deployed only by `hapq lab trial --execute --confirm <rg>` (what-if guard + ledger).

targetScope = 'resourceGroup'

@description('Region of every resource. No default: must be chosen explicitly.')
param location string
@maxLength(8)
param suffix string
param modelName string = 'gpt-5-mini'
param modelVersion string = '2025-08-07'
@allowed(['DataZoneStandard'])
param modelSku string = 'DataZoneStandard'
@minValue(1)
@maxValue(10)
param modelCapacity int = 10

var tags = { kit: 'hapq', phase: 'a', purpose: 'hosted-agent-prereq-lab' }
var accountName = 'hapq-a-${suffix}'

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

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: 'proj1'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'hapq proj1', description: 'Hosted-agent prerequisite lab (synthetic data only)' }
  dependsOn: [deployment]
}

resource appiConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'appinsights'
  properties: {
    category: 'AppInsights'
    target: appi.id
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: { key: appi.properties.ConnectionString }
    metadata: { ApiType: 'Azure', ResourceId: appi.id }
  }
}

output accountName string = account.name
output accountId string = account.id
output accountLocation string = account.location
output projectName string = project.name
output projectEndpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/proj1'
output projectPrincipalId string = project.identity.principalId
output appInsightsAppId string = appi.properties.AppId
output appInsightsId string = appi.id
