// Phase B — PRIVATE test environment (customer target configuration).
// Pattern: BYO virtual network with platform-managed data resources (Learn: networking options, template
// "11-private-network-basic-vnet"): Foundry account with network injection set AT CREATION, public network access
// disabled, private endpoint + private DNS. No Cosmos DB / AI Search are needed for hosted agents in this pattern.
// Deployed by `hapq lab private-deploy` (what-if guard + ledger). Two passes: deployMcpApp=false, then true after the
// MCP image has been pushed to the private ACR from the in-VNet runner.
//
// Telemetry, two separate controls (both private by default):
//   ingestion    publicNetworkAccessForIngestion=Disabled on App Insights + Log Analytics, data through the AMPLS
//                private endpoint (Log Analytics ingestion ignores AMPLS access modes: the resource flag enforces it)
//   consultation publicNetworkAccessForQuery=Disabled + AMPLS queryAccessMode=PrivateOnly
// Egress: subnets have defaultOutboundAccess=false; a NAT gateway serves the agent, runner and ACA subnets
// (source-code deployments need mcr.microsoft.com and Entra login; the runner installs packages).
// Role assignments (strictly necessary, all scoped to single resources):
//   runner VM identity: Foundry User (account), AcrPush (ACR), Storage Blob Data Contributor (storage),
//                       Monitoring Reader (App Insights) -- it drives the in-network tests
//   MCP tool identity:  AcrPull (ACR)
//   project identities: AcrPull (ACR) -- hosted agent from a private registry image

targetScope = 'resourceGroup'

param location string
@maxLength(8)
param suffix string

param vnetAddressPrefix string = '10.42.0.0/16'
param agentSubnetPrefix string = '10.42.0.0/24'
param privateEndpointSubnetPrefix string = '10.42.1.0/24'
param runnerSubnetPrefix string = '10.42.2.0/27'
param acaSubnetPrefix string = '10.42.4.0/23'

@allowed(['PrivateOnly', 'Open'])
param telemetryQueryAccess string = 'PrivateOnly'

param modelName string = 'gpt-5-mini'
param modelVersion string = '2025-08-07'
param modelSku string = 'DataZoneStandard'
@maxValue(10)
param modelCapacity int = 10

@description('Runner VM size. Standard_B2s was capacity-restricted in westeurope on 2026-10-02; Dasv6 needs NVMe.')
param runnerVmSize string = 'Standard_D2as_v6'

@description('SSH public key required by the VM API. The private key is not kept: the runner is driven by run-command only.')
param runnerSshPublicKey string

param deployMcpApp bool = false
param mcpImage string = ''
param tenantId string = tenant().tenantId
param mcpAudiences string = ''

var tags = { kit: 'hapq', phase: 'b', purpose: 'hosted-agent-prereq-private' }
var accountName = 'hapq-b-${suffix}'
var foundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
var acrPush = '8311e382-0749-4cb8-b61a-304f252e45ec'
var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var blobContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var monitoringReader = '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
var mcpAppName = 'ca-hapq-b-mcp-${suffix}'

// ---------------- network ----------------
resource natIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'pip-hapq-b-nat-${suffix}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

resource nat 'Microsoft.Network/natGateways@2024-05-01' = {
  name: 'nat-hapq-b-${suffix}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIpAddresses: [{ id: natIp.id }], idleTimeoutInMinutes: 4 }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-hapq-b-${suffix}'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [vnetAddressPrefix] }
    subnets: [
      {
        name: 'snet-agent'
        properties: {
          addressPrefix: agentSubnetPrefix
          defaultOutboundAccess: false
          natGateway: { id: nat.id }
          delegations: [{ name: 'aca', properties: { serviceName: 'Microsoft.App/environments' } }]
        }
      }
      { name: 'snet-pe', properties: { addressPrefix: privateEndpointSubnetPrefix, defaultOutboundAccess: false } }
      { name: 'snet-runner', properties: { addressPrefix: runnerSubnetPrefix, defaultOutboundAccess: false, natGateway: { id: nat.id } } }
      {
        name: 'snet-aca'
        properties: {
          addressPrefix: acaSubnetPrefix
          defaultOutboundAccess: false
          natGateway: { id: nat.id }
          delegations: [{ name: 'aca', properties: { serviceName: 'Microsoft.App/environments' } }]
        }
      }
    ]
  }
}

var agentSubnetId = '${vnet.id}/subnets/snet-agent'
var peSubnetId = '${vnet.id}/subnets/snet-pe'
var runnerSubnetId = '${vnet.id}/subnets/snet-runner'
var acaSubnetId = '${vnet.id}/subnets/snet-aca'

var zones = [
  'privatelink.cognitiveservices.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.services.ai.azure.com'
  'privatelink.blob.${environment().suffixes.storage}'
  'privatelink.azurecr.io'
  'privatelink.monitor.azure.com'
  'privatelink.oms.opinsights.azure.com'
  'privatelink.ods.opinsights.azure.com'
  'privatelink.agentsvc.azure-automation.net'
]

resource dns 'Microsoft.Network/privateDnsZones@2024-06-01' = [for z in zones: {
  name: z
  location: 'global'
  tags: tags
}]

resource dnsLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (z, i) in zones: {
  parent: dns[i]
  name: 'link-hapq-b'
  location: 'global'
  tags: tags
  properties: { virtualNetwork: { id: vnet.id }, registrationEnabled: false }
}]

// ---------------- observability (private ingestion and consultation) ----------------
var queryPublic = telemetryQueryAccess == 'Open' ? 'Enabled' : 'Disabled'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-hapq-b-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 }
    publicNetworkAccessForIngestion: 'Disabled'
    publicNetworkAccessForQuery: queryPublic
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-hapq-b-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    publicNetworkAccessForIngestion: 'Disabled'
    publicNetworkAccessForQuery: queryPublic
  }
}

resource ampls 'Microsoft.Insights/privateLinkScopes@2021-07-01-preview' = {
  name: 'ampls-hapq-b-${suffix}'
  location: 'global'
  tags: tags
  properties: { accessModeSettings: { ingestionAccessMode: 'PrivateOnly', queryAccessMode: telemetryQueryAccess } }
}

resource amplsAppi 'Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview' = {
  parent: ampls
  name: 'appi'
  properties: { linkedResourceId: appi.id }
}

resource amplsLogs 'Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview' = {
  parent: ampls
  name: 'logs'
  properties: { linkedResourceId: logs.id }
}

// ---------------- Foundry account (network injection at creation, public access disabled) ----------------
resource account 'Microsoft.CognitiveServices/accounts@2026-05-01' = {
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
    publicNetworkAccess: 'Disabled'
    networkAcls: { defaultAction: 'Deny' }
    networkInjections: [
      { scenario: 'agent', subnetArmId: agentSubnetId, useMicrosoftManagedNetwork: false }
    ]
  }
}

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: modelName
  sku: { name: modelSku, capacity: modelCapacity }
  properties: { model: { format: 'OpenAI', name: modelName, version: modelVersion } }
}

resource projects 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = [for p in ['proj1', 'proj2']: {
  parent: account
  name: p
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'hapq private ${p}', description: 'Private test environment (synthetic data only)' }
  dependsOn: [deployment]
}]

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

// ---------------- private targets ----------------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'sthapqb${suffix}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    publicNetworkAccess: 'Disabled'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    networkAcls: { defaultAction: 'Deny', bypass: 'None' }
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'crhapqb${suffix}'
  location: location
  tags: tags
  sku: { name: 'Premium' } // Premium is the minimum SKU that supports private endpoints
  properties: { adminUserEnabled: false, publicNetworkAccess: 'Disabled', networkRuleBypassOptions: 'None' }
}

var endpoints = [
  { name: 'account', id: account.id, group: 'account', zoneIdx: [0, 1, 2] }
  { name: 'blob', id: storage.id, group: 'blob', zoneIdx: [3] }
  { name: 'acr', id: acr.id, group: 'registry', zoneIdx: [4] }
  { name: 'ampls', id: ampls.id, group: 'azuremonitor', zoneIdx: [5, 6, 7, 8, 3] }
]

resource pes 'Microsoft.Network/privateEndpoints@2024-05-01' = [for e in endpoints: {
  name: 'pe-hapq-b-${e.name}-${suffix}'
  location: location
  tags: tags
  properties: {
    subnet: { id: peSubnetId }
    privateLinkServiceConnections: [{ name: e.name, properties: { privateLinkServiceId: e.id, groupIds: [e.group] } }]
  }
  dependsOn: [amplsAppi, amplsLogs]
}]

resource peZones 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = [for (e, i) in endpoints: {
  parent: pes[i]
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [for zi in e.zoneIdx: { name: replace(zones[zi], '.', '-'), properties: { privateDnsZoneId: dns[zi].id } }]
  }
  dependsOn: [dnsLinks]
}]

// ---------------- in-VNet execution point (no public IP, no SSH use) ----------------
var cloudInit = '''#cloud-config
package_update: true
packages: [docker.io, python3-venv, python3-pip, ca-certificates, dnsutils]
runcmd:
  - python3 -m venv /opt/hapq/venv
  - /opt/hapq/venv/bin/pip install --quiet "azure-identity==1.26.0" "azure-ai-projects==2.7.0" "httpx==0.28.1" "azure-storage-blob>=12.20"
  - systemctl enable --now docker
  - touch /opt/hapq/ready
'''

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: 'nic-hapq-b-runner-${suffix}'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [{ name: 'ipconfig1', properties: { subnet: { id: runnerSubnetId }, privateIPAllocationMethod: 'Dynamic' } }]
  }
}

resource runner 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: 'vm-hapq-b-runner-${suffix}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: runnerVmSize }
    osProfile: {
      computerName: 'hapq-runner'
      adminUsername: 'hapqadmin'
      customData: base64(cloudInit)
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: { publicKeys: [{ path: '/home/hapqadmin/.ssh/authorized_keys', keyData: runnerSshPublicKey }] }
      }
    }
    storageProfile: {
      imageReference: { publisher: 'Canonical', offer: 'ubuntu-24_04-lts', sku: 'server', version: 'latest' }
      osDisk: { createOption: 'FromImage', managedDisk: { storageAccountType: 'Standard_LRS' }, deleteOption: 'Delete' }
      diskControllerType: 'NVMe'
    }
    networkProfile: { networkInterfaces: [{ id: nic.id, properties: { deleteOption: 'Delete' } }] }
  }
}

// ---------------- MCP tool (internal Container Apps environment, no private endpoint) ----------------
resource mcpIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-hapq-b-mcp-${suffix}'
  location: location
  tags: tags
}

resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-hapq-b-${suffix}'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: { internal: true, infrastructureSubnetId: acaSubnetId }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}

module acaDns 'aca-dns.bicep' = {
  name: 'hapq-b-aca-dns'
  params: { defaultDomain: acaEnv.properties.defaultDomain, staticIp: acaEnv.properties.staticIp, vnetId: vnet.id, tags: tags }
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
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false } // environment is internal: reachable from the VNet only
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
      // minReplicas 1: aligned on foundry-samples 19 (TESTING-GUIDE 4.3, --min-replicas 1); reference alignment, not a fix
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [roleMcpAcrPull, acaDns]
}

// ---------------- role assignments (strictly necessary) ----------------
resource roleRunnerFoundry 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, runner.id, foundryUser)
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUser), principalId: runner.identity.principalId, principalType: 'ServicePrincipal' }
}

resource roleRunnerAcrPush 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, runner.id, acrPush)
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPush), principalId: runner.identity.principalId, principalType: 'ServicePrincipal' }
}

resource roleRunnerBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, runner.id, blobContributor)
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributor), principalId: runner.identity.principalId, principalType: 'ServicePrincipal' }
}

resource roleRunnerMonitoring 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: appi
  name: guid(appi.id, runner.id, monitoringReader)
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringReader), principalId: runner.identity.principalId, principalType: 'ServicePrincipal' }
}

resource roleMcpAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, mcpIdentity.id, acrPull)
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull), principalId: mcpIdentity.properties.principalId, principalType: 'ServicePrincipal' }
}

resource roleProjectAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (p, i) in ['proj1', 'proj2']: {
  scope: acr
  name: guid(acr.id, projects[i].id, acrPull)
  properties: { roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull), principalId: projects[i].identity.principalId, principalType: 'ServicePrincipal' }
}]

output accountName string = account.name
output accountId string = account.id
output accountLocation string = account.location
output projectNames array = ['proj1', 'proj2']
output projectEndpoints array = [for p in ['proj1', 'proj2']: 'https://${accountName}.services.ai.azure.com/api/projects/${p}']
output projectPrincipalIds array = [for (p, i) in ['proj1', 'proj2']: projects[i].identity.principalId]
output appInsightsAppId string = appi.properties.AppId
output appInsightsId string = appi.id
output storageName string = storage.name
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output runnerVmName string = runner.name
output mcpUrl string = deployMcpApp ? 'https://${mcpAppName}.${acaEnv.properties.defaultDomain}' : ''
output agentSubnetPrefix string = agentSubnetPrefix
output telemetryQueryAccess string = telemetryQueryAccess
