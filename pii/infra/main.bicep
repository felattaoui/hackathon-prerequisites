// PII lab, step 1 (notebooks 00 and 01): one Foundry resource (kind AIServices) used for
// Azure Language PII detection and Document Intelligence Read. Pay-per-use only, no fixed cost.
// Entra ID authentication only (local auth disabled): the signed-in user gets "Cognitive Services User".

@description('Region of every resource (passed explicitly).')
param location string = resourceGroup().location

@description('Object id of the user running the notebooks (data-plane role assignment).')
param principalId string

@description('Globally unique name, also used as the custom subdomain of the endpoint.')
param accountName string = 'piilab${uniqueString(resourceGroup().id)}'

var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
  tags: { purpose: 'pii-lab', 'managed-by': 'hackathon-prerequisites-pii' }
}

resource userRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, cognitiveServicesUserRoleId)
  scope: account
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
  }
}

output accountName string = account.name
output endpoint string = account.properties.endpoint
