// A test setup for examples/apim/llm-caller-attribution.policy.xml. PROTOTYPE: written to compile, not yet deployed.
//
// Creates a Log Analytics workspace, a workspace-based Application Insights, an API Management instance with a gateway
// identity, the policy on one chat-completions API that forwards to your model endpoint, and the diagnostic settings
// that send gateway logs (including the LLM log) to the workspace.
//
// Cost and time: API Management is billed while it exists, and a new instance can take many minutes to appear. Delete
// the resource group when you have finished (see deploy.sh teardown).
targetScope = 'resourceGroup'

@description('Name of the API Management instance. It becomes part of the gateway URL, so it must be globally unique.')
param apimName string

param location string = resourceGroup().location

@description('API Management tier for the test. Developer is slowest to create; the v2 tiers are quicker. Check the policy is supported on your tier.')
@allowed([
  'Developer'
  'BasicV2'
  'StandardV2'
])
param sku string = 'BasicV2'

@description('Required by API Management.')
param publisherEmail string

param publisherName string = 'Foundry FinOps test'

@description('Entra tenant that issues the callers\' tokens.')
param tenantId string = tenant().tenantId

@description('Audience of the callers\' tokens: the application ID URI or client ID of the gateway API registration.')
param apiAudience string

@description('Base URL of the model endpoint the gateway forwards to, ending in /openai, for example https://myaccount.openai.azure.com/openai')
param backendUrl string

@description('Name of the Foundry or Azure OpenAI account IN THIS RESOURCE GROUP to grant the gateway access to. Leave empty to grant the role yourself.')
param foundryAccountName string = ''

param namePrefix string = 'finops-test'

var cognitiveServicesOpenAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-law'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${namePrefix}-appi'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
  }
}

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: apimName
  location: location
  sku: {
    name: sku
    capacity: 1
  }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
  }
}

resource logger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'appinsights'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appInsights.id
    credentials: {
      instrumentationKey: appInsights.properties.InstrumentationKey
    }
    isBuffered: true
  }
}

// Verbosity must be 'information' or lower, or the policy's <trace severity="information"> record is not written.
resource serviceDiagnostic 'Microsoft.ApiManagement/service/diagnostics@2024-05-01' = {
  parent: apim
  name: 'applicationinsights'
  properties: {
    loggerId: logger.id
    alwaysLog: 'allErrors'
    sampling: {
      samplingType: 'fixed'
      percentage: 100
    }
    metrics: true
    verbosity: 'information'
  }
}

// Resource-specific tables (ApiManagementGatewayLlmLog) need the 'Dedicated' destination type.
// UNVERIFIED: the category name GatewayLlmLogs. Check it against the categories your instance offers.
resource diagnosticSetting 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-log-analytics'
  scope: apim
  properties: {
    workspaceId: workspace.id
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      {
        category: 'GatewayLogs'
        enabled: true
      }
      {
        category: 'GatewayLlmLogs'
        enabled: true
      }
    ]
  }
}

resource tenantIdValue 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'tenant-id'
  properties: {
    displayName: 'tenant-id'
    value: tenantId
  }
}

resource audienceValue 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'api-audience'
  properties: {
    displayName: 'api-audience'
    value: apiAudience
  }
}

// Subscription key stays required, so the gateway is never an open proxy to the model: a call with no bearer token
// still needs a key and is recorded as unknown.
resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'finops-llm'
  properties: {
    displayName: 'FinOps test: chat completions'
    path: 'openai'
    protocols: [
      'https'
    ]
    serviceUrl: backendUrl
    subscriptionRequired: true
    apiType: 'http'
  }
}

resource chatCompletions 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = {
  parent: api
  name: 'chat-completions'
  properties: {
    displayName: 'Chat completions'
    method: 'POST'
    urlTemplate: '/deployments/{deployment}/chat/completions'
    templateParameters: [
      {
        name: 'deployment'
        required: true
        type: 'string'
      }
    ]
  }
}

resource policy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('../llm-caller-attribution.policy.xml')
  }
  dependsOn: [
    tenantIdValue
    audienceValue
    serviceDiagnostic
  ]
}

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = if (!empty(foundryAccountName)) {
  name: foundryAccountName
}

resource gatewayCanCallModel 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(foundryAccountName)) {
  scope: foundryAccount
  name: guid(foundryAccount.id, apim.id, cognitiveServicesOpenAiUser)
  properties: {
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAiUser)
  }
}

output gatewayUrl string = apim.properties.gatewayUrl
output workspaceId string = workspace.id
output workspaceCustomerId string = workspace.properties.customerId
output gatewayPrincipalId string = apim.identity.principalId
