// A small Ubuntu VM for an SSH demo VPS. Not part of `azd up`.
// Run scripts/azure-vm.sh (or `az deployment group create` against this file).
// Password login is disabled. The NSG allows only SSH, HTTP, and the API port.
targetScope = 'resourceGroup'

@description('Azure region for the VM. Standard_B2s must be offered here.')
param location string = resourceGroup().location

@description('Linux account. Password authentication is disabled.')
@minLength(1)
@maxLength(32)
param adminUsername string = 'azureuser'

@description('SSH public key text (ssh-ed25519 or ssh-rsa). Not a private key.')
@secure()
@minLength(20)
param sshPublicKey string

@description('Who may open SSH. Use your IP as x.x.x.x/32 to lock it down. Web and API stay open.')
param sshSourceAddressPrefix string = '*'

@description('Git repository cloud-init clones.')
param repository string = 'https://github.com/VictorXypCh/GameLens.git'

@description('Branch or tag to clone. Use main once this compose file is on main.')
param gitRef string = 'main'

@description('Optional DNS label. Empty uses a unique gl… label.')
param dnsLabelPrefix string = ''

var token = toLower(uniqueString(subscription().id, resourceGroup().id, 'gamelens-vm'))
var dnsLabel = dnsLabelPrefix != '' ? toLower(dnsLabelPrefix) : 'gl${token}'
var tags = { project: 'matchday-access', role: 'demo-vm' }
var cloudInit = replace(replace(replace(loadTextContent('vm-cloud-init.yaml'), '__ADMIN__', adminUsername), '__REPO__', repository), '__REF__', gitRef)

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: 'nsg-${token}'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowSSH'
        properties: {
          protocol: 'Tcp'
          direction: 'Inbound'
          access: 'Allow'
          priority: 1000
          sourceAddressPrefix: sshSourceAddressPrefix
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '22'
        }
      }
      {
        name: 'AllowWeb'
        properties: {
          protocol: 'Tcp'
          direction: 'Inbound'
          access: 'Allow'
          priority: 1010
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '80'
        }
      }
      {
        name: 'AllowApi'
        properties: {
          protocol: 'Tcp'
          direction: 'Inbound'
          access: 'Allow'
          priority: 1020
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '8000'
        }
      }
    ]
  }
}

resource pip 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: 'pip-${token}'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: { domainNameLabel: dnsLabel }
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-01-01' = {
  name: 'nic-${token}'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: { id: pip.id }
          subnet: { id: vnet.properties.subnets[0].id }
        }
      }
    ]
    networkSecurityGroup: { id: nsg.id }
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: 'vnet-${token}'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [ '10.20.0.0/16' ] }
    subnets: [
      {
        name: 'default'
        properties: { addressPrefix: '10.20.0.0/24' }
      }
    ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-03-01' = {
  name: 'vm-${token}'
  location: location
  tags: tags
  properties: {
    hardwareProfile: { vmSize: 'Standard_B2s' }
    osProfile: {
      computerName: 'gl${token}'
      adminUsername: adminUsername
      customData: base64(cloudInit)
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: '0001-com-ubuntu-server-noble'
        sku: '24_04-lts-gen2'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        diskSizeGB: 32
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
        deleteOption: 'Delete'
      }
    }
    networkProfile: {
      networkInterfaces: [
        { id: nic.id }
      ]
    }
  }
}

output vmName string = vm.name
output publicIp string = pip.properties.ipAddress
output fqdn string = '${dnsLabel}.${location}.cloudapp.azure.com'
output sshCommand string = 'ssh ${adminUsername}@${dnsLabel}.${location}.cloudapp.azure.com'
output webUrl string = 'http://${dnsLabel}.${location}.cloudapp.azure.com'
output apiUrl string = 'http://${dnsLabel}.${location}.cloudapp.azure.com:8000'
