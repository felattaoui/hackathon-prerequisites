// Environment B: one additional project (proj3) in STANDARD agent setup on the existing network-injected account.
// Mirrors foundry-samples 15-private-network-standard-agent-setup (commit d807d9a) for one project:
//   BYO Cosmos DB + AI Search + Storage (public access disabled, private endpoints, private DNS zones),
//   project connections (AAD), pre-caphost roles, project capability host, post-caphost roles.
// Not mirrored on purpose: the account capability host (auto-created by the platform for network-injected accounts;
// a second one returns 409, see the template's createAccountCapabilityHost note).
// Deviation: AI Search SKU 'basic' (template: 'standard'); basic supports private endpoints and Entra auth.
// Existing projects (proj1, proj2) are untouched. Deployed only through the kit (tracked, ledgered).
// Regional note: capability settings (2026-07-15-preview) are only in UK South / Canada Central, hence the
// project capability host path used by template 15.

targetScope = 'resourceGroup'

param location string
@maxLength(8)
param suffix string
param accountName string
param vnetName string
param peSubnetName string = 'snet-pe'
param projectName string = 'proj3'
@description('Cosmos DB region. Learn (virtual-networks, Limitations): Cosmos DB, AI Search and Storage can be in other regions than the account. West Europe returned ServiceUnavailable (high demand, region access request needed) on 2026-10-02.')
param cosmosLocation string = location

var tags = { kit: 'hapq', phase: 'b', purpose: 'hosted-agent-prereq-lab', setup: 'standard' }
var cosmosName = cosmosLocation == location ? 'cos-hapq-b-${suffix}' : (cosmosLocation == 'northeurope' ? 'cos-hapq-b-ne-${suffix}' : 'cos-hapq-b-${cosmosLocation}-${suffix}')
var searchName = 'srch-hapq-b-${suffix}'
var storageName = 'sthapqb3${suffix}'
var peSubnetId = resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, peSubnetName)
var vnetId = resourceId('Microsoft.Network/virtualNetworks', vnetName)

// ---------------- BYO dependent resources (as standard-dependent-resources.bicep) ----------------
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: cosmosName
  location: cosmosLocation
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    disableLocalAuth: true
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    publicNetworkAccess: 'Disabled'
    enableFreeTier: false
    locations: [{ locationName: cosmosLocation, failoverPriority: 0, isZoneRedundant: false }]
    databaseAccountOfferType: 'Standard'
  }
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: searchName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  sku: { name: 'basic' }
  properties: {
    disableLocalAuth: false
    authOptions: { aadOrApiKey: { aadAuthFailureMode: 'http401WithBearerChallenge' } }
    hostingMode: 'default'
    partitionCount: 1
    replicaCount: 1
    publicNetworkAccess: 'disabled'
    semanticSearch: 'disabled'
    networkRuleSet: { bypass: 'None', ipRules: [] }
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_ZRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Disabled'
    networkAcls: { bypass: 'AzureServices', defaultAction: 'Deny', virtualNetworkRules: [] }
  }
}

// ---------------- Private endpoints + DNS ----------------
resource zoneCosmos 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.documents.azure.com'
  location: 'global'
  tags: tags
}
resource zoneSearch 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.search.windows.net'
  location: 'global'
  tags: tags
}
resource zoneBlob 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: 'privatelink.blob.core.windows.net'
}
resource linkCosmos 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: zoneCosmos
  name: 'link-hapq-b'
  location: 'global'
  properties: { virtualNetwork: { id: vnetId }, registrationEnabled: false }
}
resource linkSearch 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: zoneSearch
  name: 'link-hapq-b'
  location: 'global'
  properties: { virtualNetwork: { id: vnetId }, registrationEnabled: false }
}

var pes = [
  { name: 'pe-${cosmosName}', target: cosmos.id, group: 'Sql', zone: zoneCosmos.id }
  { name: 'pe-${searchName}', target: search.id, group: 'searchService', zone: zoneSearch.id }
  { name: 'pe-${storageName}-blob', target: storage.id, group: 'blob', zone: zoneBlob.id }
]
resource pe 'Microsoft.Network/privateEndpoints@2023-11-01' = [for p in pes: {
  name: p.name
  location: location
  tags: tags
  properties: {
    subnet: { id: peSubnetId }
    privateLinkServiceConnections: [{ name: p.name, properties: { privateLinkServiceId: p.target, groupIds: [p.group] } }]
  }
}]
resource peDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = [for (p, i) in pes: {
  parent: pe[i]
  name: 'default'
  properties: { privateDnsZoneConfigs: [{ name: 'cfg', properties: { privateDnsZoneId: p.zone } }] }
}]

// ---------------- Project + connections (as ai-project-identity.bicep) ----------------
resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: accountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: { displayName: 'hapq ${projectName} (standard)', description: 'Standard agent setup, synthetic data only' }

  resource connCosmos 'connections@2025-04-01-preview' = {
    name: cosmosName
    properties: {
      category: 'CosmosDB'
      target: cosmos.properties.documentEndpoint
      authType: 'AAD'
      metadata: { ApiType: 'Azure', ResourceId: cosmos.id, location: cosmos.location }
    }
  }
  resource connStorage 'connections@2025-04-01-preview' = {
    name: storageName
    properties: {
      category: 'AzureStorageAccount'
      target: storage.properties.primaryEndpoints.blob
      authType: 'AAD'
      metadata: { ApiType: 'Azure', ResourceId: storage.id, location: storage.location }
    }
  }
  resource connSearch 'connections@2025-04-01-preview' = {
    name: searchName
    properties: {
      category: 'CognitiveSearch'
      target: 'https://${searchName}.search.windows.net'
      authType: 'AAD'
      metadata: { ApiType: 'Azure', ResourceId: search.id, location: search.location }
    }
  }
}

// ---------------- Roles before the capability host ----------------
var roleBlobContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var roleCosmosOperator = '230815da-be43-4aae-9cb4-875f7bd000aa'
var roleSearchIndexContributor = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var roleSearchServiceContributor = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
var roleBlobOwner = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'

resource raBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(project.id, roleBlobContributor, storage.id)
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleBlobContributor) }
  dependsOn: [peDns]
}
resource raCosmos 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: cosmos
  name: guid(project.id, roleCosmosOperator, cosmos.id)
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCosmosOperator) }
  dependsOn: [peDns]
}
resource raSearchIndex 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(project.id, roleSearchIndexContributor, search.id)
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleSearchIndexContributor) }
  dependsOn: [peDns]
}
resource raSearchService 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(project.id, roleSearchServiceContributor, search.id)
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleSearchServiceContributor) }
  dependsOn: [peDns]
}

// ---------------- Project capability host (as add-project-capability-host.bicep) ----------------
resource projectCapHost 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = {
  parent: project
  name: 'caphostproj'
  properties: {
    #disable-next-line BCP037
    capabilityHostKind: 'Agents'
    vectorStoreConnections: [searchName]
    storageConnections: [storageName]
    threadStorageConnections: [cosmosName]
  }
  dependsOn: [raBlob, raCosmos, raSearchIndex, raSearchService, linkCosmos, linkSearch]
}

// ---------------- Roles after the capability host ----------------
#disable-next-line BCP053
var wsRaw = project.properties.internalId
var workspaceId = '${substring(wsRaw, 0, 8)}-${substring(wsRaw, 8, 4)}-${substring(wsRaw, 12, 4)}-${substring(wsRaw, 16, 4)}-${substring(wsRaw, 20, 12)}'
var blobCondition = '((!(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/read\'})  AND  !(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/filter/action\'}) AND  !(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/write\'}) ) OR (@Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] StringStartsWithIgnoreCase \'${workspaceId}\' AND @Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] StringLikeIgnoreCase \'*-azureml-agent\'))'

resource raBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, project.id, roleBlobOwner)
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleBlobOwner)
    conditionVersion: '2.0'
    condition: blobCondition }
  dependsOn: [projectCapHost]
}
resource raCosmosData 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2022-05-15' = {
  parent: cosmos
  name: guid(project.id, cosmosName, '00000000-0000-0000-0000-000000000002')
  properties: {
    principalId: project.identity.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    scope: cosmos.id
  }
  dependsOn: [projectCapHost, raBlobOwner]
}

output projectName string = project.name
output projectEndpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${projectName}'
output projectPrincipalId string = project.identity.principalId
output capHost string = projectCapHost.name
