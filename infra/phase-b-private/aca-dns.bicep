// Private DNS for the internal Container Apps environment (name known only after the environment exists).
param defaultDomain string
param staticIp string
param vnetId string
param tags object

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: defaultDomain
  location: 'global'
  tags: tags
}

resource wildcard 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
  parent: zone
  name: '*'
  properties: { ttl: 300, aRecords: [{ ipv4Address: staticIp }] }
}

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: 'link-hapq-b'
  location: 'global'
  tags: tags
  properties: { virtualNetwork: { id: vnetId }, registrationEnabled: false }
}
