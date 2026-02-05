// Persistent AI Search Module for Study Partner
// Deployed only when enableStudyPartner=true in main.bicep
// This is SEPARATE from the ephemeral search used during content generation

// ============================================================================
// PARAMETERS
// ============================================================================

param resourcePrefix string
param location string
param uniqueSuffix string
param automationPrincipalId string = ''
param tags object

@description('Whether to actually deploy the search service')
param enabled bool = true

// ============================================================================
// VARIABLES
// ============================================================================

var searchName = '${resourcePrefix}-search-${uniqueSuffix}'

// ============================================================================
// RESOURCES
// ============================================================================

// Azure AI Search (Basic tier - ~$75/month)
// Persistent instance for Study Partner RAG queries
resource search 'Microsoft.Search/searchServices@2024-03-01-preview' = if (enabled) {
  name: searchName
  location: location
  tags: union(tags, {
    purpose: 'study-partner-rag'
    persistent: 'true'
  })
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

// Search Index Data Contributor - allows querying and managing indexes
resource searchIndexDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enabled && automationPrincipalId != '') {
  name: guid(search.id, automationPrincipalId, 'Search Index Data Contributor')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
    principalId: automationPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Search Index Data Reader - allows running queries (for Functions managed identity)
// This is assigned via CLI in deploy-infra.yml to the Functions MI

// ============================================================================
// OUTPUTS
// ============================================================================

output searchName string = enabled ? searchName : ''
output searchEndpoint string = enabled ? 'https://${searchName}.search.windows.net' : ''
output searchId string = enabled ? search.id : ''
