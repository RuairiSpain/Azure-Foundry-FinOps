// Foundry FinOps portal on Azure App Service.
//
// Creates the web app (Linux, one instance: the portal keeps its data in SQLite), a Key Vault for its secrets,
// Application Insights, an Event Grid topic for cost control alerts, and the role assignments its managed identity
// needs on the Foundry resource group. Everything that differs between customers is a parameter.
targetScope = 'resourceGroup'

@description('Prefix for resource names. Letters and numbers, 3 to 10 characters.')
@minLength(3)
@maxLength(10)
param namePrefix string = 'ffportal'

param location string = resourceGroup().location

@description('App Service plan SKU. P0v3 or larger is recommended; B1 works for a trial.')
param appServiceSkuName string = 'P0v3'

@description('Microsoft Entra tenant of your people.')
param tenantId string = tenant().tenantId

@description('Client ID of the portal\'s app registration (see deploy/create-app-registration.sh).')
param portalClientId string

@description('Client secret of the app registration, used for the on-behalf-of exchange. Stored in Key Vault.')
@secure()
param portalClientSecret string

@description('Subscription that holds the Foundry resource.')
param foundrySubscriptionId string = subscription().subscriptionId

@description('Resource group that holds the Foundry resource. The portal\'s identity gets its roles here.')
param foundryResourceGroup string

@description('Name of the Foundry resource the portal manages.')
param foundryAccount string

@description('Azure region of the Foundry resource (used for quota).')
param foundryLocation string = 'eastus2'

@description('API Management instance that fronts Foundry, if any.')
param apimService string = ''

@description('Object IDs of the first portal administrators. They add everybody else in the Admin page.')
param bootstrapAdmins array

@description('Name shown in the portal\'s title bar.')
param brandName string = 'Foundry FinOps'

@description('Block every change made through the portal.')
param readOnly bool = false

@description('Shared secret the Event Grid subscription sends to the portal. Generated if you give none.')
@secure()
param alertWebhookSecret string = newGuid()

@description('Roles the portal\'s identity needs to READ: Reader, Cost Management Reader, Monitoring Reader.')
param readerRoleDefinitionIds array = [
  'acdd72a7-3385-48ef-bd42-f606fba81ae7' // Reader
  '72fafb9e-0641-4937-9268-a91bfd8191a3' // Cost Management Reader
  '43d0d8ad-25c7-4714-9337-8ba259a9fe05' // Monitoring Reader
]

@description('Roles for the changes the portal makes for people: cost controls, budgets, tags. Empty means the portal is read-only in practice. Use the most specific roles you have, for example Foundry Account Owner (look up its GUID with `az role definition list`) plus Tag Contributor (4a9ae827-6dc8-4573-8ac7-8239d42aa03f).')
param writerRoleDefinitionIds array = []

@description('Create the Event Grid topic that Foundry cost controls send alerts to.')
param createEventGridTopic bool = true

@description('Create the Event Grid subscription that delivers alerts to the portal. Turn this on in a second deployment, after the portal code is running.')
param createEventSubscription bool = false

param tags object = {}

var suffix = uniqueString(resourceGroup().id, namePrefix)
var appName = '${namePrefix}-${suffix}'
var vaultName = take('${namePrefix}kv${suffix}', 24)
var topicName = '${namePrefix}-alerts-${suffix}'
var secretsUserRole = '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${namePrefix}-ai-${suffix}'
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  tags: tags
  properties: {
    tenantId: tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource clientSecretValue 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'portal-client-secret'
  properties: { value: portalClientSecret }
}

resource webhookSecretValue 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'alert-webhook-secret'
  properties: { value: alertWebhookSecret }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-plan-${suffix}'
  location: location
  tags: tags
  kind: 'linux'
  sku: { name: appServiceSkuName, capacity: 1 } // one instance: SQLite is a single-writer database
  properties: { reserved: true }
}

resource site 'Microsoft.Web/sites@2023-12-01' = {
  name: appName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: false
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'python -m foundry_finops_portal --host 0.0.0.0'
      alwaysOn: true
      http20Enabled: true
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      healthCheckPath: '/api/health'
      numberOfWorkers: 1
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE', value: 'true' } // keeps /home across restarts: the SQLite file lives there
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
        { name: 'PORTAL_MODE', value: 'entra' }
        { name: 'PORTAL_TENANT_ID', value: tenantId }
        { name: 'PORTAL_CLIENT_ID', value: portalClientId }
        { name: 'PORTAL_CLIENT_SECRET', value: '@Microsoft.KeyVault(SecretUri=${clientSecretValue.properties.secretUri})' }
        { name: 'PORTAL_SUBSCRIPTION_ID', value: foundrySubscriptionId }
        { name: 'PORTAL_RESOURCE_GROUP', value: foundryResourceGroup }
        { name: 'PORTAL_FOUNDRY_ACCOUNT', value: foundryAccount }
        { name: 'PORTAL_LOCATION', value: foundryLocation }
        { name: 'PORTAL_APIM_SERVICE', value: apimService }
        { name: 'PORTAL_STATIC_DIR', value: '/home/site/wwwroot/static' }
        { name: 'PORTAL_DB_PATH', value: '/home/data/portal.db' }
        { name: 'PORTAL_ADMINS', value: join(bootstrapAdmins, ',') }
        { name: 'PORTAL_BRAND_NAME', value: brandName }
        { name: 'PORTAL_READ_ONLY', value: string(readOnly) }
        { name: 'PORTAL_ALERT_WEBHOOK_SECRET', value: '@Microsoft.KeyVault(SecretUri=${webhookSecretValue.properties.secretUri})' }
      ]
    }
  }
}

resource vaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, site.id, secretsUserRole)
  scope: vault
  properties: {
    principalId: site.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsUserRole)
  }
}

module readerRoles 'modules/roles.bicep' = if (!empty(readerRoleDefinitionIds)) {
  name: 'portal-reader-roles'
  scope: resourceGroup(foundrySubscriptionId, foundryResourceGroup)
  params: {
    principalId: site.identity.principalId
    roleDefinitionIds: readerRoleDefinitionIds
  }
}

module writerRoles 'modules/roles.bicep' = if (!empty(writerRoleDefinitionIds)) {
  name: 'portal-writer-roles'
  scope: resourceGroup(foundrySubscriptionId, foundryResourceGroup)
  params: {
    principalId: site.identity.principalId
    roleDefinitionIds: writerRoleDefinitionIds
  }
}

// Foundry cost controls send alerts to an Event Grid topic that uses the CloudEvents v1.0 input schema and allows
// key-based authentication.
resource topic 'Microsoft.EventGrid/topics@2022-06-15' = if (createEventGridTopic) {
  name: topicName
  location: location
  tags: tags
  properties: {
    inputSchema: 'CloudEventSchemaV1_0'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

module alertDelivery 'modules/eventSubscription.bicep' = if (createEventGridTopic && createEventSubscription) {
  name: 'portal-alert-delivery'
  params: {
    topicName: topicName
    endpointUrl: 'https://${site.properties.defaultHostName}/api/events/cost-control-alert?secret=${alertWebhookSecret}'
  }
  dependsOn: [ topic ]
}

output portalUrl string = 'https://${site.properties.defaultHostName}'
output webAppName string = site.name
output managedIdentityPrincipalId string = site.identity.principalId
output eventGridTopicName string = createEventGridTopic ? topicName : ''
output spaRedirectUri string = 'https://${site.properties.defaultHostName}'
