// Azure AI Certification Audio Learning Platform
// Main orchestrator for all infrastructure modules

targetScope = 'resourceGroup'

// ============================================================================
// PARAMETERS
// ============================================================================

@description('Microsoft certification ID (e.g., ai-102, az-204, az-104)')
param certificationId string = 'ai-102'

@description('Audio format for the course')
@allowed(['instructional', 'podcast'])
param audioFormat string = 'instructional'

@description('Enable Azure AD B2C authentication')
param enableB2C bool = false

@description('Azure region for resources')
param location string = resourceGroup().location

@description('Environment name for resource naming')
@allowed(['dev', 'prod'])
param environment string = 'dev'

@description('Unique suffix for globally unique resource names')
param uniqueSuffix string = uniqueString(resourceGroup().id)

// ============================================================================
// VARIABLES
// ============================================================================

var baseName = 'certaudio'
var resourcePrefix = '${baseName}-${environment}'
var tags = {
  project: 'certification-audio-platform'
  certification: certificationId
  environment: environment
  audioFormat: audioFormat
}

// ============================================================================
// MODULES
// ============================================================================

// AI Services: OpenAI, Speech, Document Intelligence, AI Search
module aiServices 'modules/ai-services.bicep' = {
  name: 'deploy-ai-services'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

// Data: Cosmos DB, Storage Account
module data 'modules/data.bicep' = {
  name: 'deploy-data'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    uniqueSuffix: uniqueSuffix
    certificationId: certificationId
    audioFormat: audioFormat
    tags: tags
  }
}

// Web: Static Web Apps, Functions
module web 'modules/web.bicep' = {
  name: 'deploy-web'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    uniqueSuffix: uniqueSuffix
    storageAccountName: data.outputs.storageAccountName
    cosmosDbAccountName: data.outputs.cosmosDbAccountName
    cosmosDbDatabaseName: data.outputs.cosmosDbDatabaseName
    openAiEndpoint: aiServices.outputs.openAiEndpoint
    speechEndpoint: aiServices.outputs.speechEndpoint
    searchEndpoint: aiServices.outputs.searchEndpoint
    enableB2C: enableB2C
    tags: tags
  }
}

// Identity: Azure AD B2C (conditional)
module identity 'modules/identity.bicep' = if (enableB2C) {
  name: 'deploy-identity'
  params: {
    resourcePrefix: resourcePrefix
    staticWebAppName: web.outputs.staticWebAppName
  }
}

// ============================================================================
// OUTPUTS
// ============================================================================

output resourceGroupName string = resourceGroup().name
output storageAccountName string = data.outputs.storageAccountName
output cosmosDbAccountName string = data.outputs.cosmosDbAccountName
output staticWebAppName string = web.outputs.staticWebAppName
output staticWebAppUrl string = web.outputs.staticWebAppUrl
output functionsAppName string = web.outputs.functionsAppName
output openAiEndpoint string = aiServices.outputs.openAiEndpoint
output speechEndpoint string = aiServices.outputs.speechEndpoint
output searchEndpoint string = aiServices.outputs.searchEndpoint
output documentIntelligenceEndpoint string = aiServices.outputs.documentIntelligenceEndpoint
output b2cTenantName string = enableB2C && identity != null ? identity!.outputs.b2cTenantName : 'N/A - B2C disabled'
