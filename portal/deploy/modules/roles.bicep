// Role assignments for the portal's managed identity on the resource group that holds the Foundry resource.
targetScope = 'resourceGroup'

@description('Object ID of the portal web app\'s managed identity.')
param principalId string

@description('Built-in or custom role definition GUIDs to assign at this resource group.')
param roleDefinitionIds array

resource assignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for roleId in roleDefinitionIds: {
  name: guid(resourceGroup().id, principalId, roleId)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleId)
  }
}]
