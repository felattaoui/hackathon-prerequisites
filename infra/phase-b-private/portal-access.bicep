// Environment B: private browser access for the manual portal check (MAN-C02-PORTAL-CHAT-INVNET).
// Azure Bastion (Basic) in a dedicated AzureBastionSubnet + a Windows Server VM (Edge) without public IP in the runner
// subnet. The Bastion public IP is the Bastion service front end only: no Foundry/ACR/Storage endpoint is reopened.
// Temporary: removed by the kit cleanup at the end of the qualification.

targetScope = 'resourceGroup'

param location string
@maxLength(8)
param suffix string
param vnetName string
param runnerSubnetName string = 'snet-runner'
param bastionPrefix string = '10.42.6.0/26'
param vmSize string = 'Standard_D2as_v6'
param adminUsername string = 'hapqadmin'
@secure()
param adminPassword string

var tags = { kit: 'hapq', phase: 'b', purpose: 'hosted-agent-prereq-lab', temporary: 'portal-check' }

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' existing = {
  name: vnetName
}

resource bastionSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-11-01' = {
  parent: vnet
  name: 'AzureBastionSubnet'
  properties: { addressPrefix: bastionPrefix }
}

resource bastionIp 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: 'pip-bas-hapq-b-${suffix}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

resource bastion 'Microsoft.Network/bastionHosts@2023-11-01' = {
  name: 'bas-hapq-b-${suffix}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    ipConfigurations: [{
      name: 'ipconfig'
      properties: { subnet: { id: bastionSubnet.id }, publicIPAddress: { id: bastionIp.id } }
    }]
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2023-11-01' = {
  name: 'nic-vm-hapq-b-web-${suffix}'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [{
      name: 'ipconfig'
      properties: {
        subnet: { id: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, runnerSubnetName) }
        privateIPAllocationMethod: 'Dynamic'
      }
    }]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-03-01' = {
  name: 'vm-hapq-b-web-${suffix}'
  location: location
  tags: tags
  properties: {
    hardwareProfile: { vmSize: vmSize }
    osProfile: {
      computerName: 'hapqweb'
      adminUsername: adminUsername
      adminPassword: adminPassword
      windowsConfiguration: { enableAutomaticUpdates: true, provisionVMAgent: true }
    }
    storageProfile: {
      imageReference: { publisher: 'MicrosoftWindowsServer', offer: 'WindowsServer', sku: '2022-datacenter-azure-edition', version: 'latest' }
      osDisk: { createOption: 'FromImage', managedDisk: { storageAccountType: 'StandardSSD_LRS' }, deleteOption: 'Delete' }
      diskControllerType: 'NVMe'
    }
    networkProfile: { networkInterfaces: [{ id: nic.id, properties: { deleteOption: 'Delete' } }] }
    securityProfile: { securityType: 'TrustedLaunch', uefiSettings: { secureBootEnabled: true, vTpmEnabled: true } }
  }
}

output bastionName string = bastion.name
output vmName string = vm.name
