// Starter infrastructure for the Microsoft hero stack. Deploy with `azd up`.
// Not yet deployed in a real subscription: run `azd provision --preview` first and adjust
// region, SKUs and the model name/version to what your subscription offers.
//
// The API stays on synthetic data. Tracking can also be published to Event Hubs
// (`match-tracking`) and Web PubSub (hub `live`). The vision service reads video
// from Blob Storage. Its GPU replica is off unless deployGpu=true, and it scales
// to zero replicas so an idle GPU is not billed.
targetScope = 'resourceGroup'

@description('Short name used to build resource names')
param environmentName string
param location string = resourceGroup().location
@description('Static Web Apps region (centralus, eastus2, westus2, westeurope, eastasia); eastus is not offered.')
param webLocation string = 'eastasia'
@description('Foundry / Azure OpenAI model to deploy for explanations')
param modelName string = 'gpt-4.1-mini'
param modelVersion string = '2025-04-14'
@description('Create a second Container Apps environment with a serverless T4 and run the vision app on it. Leave false until the subscription has GPU quota.')
param deployGpu bool = false
@description('Region that offers Consumption-GPU-NC8as-T4 (Sweden Central, West US, Australia East).')
param gpuLocation string = 'swedencentral'
@description('Create serverless Cosmos DB for highlight job records. Leave false to keep JSON files only (the API always writes files).')
param deployCosmos bool = false
@description('Subscribe Event Grid BlobCreated on the highlights container to the API webhook. Enable after the API ingress is up so handshake can succeed.')
param deployHighlightEvents bool = false
@secure()
@description('Token for /admin/video. Empty keeps the admin video page off.')
param adminToken string = ''

var token = toLower(uniqueString(subscription().id, resourceGroup().id, environmentName))
var tags = { 'azd-env-name': environmentName, project: 'matchday-access', data: 'synthetic-default' }
var blobReaderRole = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var blobContributorRole = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var eventHubsSenderRole = '2b629674-e913-4c01-ae53-ef4638d8f975'
var webPubSubOwnerRole = '12cf5a90-567b-43ae-8102-96cf46c7d9b4'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${token}'
  location: location
  tags: tags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${token}'
  location: location
  kind: 'web'
  tags: tags
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}

// Live replay of match events, plus a second hub for tracking frames.
resource ehNamespace 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: 'evh-${token}'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard', capacity: 1 }
}

resource matchEvents 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: ehNamespace
  name: 'match-events'
  properties: { partitionCount: 2, messageRetentionInDays: 1 }
}

resource matchTracking 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: ehNamespace
  name: 'match-tracking'
  properties: { partitionCount: 2, messageRetentionInDays: 1 }
}

// Foundry Tools: Azure OpenAI + Speech in one AI Services account
resource ai 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'ai-${token}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: { customSubDomainName: 'ai-${token}', publicNetworkAccess: 'Enabled' }
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: ai
  name: 'explainer'
  sku: { name: 'GlobalStandard', capacity: 10 }
  properties: { model: { format: 'OpenAI', name: modelName, version: modelVersion } }
}

resource pubsub 'Microsoft.SignalRService/webPubSub@2024-03-01' = {
  name: 'wps-${token}'
  location: location
  tags: tags
  sku: { name: 'Free_F1', capacity: 1 }
}

resource liveHub 'Microsoft.SignalRService/webPubSub/hubs@2024-03-01' = {
  parent: pubsub
  name: 'live'
  properties: {
    // Demo hub: browsers can subscribe without a token. Lock this down before a public deployment.
    anonymousConnectPolicy: 'allow'
    eventHandlers: []
  }
}

resource highlightsHub 'Microsoft.SignalRService/webPubSub/hubs@2024-03-01' = {
  parent: pubsub
  name: 'highlights'
  properties: {
    anonymousConnectPolicy: 'allow'
    eventHandlers: []
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'cr${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: true }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'st${token}'
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowSharedKeyAccess: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource matchVideo 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'match-video'
  properties: { publicAccess: 'None' }
}

resource highlightsBlobs 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'highlights'
  properties: { publicAccess: 'None' }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${token}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey }
    }
  }
}

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-api-${token}'
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: true, targetPort: 8000 }
      registries: [ { server: registry.properties.loginServer, username: registry.listCredentials().username, passwordSecretRef: 'acr' } ]
      secrets: concat([
        { name: 'acr', value: registry.listCredentials().passwords[0].value }
        { name: 'aoai-key', value: ai.listKeys().key1 }
      ], empty(adminToken) ? [] : [ { name: 'admin-token', value: adminToken } ])
    }
    template: {
      containers: [ {
        name: 'api'
        image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest' // azd replaces this on deploy
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: concat([
          { name: 'AZURE_OPENAI_ENDPOINT', value: ai.properties.endpoint }
          { name: 'AZURE_OPENAI_API_KEY', secretRef: 'aoai-key' }
          { name: 'AZURE_OPENAI_DEPLOYMENT', value: model.name }
          { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
          { name: 'MATCH_SOURCE', value: 'proto' }
          { name: 'MATCH_SEED', value: '7' }
          { name: 'EVENTHUB_NAMESPACE', value: '${ehNamespace.name}.servicebus.windows.net' }
          { name: 'EVENTHUB_NAME', value: 'match-events' }
          { name: 'EVENTHUB_TRACKING_NAME', value: 'match-tracking' }
          { name: 'WEBPUBSUB_HOST', value: '${pubsub.name}.webpubsub.azure.com' }
          { name: 'WEBPUBSUB_HUB', value: 'live' }
          // Off by default: each open live pitch would otherwise publish its own copy.
          { name: 'LIVE_PUBLISH', value: '0' }
          { name: 'AZURE_STORAGE_ACCOUNT', value: storage.name }
          { name: 'HIGHLIGHTS_CONTAINER', value: 'highlights' }
          { name: 'HIGHLIGHTS_DIR', value: '/app/data/highlights' }
          { name: 'WEBPUBSUB_HIGHLIGHTS_HUB', value: 'highlights' }
          { name: 'COSMOS_ENDPOINT', value: deployCosmos ? cosmos.properties.documentEndpoint : '' }
          { name: 'COSMOS_DATABASE', value: 'gamelens' }
          { name: 'COSMOS_CONTAINER', value: 'highlights' }
          { name: 'ADMIN_VIDEO', value: empty(adminToken) ? '0' : '1' }
          { name: 'PUBLISHED_DIR', value: '/app/data/published' }
          // Uploads are tracked on the vision app; the API image has no torch.
          { name: 'VISION_URL', value: deployGpu ? 'https://${visionGpu.properties.configuration.ingress.fqdn}' : 'https://${visionCpu.properties.configuration.ingress.fqdn}' }
        ], empty(adminToken) ? [] : [ { name: 'ADMIN_TOKEN', secretRef: 'admin-token' } ])
      } ]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

resource apiEventHubs 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(ehNamespace.id, api.id, eventHubsSenderRole)
  scope: ehNamespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', eventHubsSenderRole)
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource apiPubSub 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(pubsub.id, api.id, webPubSubOwnerRole)
  scope: pubsub
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', webPubSubOwnerRole)
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource apiBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, api.id, blobContributorRole)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributorRole)
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Manual Container Apps Job: `python -m highlights.worker --job-id …`.
// Same placeholder image as the API; after `azd deploy api`, point this job at that image
// (or the vision image when you want YOLO). Trigger is manual, not Event Grid.
resource highlightJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-highlights-${token}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: env.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1800
      replicaRetryLimit: 1
      replicaCompletionCount: 1
      parallelism: 1
      registries: [ { server: registry.properties.loginServer, username: registry.listCredentials().username, passwordSecretRef: 'acr' } ]
      secrets: [ { name: 'acr', value: registry.listCredentials().passwords[0].value } ]
    }
    template: {
      containers: [ {
        name: 'worker'
        image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
        args: ['python', '-m', 'highlights.worker']
        resources: { cpu: json('1.0'), memory: '2Gi' }
        env: [
          { name: 'AZURE_STORAGE_ACCOUNT', value: storage.name }
          { name: 'HIGHLIGHTS_CONTAINER', value: 'highlights' }
          { name: 'HIGHLIGHTS_DIR', value: '/app/data/highlights' }
          { name: 'WEBPUBSUB_HOST', value: '${pubsub.name}.webpubsub.azure.com' }
          { name: 'WEBPUBSUB_HIGHLIGHTS_HUB', value: 'highlights' }
          { name: 'VISION_DEVICE', value: 'cpu' }
          { name: 'VISION_MODEL', value: 'yolo11n.pt' }
          { name: 'VISION_MAX_WIDTH', value: '960' }
        ]
      } ]
    }
  }
}

resource highlightJobGpu 'Microsoft.App/jobs@2024-10-02-preview' = if (deployGpu) {
  name: 'job-highlights-gpu-${token}'
  location: gpuLocation
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    environmentId: gpuEnv.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1800
      replicaRetryLimit: 1
      replicaCompletionCount: 1
      parallelism: 1
      registries: [ { server: registry.properties.loginServer, username: registry.listCredentials().username, passwordSecretRef: 'acr' } ]
      secrets: [ { name: 'acr', value: registry.listCredentials().passwords[0].value } ]
    }
    template: {
      containers: [ {
        name: 'worker'
        image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
        args: ['python', '-m', 'highlights.worker']
        resources: { cpu: json('8'), memory: '56Gi' }
        env: [
          { name: 'AZURE_STORAGE_ACCOUNT', value: storage.name }
          { name: 'HIGHLIGHTS_CONTAINER', value: 'highlights' }
          { name: 'HIGHLIGHTS_DIR', value: '/app/data/highlights' }
          { name: 'WEBPUBSUB_HOST', value: '${pubsub.name}.webpubsub.azure.com' }
          { name: 'WEBPUBSUB_HIGHLIGHTS_HUB', value: 'highlights' }
          { name: 'VISION_DEVICE', value: 'cuda' }
          { name: 'VISION_MODEL', value: 'yolo11n.pt' }
        ]
      } ]
    }
  }
}

resource jobBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, highlightJob.id, blobContributorRole)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributorRole)
    principalId: highlightJob.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource jobBlobGpu 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployGpu) {
  name: guid(storage.id, highlightJobGpu.id, blobContributorRole)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributorRole)
    principalId: highlightJobGpu.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = if (deployCosmos) {
  name: 'cosmos-${token}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    capabilities: [ { name: 'EnableServerless' } ]
    locations: [ { locationName: location, failoverPriority: 0 } ]
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    disableLocalAuth: false
  }
}

resource cosmosSql 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = if (deployCosmos) {
  parent: cosmos
  name: 'gamelens'
  properties: { resource: { id: 'gamelens' } }
}

resource cosmosHighlights 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = if (deployCosmos) {
  parent: cosmosSql
  name: 'highlights'
  properties: {
    resource: {
      id: 'highlights'
      partitionKey: { paths: ['/id'], kind: 'Hash' }
    }
  }
}

// Event Grid → API webhook. The API answers SubscriptionValidationEvent.
// First `azd up` can skip this (default false); turn it on once ingress exists.
resource highlightEvents 'Microsoft.EventGrid/systemTopics@2022-06-15' = if (deployHighlightEvents) {
  name: 'evgs-${token}'
  location: location
  tags: tags
  properties: {
    source: storage.id
    topicType: 'Microsoft.Storage.StorageAccounts'
  }
}

resource highlightBlobSub 'Microsoft.EventGrid/systemTopics/eventSubscriptions@2022-06-15' = if (deployHighlightEvents) {
  parent: highlightEvents
  name: 'highlights-blob-created'
  properties: {
    destination: {
      endpointType: 'WebHook'
      properties: {
        endpointUrl: 'https://${api.properties.configuration.ingress.fqdn}/api/highlights/hooks/blob'
      }
    }
    filter: {
      includedEventTypes: [ 'Microsoft.Storage.BlobCreated' ]
      subjectBeginsWith: '/blobServices/default/containers/highlights/'
    }
    eventDeliverySchema: 'EventGridSchema'
    retryPolicy: { maxDeliveryAttempts: 5, eventTimeToLiveInMinutes: 60 }
  }
}

// Separate environment: a consumption-only environment cannot take a GPU workload profile.
resource gpuEnv 'Microsoft.App/managedEnvironments@2024-10-02-preview' = if (deployGpu) {
  name: 'cae-gpu-${token}'
  location: gpuLocation
  tags: tags
  properties: {
    workloadProfiles: [
      { name: 'Consumption', workloadProfileType: 'Consumption' }
      { name: 'Consumption-GPU-NC8as-T4', workloadProfileType: 'Consumption-GPU-NC8as-T4' }
    ]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey }
    }
  }
}

// CPU vision app when there is no GPU quota. Same service name so `azd deploy vision` has a target.
resource visionCpu 'Microsoft.App/containerApps@2024-03-01' = if (!deployGpu) {
  name: 'ca-vision-${token}'
  location: location
  tags: union(tags, { 'azd-service-name': 'vision' })
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: true, targetPort: 8000 }
      registries: [ { server: registry.properties.loginServer, username: registry.listCredentials().username, passwordSecretRef: 'acr' } ]
      secrets: [ { name: 'acr', value: registry.listCredentials().passwords[0].value } ]
    }
    template: {
      containers: [ {
        name: 'vision'
        image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
        // three football YOLOv8 models (players, ball tiles, pitch keypoints) need the Consumption maximum
        resources: { cpu: json('4'), memory: '8Gi' }
        env: [
          { name: 'VISION_DEVICE', value: 'cpu' }
          { name: 'AZURE_STORAGE_ACCOUNT', value: storage.name }
          { name: 'AZURE_STORAGE_BLOB_ENDPOINT', value: storage.properties.primaryEndpoints.blob }
          { name: 'VISION_CONTAINER', value: 'match-video' }
        ]
      } ]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
}

resource visionGpu 'Microsoft.App/containerApps@2024-10-02-preview' = if (deployGpu) {
  name: 'ca-vision-${token}'
  location: gpuLocation
  tags: union(tags, { 'azd-service-name': 'vision' })
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: gpuEnv.id
    workloadProfileName: 'Consumption-GPU-NC8as-T4'
    configuration: {
      ingress: { external: true, targetPort: 8000 }
      registries: [ { server: registry.properties.loginServer, username: registry.listCredentials().username, passwordSecretRef: 'acr' } ]
      secrets: [ { name: 'acr', value: registry.listCredentials().passwords[0].value } ]
    }
    template: {
      containers: [ {
        name: 'vision'
        image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
        // T4 serverless profile is fixed at 8 vCPU and 56 GiB. minReplicas 0 bills only while a job is running.
        resources: { cpu: json('8'), memory: '56Gi' }
        env: [
          { name: 'VISION_DEVICE', value: 'cuda' }
          { name: 'VISION_MODEL', value: 'yolo11n.pt' }
          { name: 'AZURE_STORAGE_ACCOUNT', value: storage.name }
          { name: 'AZURE_STORAGE_BLOB_ENDPOINT', value: storage.properties.primaryEndpoints.blob }
          { name: 'VISION_CONTAINER', value: 'match-video' }
        ]
      } ]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
}

resource visionBlobCpu 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!deployGpu) {
  name: guid(storage.id, visionCpu.id, blobReaderRole)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobReaderRole)
    principalId: visionCpu.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource visionBlobGpu 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployGpu) {
  name: guid(storage.id, visionGpu.id, blobReaderRole)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobReaderRole)
    principalId: visionGpu.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource web 'Microsoft.Web/staticSites@2023-12-01' = {
  name: 'swa-${token}'
  location: webLocation
  tags: union(tags, { 'azd-service-name': 'web' })
  sku: { name: 'Free', tier: 'Free' }
  properties: {}
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.properties.loginServer
output API_URL string = 'https://${api.properties.configuration.ingress.fqdn}'
output WEB_URL string = 'https://${web.properties.defaultHostname}'
output VISION_URL string = deployGpu ? 'https://${visionGpu.properties.configuration.ingress.fqdn}' : 'https://${visionCpu.properties.configuration.ingress.fqdn}'
output EVENTHUB_NAMESPACE string = ehNamespace.name
output EVENTHUB_TRACKING_NAME string = matchTracking.name
output WEBPUBSUB_HOSTNAME string = '${pubsub.name}.webpubsub.azure.com'
output STORAGE_ACCOUNT string = storage.name
output STORAGE_BLOB_ENDPOINT string = storage.properties.primaryEndpoints.blob
output HIGHLIGHTS_CONTAINER string = highlightsBlobs.name
output HIGHLIGHTS_JOB_NAME string = highlightJob.name
output COSMOS_ENDPOINT string = deployCosmos ? cosmos.properties.documentEndpoint : ''
output AI_ENDPOINT string = ai.properties.endpoint
