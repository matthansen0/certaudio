// Azure AI Certification Audio Learning Platform
// Main orchestrator for all infrastructure modules

targetScope = 'resourceGroup'

// ============================================================================
// PARAMETERS
// ============================================================================

@description('Azure region for resources')
param location string = resourceGroup().location

@description('Unique suffix for globally unique resource names')
param uniqueSuffix string = uniqueString(resourceGroup().id)

@description('Location for Azure OpenAI (GPT-4o has limited regional availability)')
@allowed(['eastus', 'eastus2', 'westus', 'westus3', 'northcentralus', 'southcentralus', 'westeurope', 'swedencentral'])
param openAiLocation string = 'eastus2'

@description('Location for Azure Speech (HD voices only available in eastus, westeurope, southeastasia)')
@allowed(['eastus', 'westeurope', 'southeastasia'])
param speechLocation string = 'eastus'

@description('Location for AI Foundry (Standard Agent Setup requires specific regions)')
@allowed(['eastus', 'eastus2', 'westus', 'westus2', 'westus3', 'swedencentral', 'westeurope', 'southcentralus', 'canadaeast', 'australiaeast', 'uksouth'])
param foundryLocation string = 'eastus'

@description('Optional AAD object ID of the automation principal (e.g., GitHub OIDC service principal) that runs content-generation workflows. If provided, it is granted Cosmos SQL Data Contributor at the database scope.')
param automationPrincipalId string = ''

@description('Enable Study Partner feature with AI Foundry Agent (~$75+/month for AI Search + agent infrastructure). When false, the Study Partner page shows "not deployed".')
param enableStudyPartner bool = false

// ============================================================================
// VARIABLES
// ============================================================================

var baseName = 'certaudio'
var environment = 'dev'
var resourcePrefix = '${baseName}-${environment}'
var tags = {
  project: 'certification-audio-platform'
  environment: environment
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
    openAiLocation: openAiLocation
    speechLocation: speechLocation
    uniqueSuffix: uniqueSuffix
    automationPrincipalId: automationPrincipalId
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
    automationPrincipalId: automationPrincipalId
    tags: tags
  }
}

// Study Partner: AI Search for RAG queries (optional, ~$75/month)
// Module is always called but resources are conditionally deployed based on enableStudyPartner
module search 'modules/search-persistent.bicep' = {
  name: 'deploy-search-persistent'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    uniqueSuffix: uniqueSuffix
    automationPrincipalId: automationPrincipalId
    enabled: enableStudyPartner
    tags: tags
  }
}

// AI Foundry: Agent Service for Study Partner (optional)
// Provides true AI agent capabilities with built-in tools for RAG
// Must be deployed before web module so we can pass endpoints to Functions
module aiFoundry 'modules/ai-foundry.bicep' = {
  name: 'deploy-ai-foundry'
  params: {
    resourcePrefix: resourcePrefix
    location: foundryLocation
    uniqueSuffix: uniqueSuffix
    enabled: enableStudyPartner
    cosmosDbAccountName: data.outputs.cosmosDbAccountName
    storageAccountName: data.outputs.storageAccountName
    searchServiceName: search.outputs.searchName
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
    automationPrincipalId: automationPrincipalId
    openAiEndpoint: aiServices.outputs.openAiEndpoint
    speechEndpoint: aiServices.outputs.speechEndpoint
    searchEndpoint: search.outputs.searchEndpoint
    foundryEndpoint: aiFoundry.outputs.foundryAccountEndpoint
    foundrySearchConnection: aiFoundry.outputs.searchConnectionName
    tags: tags
  }
}



// ============================================================================
// OUTPUTS
// ============================================================================

output resourceGroupName string = resourceGroup().name
output storageAccountName string = data.outputs.storageAccountName
output cosmosDbAccountName string = data.outputs.cosmosDbAccountName
output cosmosDbDatabaseName string = data.outputs.cosmosDbDatabaseName
output staticWebAppName string = web.outputs.staticWebAppName
output staticWebAppUrl string = web.outputs.staticWebAppUrl
output functionsAppName string = web.outputs.functionsAppName
output funcStorageAccountName string = web.outputs.funcStorageAccountName
output openAiName string = aiServices.outputs.openAiName
output openAiEndpoint string = aiServices.outputs.openAiEndpoint
output speechEndpoint string = aiServices.outputs.speechEndpoint
output speechRegion string = aiServices.outputs.speechRegion
output documentIntelligenceEndpoint string = aiServices.outputs.documentIntelligenceEndpoint

// Study Partner outputs (conditional)
output studyPartnerEnabled bool = enableStudyPartner
output searchName string = search.outputs.searchName
output searchEndpoint string = search.outputs.searchEndpoint

// AI Foundry outputs (conditional)
output foundryAccountName string = aiFoundry.outputs.foundryAccountName
output foundryProjectEndpoint string = aiFoundry.outputs.foundryAccountEndpoint
output foundryModelDeployment string = aiFoundry.outputs.modelDeploymentName
output foundryProjectPrincipalId string = aiFoundry.outputs.foundryProjectPrincipalId
