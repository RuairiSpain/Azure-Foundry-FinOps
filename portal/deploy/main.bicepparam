using './main.bicep'

// Copy this file, fill in the values, and deploy with:
//   az deployment group create -g <portal-resource-group> -f main.bicep -p main.bicepparam -p portalClientSecret=<secret>

param namePrefix = 'ffportal'
param appServiceSkuName = 'P0v3'
param portalClientId = '00000000-0000-0000-0000-000000000000'
param portalClientSecret = 'set-on-the-command-line'
param foundryResourceGroup = 'rg-ai'
param foundryAccount = 'contoso-foundry'
param foundryLocation = 'eastus2'
param bootstrapAdmins = [
  '00000000-0000-0000-0000-000000000000' // object ID of the first administrator
]
param brandName = 'Contoso AI FinOps'
param tags = {
  owner: 'platform-team'
}
