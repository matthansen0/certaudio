// AI Foundry Module for Study Partner Agent
// Deploys: Foundry Account, Project, Connections
// NOTE: Capability hosts are commented out due to Azure preview API issues
// The Azure AI Projects SDK can work without explicit capability hosts
// Deployed only when enableStudyPartner=true

// ============================================================================
// PARAMETERS
// ============================================================================

param resourcePrefix string
param location string
param uniqueSuffix string
param tags object

@description('Whether to deploy AI Foundry resources')
param enabled bool = true

@description('Name of the existing Cosmos DB account')
param cosmosDbAccountName string

@description('Name of the existing Storage account')
param storageAccountName string

@description('Name of the existing AI Search service')
param searchServiceName string

// Model configuration
@description('Model name for the agent')
param modelName string = 'gpt-4o'

@description('Model version')
param modelVersion string = '2024-11-20'

@description('Model SKU name')
param modelSkuName string = 'GlobalStandard'

@description('Model capacity (TPM in thousands)')
param modelCapacity int = 30

// ============================================================================
// VARIABLES
// ============================================================================

var foundryAccountName = '${resourcePrefix}-foundry-${uniqueSuffix}'
var projectName = 'study-partner'
// Capability host names (kept for reference but not deployed via Bicep)
// var accountCapHostName = 'caphost-account'
// var projectCapHostName = 'caphost-project'

// ============================================================================
// AI FOUNDRY ACCOUNT
// ============================================================================

// Microsoft Foundry Account (AIServices kind with project management)
resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = if (enabled) {
  name: foundryAccountName
  location: location
  tags: union(tags, {
    purpose: 'study-partner-agent'
  })
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: foundryAccountName
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      virtualNetworkRules: []
      ipRules: []
    }
    disableLocalAuth: false
  }
}

// Model deployment for the agent
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = if (enabled) {
  parent: foundryAccount
  name: modelName
  sku: {
    name: modelSkuName
    capacity: modelCapacity
  }
  properties: {
    model: {
      name: modelName
      format: 'OpenAI'
      version: modelVersion
    }
  }
}

// ============================================================================
// FOUNDRY PROJECT
// ============================================================================

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = if (enabled) {
  parent: foundryAccount
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: 'Study Partner AI Agent for certification exam preparation'
    displayName: 'Study Partner'
  }
}

// ============================================================================
// PROJECT CONNECTIONS (to existing resources)
// Must be created after project but before capability hosts
// ============================================================================

// Reference existing resources for connection metadata
resource existingCosmosDb 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = if (enabled) {
  name: cosmosDbAccountName
}

resource existingStorage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = if (enabled) {
  name: storageAccountName
}

resource existingSearch 'Microsoft.Search/searchServices@2024-03-01-preview' existing = if (enabled) {
  name: searchServiceName
}

// Connection to Cosmos DB for thread storage
resource cosmosConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = if (enabled) {
  parent: foundryProject
  name: '${cosmosDbAccountName}-connection'
  properties: {
    category: 'CosmosDB'
    target: existingCosmosDb.properties.documentEndpoint
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: existingCosmosDb.id
      location: existingCosmosDb.location
    }
  }
}

// Connection to Storage for file storage
resource storageConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = if (enabled) {
  parent: foundryProject
  name: '${storageAccountName}-connection'
  properties: {
    category: 'AzureStorageAccount'
    target: existingStorage.properties.primaryEndpoints.blob
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: existingStorage.id
      location: existingStorage.location
    }
  }
}

// Connection to AI Search for vector store
resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = if (enabled) {
  parent: foundryProject
  name: '${searchServiceName}-connection'
  properties: {
    category: 'CognitiveSearch'
    target: 'https://${searchServiceName}.search.windows.net'
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: existingSearch.id
      location: existingSearch.location
    }
  }
}

// ============================================================================
// CAPABILITY HOSTS (COMMENTED OUT - Azure preview API issues)
// ============================================================================
// The Azure AI Projects SDK can create agents without explicit capability hosts 
// deployed via Bicep. Capability hosts may be auto-created by the service.
// If needed, these can be enabled via Azure CLI after deployment.

// // Account-level capability host
// resource accountCapabilityHost 'Microsoft.CognitiveServices/accounts/capabilityHosts@2025-04-01-preview' = if (enabled) {
//   parent: foundryAccount
//   name: accountCapHostName
//   properties: {
//     capabilityHostKind: 'Agents'
//   }
// }

// // Project-level capability host with resource connections
// resource projectCapabilityHost 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = if (enabled) {
//   parent: foundryProject
//   name: projectCapHostName
//   properties: {
//     capabilityHostKind: 'Agents'
//     vectorStoreConnections: ['${searchServiceName}-connection']
//     storageConnections: ['${storageAccountName}-connection']
//     threadStorageConnections: ['${cosmosDbAccountName}-connection']
//   }
//   dependsOn: [
//     accountCapabilityHost
//     cosmosConnection
//     storageConnection
//     searchConnection
//   ]
// }

// ============================================================================
// OUTPUTS
// ============================================================================

// Core identifiers
output foundryAccountName string = enabled ? foundryAccount.name : ''
output foundryAccountId string = enabled ? foundryAccount.id : ''
output foundryProjectName string = enabled ? foundryProject.name : ''
output foundryProjectId string = enabled ? foundryProject.id : ''
output modelDeploymentName string = enabled ? modelDeployment.name : ''

// Endpoints for SDK usage
output foundryAccountEndpoint string = enabled ? foundryAccount.properties.endpoint : ''

// Project principal ID (needed for role assignments via CLI)
output foundryProjectPrincipalId string = enabled ? foundryProject.identity.principalId : ''

// Connection names (for reference)
output cosmosConnectionName string = enabled ? cosmosConnection.name : ''
output storageConnectionName string = enabled ? storageConnection.name : ''
output searchConnectionName string = enabled ? searchConnection.name : ''
