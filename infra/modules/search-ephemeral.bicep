// Ephemeral AI Search Module
// Deployed on-demand during content generation, deleted after to save ~$250/month
// This module is called directly from GitHub Actions, not from main.bicep

targetScope = 'resourceGroup'

// ============================================================================
// PARAMETERS
// ============================================================================

@description('Resource prefix for naming')
param resourcePrefix string = 'certaudio-dev'

@description('Azure region for resources')
param location string = resourceGroup().location

@description('Unique suffix for globally unique resource names')
param uniqueSuffix string = uniqueString(resourceGroup().id)

@description('AAD object ID of the automation principal for RBAC')
param automationPrincipalId string = ''

param tags object = {
  project: 'certification-audio-platform'
  ephemeral: 'true'
  purpose: 'content-generation'
}

// ============================================================================
// VARIABLES
// ============================================================================

var searchName = '${resourcePrefix}-search-${uniqueSuffix}'

// ============================================================================
// RESOURCES
// ============================================================================

// Azure AI Search (Basic tier - cheapest paid tier with semantic search)
resource search 'Microsoft.Search/searchServices@2024-03-01-preview' = {
  name: searchName
  location: location
  tags: tags
  sku: {
    name: 'basic'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    semanticSearch: 'free'
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

// Search Index Data Contributor - allows creating indexes and uploading documents
resource searchIndexDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (automationPrincipalId != '') {
  name: guid(search.id, automationPrincipalId, 'Search Index Data Contributor')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
    principalId: automationPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Search Service Contributor - allows managing the search service
resource searchServiceContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (automationPrincipalId != '') {
  name: guid(search.id, automationPrincipalId, 'Search Service Contributor')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
    principalId: automationPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// OUTPUTS
// ============================================================================

output searchName string = search.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchId string = search.id
