// Azure Container Instance for long-running content generation jobs
// This avoids GitHub Actions' 6-hour timeout and token expiration issues

@description('Base name for resources')
param baseName string

@description('Location for resources')
param location string = resourceGroup().location

@description('User-assigned managed identity resource ID')
param userAssignedIdentityId string

@description('User-assigned managed identity principal ID')
param userAssignedIdentityPrincipalId string

@description('User-assigned managed identity client ID')
param userAssignedIdentityClientId string

@description('Container registry login server')
param containerRegistryLoginServer string = 'ghcr.io'

@description('Container image name')
param containerImage string

@description('OpenAI endpoint')
param openaiEndpoint string

@description('Speech endpoint')
param speechEndpoint string

@description('Speech region')
param speechRegion string

@description('Cosmos DB endpoint')
param cosmosEndpoint string

@description('Storage account name')
param storageAccountName string

@description('Search endpoint')
param searchEndpoint string

@description('Azure subscription ID')
param subscriptionId string = subscription().subscriptionId

@description('Resource group name')
param resourceGroupName string = resourceGroup().name

// Container Instance for generation jobs
// Note: This is a "template" - actual jobs are created via CLI with specific parameters
resource containerGroup 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: '${baseName}-generate-template'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${userAssignedIdentityId}': {}
    }
  }
  properties: {
    osType: 'Linux'
    restartPolicy: 'Never'  // Run once, don't restart on completion
    containers: [
      {
        name: 'generate'
        properties: {
          image: containerImage
          resources: {
            requests: {
              cpu: 2
              memoryInGB: 4
            }
          }
          environmentVariables: [
            // Azure auth - managed identity auto-handles this
            { name: 'AZURE_CLIENT_ID', value: userAssignedIdentityClientId }
            { name: 'AZURE_SUBSCRIPTION_ID', value: subscriptionId }
            { name: 'AZURE_RESOURCE_GROUP', value: resourceGroupName }
            
            // Service endpoints
            { name: 'OPENAI_ENDPOINT', value: openaiEndpoint }
            { name: 'SPEECH_ENDPOINT', value: speechEndpoint }
            { name: 'SPEECH_REGION', value: speechRegion }
            { name: 'COSMOS_DB_ENDPOINT', value: cosmosEndpoint }
            { name: 'STORAGE_ACCOUNT_NAME', value: storageAccountName }
            { name: 'SEARCH_ENDPOINT', value: searchEndpoint }
            
            // Generation settings (override via CLI when creating job)
            { name: 'CERTIFICATION_ID', value: 'dp-700' }
            { name: 'AUDIO_FORMAT', value: 'instructional' }
            { name: 'DISCOVERY_MODE', value: 'comprehensive' }
            { name: 'TTS_MAX_WORKERS', value: '10' }
          ]
          command: [
            'python3', '-m', 'tools.generate_all'
          ]
        }
      }
    ]
  }
}

output containerGroupName string = containerGroup.name
output containerGroupId string = containerGroup.id
