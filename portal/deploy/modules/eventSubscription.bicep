// Sends Foundry cost control alerts from the Event Grid topic to the portal. Deploy this AFTER the portal code is
// running: Event Grid validates the endpoint (a CloudEvents OPTIONS handshake) when the subscription is created.
param topicName string
@description('Full endpoint URL including the ?secret= query string.')
@secure()
param endpointUrl string

resource topic 'Microsoft.EventGrid/topics@2022-06-15' existing = {
  name: topicName
}

resource subscription 'Microsoft.EventGrid/topics/eventSubscriptions@2022-06-15' = {
  parent: topic
  name: 'portal'
  properties: {
    eventDeliverySchema: 'CloudEventSchemaV1_0'
    destination: {
      endpointType: 'WebHook'
      properties: {
        endpointUrl: endpointUrl
      }
    }
    filter: {
      includedEventTypes: [ 'costControl.alert' ]
    }
    retryPolicy: {
      maxDeliveryAttempts: 10
      eventTimeToLiveInMinutes: 1440
    }
  }
}
