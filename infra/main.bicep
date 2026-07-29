// Azure AI Certification Audio Learning Platform
// Main orchestrator for all infrastructure modules.
//
// Deployed with the Azure Developer CLI: `azd up`. Subscription scoped so azd
// can create the resource group itself and the whole stack comes up from a
// clean subscription with no CI/CD involved.

targetScope = 'subscription'

// ============================================================================
// PARAMETERS
// ============================================================================

@description('Name of the azd environment. Drives resource naming and the azd-env-name tag.')
@minLength(1)
@maxLength(24)
param environmentName string = 'dev'

@description('Azure region for resources')
param location string

@description('Resource group to deploy into. Defaults to rg-<baseName>-<environmentName>.')
param resourceGroupName string = ''

@description('Unique suffix for globally unique resource names. Pin this to adopt an existing deployment; otherwise it is derived from the subscription and environment.')
param uniqueSuffix string = ''

@description('Location for Azure OpenAI (GPT-4o has limited regional availability)')
@allowed(['eastus', 'eastus2', 'westus', 'westus3', 'northcentralus', 'southcentralus', 'westeurope', 'swedencentral'])
param openAiLocation string = 'eastus2'

@description('Location for Azure Speech (HD voices only available in eastus, westeurope, southeastasia)')
@allowed(['eastus', 'westeurope', 'southeastasia'])
param speechLocation string = 'eastus'

@description('Location for AI Foundry (Standard Agent Setup requires specific regions)')
@allowed(['eastus', 'eastus2', 'westus', 'westus2', 'westus3', 'swedencentral', 'westeurope', 'southcentralus', 'canadaeast', 'australiaeast', 'uksouth'])
param foundryLocation string = 'eastus'

@description('Object ID of the principal running the deployment. azd supplies this automatically. Granted data-plane roles so you can inspect Cosmos, Storage and Search from your own machine.')
param principalId string = ''

@description('Enable Study Partner feature with AI Foundry Agent (~$5-10/month for the agent; AI Search is always deployed because generation needs it). When false, the Study Partner page shows "not deployed".')
param enableStudyPartner bool = false

@description('One-time token allowing the first authenticated user to claim admin access in the portal. Rotates on every deployment and can only be claimed once.')
@secure()
param adminBootstrapToken string = newGuid()

// ============================================================================
// VARIABLES
// ============================================================================

var baseName = 'certaudio'
var resourcePrefix = '${baseName}-${environmentName}'
var rgName = empty(resourceGroupName) ? 'rg-${baseName}-${environmentName}' : resourceGroupName
// uniqueString on the subscription keeps names stable across redeployments of the
// same environment, while still differing between environments and subscriptions.
var suffix = empty(uniqueSuffix) ? uniqueString(subscription().id, environmentName) : uniqueSuffix
var tags = {
  project: 'certification-audio-platform'
  environment: environmentName
  'azd-env-name': environmentName
}

// ============================================================================
// RESOURCE GROUP
// ============================================================================

resource rg 'Microsoft.Resources/resourceGroups@2021-04-01' = {
  name: rgName
  location: location
  tags: tags
}

// ============================================================================
// MODULES
// ============================================================================

// Network: VNet integration, private endpoints, and private DNS
module network 'modules/network.bicep' = {
  scope: rg
  name: 'deploy-network'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    tags: tags
  }
}

// AI Services: OpenAI, Speech, Document Intelligence, AI Search
module aiServices 'modules/ai-services.bicep' = {
  scope: rg
  name: 'deploy-ai-services'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    openAiLocation: openAiLocation
    speechLocation: speechLocation
    uniqueSuffix: suffix
    automationPrincipalId: principalId
    tags: tags
  }
}

// Data: Cosmos DB, Storage Account
module data 'modules/data.bicep' = {
  scope: rg
  name: 'deploy-data'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    uniqueSuffix: suffix
    automationPrincipalId: principalId
    tags: tags
  }
}

// AI Search: shared `certification-content` index. Always deployed - content
// generation grounds narration in it, so it is not optional.
module search 'modules/search-persistent.bicep' = {
  scope: rg
  name: 'deploy-search-persistent'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    uniqueSuffix: suffix
    automationPrincipalId: principalId
    enabled: true
    tags: tags
  }
}

// AI Foundry: Agent Service for Study Partner (optional)
// Provides true AI agent capabilities with built-in tools for RAG
// Must be deployed before web module so we can pass endpoints to Functions
module aiFoundry 'modules/ai-foundry.bicep' = {
  scope: rg
  name: 'deploy-ai-foundry'
  params: {
    resourcePrefix: resourcePrefix
    location: foundryLocation
    uniqueSuffix: suffix
    enabled: enableStudyPartner
    cosmosDbAccountName: data.outputs.cosmosDbAccountName
    storageAccountName: data.outputs.storageAccountName
    searchServiceName: search.outputs.searchName
    tags: tags
  }
}

// Web: Static Web Apps, Functions
module web 'modules/web.bicep' = {
  scope: rg
  name: 'deploy-web'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    uniqueSuffix: suffix
    storageAccountName: data.outputs.storageAccountName
    cosmosDbAccountName: data.outputs.cosmosDbAccountName
    cosmosDbDatabaseName: data.outputs.cosmosDbDatabaseName
    functionsSubnetId: network.outputs.functionsSubnetId
    automationPrincipalId: principalId
    openAiEndpoint: aiServices.outputs.openAiEndpoint
    speechEndpoint: aiServices.outputs.speechEndpoint
    speechRegion: aiServices.outputs.speechRegion
    searchEndpoint: search.outputs.searchEndpoint
    adminBootstrapToken: adminBootstrapToken
    foundryEndpoint: aiFoundry.outputs.foundryAccountEndpoint
    foundrySearchConnection: aiFoundry.outputs.searchConnectionName
    tags: tags
  }
}

// Data-plane RBAC for the Functions managed identity. This used to live in
// workflow bash, which meant the deployment was only complete if CI ran. It is
// declarative now so `azd up` produces a working system on its own.
module functionsRbac 'modules/rbac.bicep' = {
  scope: rg
  name: 'deploy-functions-rbac'
  params: {
    functionsPrincipalId: web.outputs.functionsAppPrincipalId
    dataStorageAccountName: data.outputs.storageAccountName
    funcStorageAccountName: web.outputs.funcStorageAccountName
    cosmosDbAccountName: data.outputs.cosmosDbAccountName
    cosmosDbDatabaseName: data.outputs.cosmosDbDatabaseName
    openAiName: aiServices.outputs.openAiName
    speechName: aiServices.outputs.speechName
    searchName: search.outputs.searchName
  }
}

// Private Link: policy-compliant access from Functions and private batch jobs
module privateEndpoints 'modules/private-endpoints.bicep' = {
  scope: rg
  name: 'deploy-private-endpoints'
  params: {
    resourcePrefix: resourcePrefix
    location: location
    privateEndpointsSubnetId: network.outputs.privateEndpointsSubnetId
    cosmosDbId: data.outputs.cosmosDbId
    dataStorageAccountId: data.outputs.storageAccountId
    funcStorageAccountId: web.outputs.funcStorageAccountId
    blobPrivateDnsZoneId: network.outputs.blobPrivateDnsZoneId
    queuePrivateDnsZoneId: network.outputs.queuePrivateDnsZoneId
    tablePrivateDnsZoneId: network.outputs.tablePrivateDnsZoneId
    cosmosPrivateDnsZoneId: network.outputs.cosmosPrivateDnsZoneId
    tags: tags
  }
}

// ============================================================================
// OUTPUTS
// ============================================================================

// azd surfaces every output as an environment variable in .azure/<env>/.env,
// which is how the postprovision hook and local tooling discover the deployment.
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = subscription().tenantId
output AZURE_RESOURCE_GROUP string = rg.name

output resourceGroupName string = rg.name
output storageAccountName string = data.outputs.storageAccountName
output cosmosDbAccountName string = data.outputs.cosmosDbAccountName
output cosmosDbDatabaseName string = data.outputs.cosmosDbDatabaseName
output staticWebAppName string = web.outputs.staticWebAppName
output staticWebAppUrl string = web.outputs.staticWebAppUrl
output functionsAppName string = web.outputs.functionsAppName
output functionsAppUrl string = web.outputs.functionsAppUrl
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
