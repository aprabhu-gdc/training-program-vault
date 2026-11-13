// Claude analytics connector: Azure Container Apps (consumption, scale-to-zero
// outside business hours) + Table Storage for encrypted sign-in state.
// Deploy: see docs/claude-analytics-connector.md ("Deploy the infrastructure").

@description('Azure region. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Short lowercase prefix for resource names, e.g. "gdcanalytics".')
@minLength(3)
@maxLength(16)
param namePrefix string

@description('Container image, e.g. ghcr.io/aprabhu-gdc/training-analytics-connector:<sha>.')
param image string

@description('GHCR user that owns the read-only package token.')
param registryUsername string

@description('GitHub personal access token with read:packages only.')
@secure()
param registryPassword string

@description('Entra tenant id.')
param tenantId string

@description('Application (client) id of the "Training Analytics Connector" app registration.')
param connectorClientId string

@description('Client secret of that app registration.')
@secure()
param connectorClientSecret string

@description('Random string of 32+ characters that signs connector sessions and encrypts stored tokens.')
@secure()
param jwtSigningKey string

@description('SharePoint site id that holds the analytics lists (hostname,siteCollectionId,webId).')
param sharepointSiteId string

@description('List id of TrainingBotQueryEvents (required for list-level permissions).')
param queryListId string = ''

@description('List id of TrainingBotFeedback (required for list-level permissions).')
param feedbackListId string = ''

@description('IANA time zone for business-hours scaling, e.g. America/New_York.')
param businessHoursTimeZone string

@description('Cron (min hour dom mon dow) when one replica starts staying warm.')
param businessHoursStart string = '0 7 * * 1-5'

@description('Cron when the warm replica is released (scale-to-zero resumes).')
param businessHoursEnd string = '0 19 * * 1-5'

var appName = '${namePrefix}-connector'
var storageName = take(replace('${namePrefix}state${uniqueString(resourceGroup().id)}', '-', ''), 24)
var stateTableName = 'mcpoauthstate'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource stateTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: stateTableName
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var stateConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
var baseUrl = 'https://${appName}.${env.properties.defaultDomain}'

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: 'ghcr.io'
          username: registryUsername
          passwordSecretRef: 'registry-password'
        }
      ]
      secrets: [
        { name: 'registry-password', value: registryPassword }
        { name: 'connector-client-secret', value: connectorClientSecret }
        { name: 'jwt-signing-key', value: jwtSigningKey }
        { name: 'state-connection-string', value: stateConnectionString }
      ]
    }
    template: {
      containers: [
        {
          name: 'connector'
          image: image
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'ANALYTICS_MCP_BASE_URL', value: baseUrl }
            { name: 'ANALYTICS_MCP_TENANT_ID', value: tenantId }
            { name: 'ANALYTICS_MCP_CLIENT_ID', value: connectorClientId }
            { name: 'ANALYTICS_MCP_CLIENT_SECRET', secretRef: 'connector-client-secret' }
            { name: 'ANALYTICS_MCP_JWT_SIGNING_KEY', secretRef: 'jwt-signing-key' }
            { name: 'ANALYTICS_MCP_STATE_CONNECTION_STRING', secretRef: 'state-connection-string' }
            { name: 'ANALYTICS_MCP_STATE_TABLE', value: stateTableName }
            { name: 'ANALYTICS_MCP_QUERY_LIST_ID', value: queryListId }
            { name: 'ANALYTICS_MCP_FEEDBACK_LIST_ID', value: feedbackListId }
            // The same app registration reads/writes the analytics lists (least privilege:
            // Lists.SelectedOperations.Selected or Sites.Selected - never Sites.ReadWrite.All).
            { name: 'SHAREPOINT_TENANT_ID', value: tenantId }
            { name: 'SHAREPOINT_CLIENT_ID', value: connectorClientId }
            { name: 'SHAREPOINT_CLIENT_SECRET', secretRef: 'connector-client-secret' }
            { name: 'SHAREPOINT_SITE_ID', value: sharepointSiteId }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8000 }
              initialDelaySeconds: 5
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        // One replica max: sign-in state is shared via Table Storage, but the
        // in-process rate limiter and feedback cache assume a single instance.
        minReplicas: 0
        maxReplicas: 1
        rules: [
          {
            name: 'http'
            http: { metadata: { concurrentRequests: '20' } }
          }
          {
            name: 'business-hours'
            custom: {
              type: 'cron'
              metadata: {
                timezone: businessHoursTimeZone
                start: businessHoursStart
                end: businessHoursEnd
                desiredReplicas: '1'
              }
            }
          }
        ]
      }
    }
  }
}

output connectorUrl string = '${baseUrl}/mcp'
output oauthRedirectUri string = '${baseUrl}/auth/callback'
output storageAccountName string = storage.name
