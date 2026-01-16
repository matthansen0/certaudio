// AI Services Module
// Deploys: Azure OpenAI, AI Speech, Document Intelligence, AI Search

// ============================================================================
// PARAMETERS
// ============================================================================

param resourcePrefix string
param location string
param uniqueSuffix string
param tags object
@description('Location for Azure OpenAI (may differ from main location due to model availability)')
param openAiLocation string = 'eastus2'
@description('Optional AAD object ID of an automation principal (e.g., GitHub OIDC service principal) that runs content-generation workflows. If provided, it is granted Azure AI Search Index Data Contributor on the Search service.')
param automationPrincipalId string = ''
// ============================================================================
// VARIABLES
// ============================================================================

var openAiName = '${resourcePrefix}-openai-${uniqueSuffix}'
var speechName = '${resourcePrefix}-speech-${uniqueSuffix}'
var docIntelName = '${resourcePrefix}-docintel-${uniqueSuffix}'
var searchName = '${resourcePrefix}-search-${uniqueSuffix}'

// ============================================================================
// RESOURCES
// ============================================================================

// Azure OpenAI Service
// Note: Using separate location due to model availability constraints
resource openAi 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: openAiName
  location: openAiLocation
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: openAiName
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// GPT-4o deployment for script generation
// Using GlobalStandard for wider regional availability
resource gpt4oDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: openAi
  name: 'gpt-4o'
  sku: {
    name: 'GlobalStandard'
    capacity: 30
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-08-06'
    }
    raiPolicyName: 'Microsoft.Default'
  }
}

// Text embedding deployment for RAG
resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: openAi
  name: 'text-embedding-3-large'
  sku: {
    name: 'Standard'
    capacity: 30
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-large'
      version: '1'
    }
  }
  dependsOn: [gpt4oDeployment]
}

// Azure AI Speech Service
resource speech 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: speechName
  location: location
  tags: tags
  kind: 'SpeechServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: speechName
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// Azure AI Document Intelligence
resource documentIntelligence 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: docIntelName
  location: location
  tags: tags
  kind: 'FormRecognizer'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: docIntelName
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// Azure AI Search
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
    // Enable RBAC authentication for data-plane operations (index management, document upload)
    authOptions: {
      aadOrApiKey: {
        aadAuthFailureMode: 'http401WithBearerChallenge'
      }
    }
  }
}

// Data-plane RBAC: allow automation principal to create/update indexes and upload documents.
resource searchIndexDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (automationPrincipalId != '') {
  name: guid(search.id, automationPrincipalId, 'Search Index Data Contributor')
  scope: search
  properties: {
    // Built-in role: Search Index Data Contributor
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
    principalId: automationPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Data-plane RBAC: allow automation principal to call OpenAI embeddings and completions.
resource openAiUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (automationPrincipalId != '') {
  name: guid(openAi.id, automationPrincipalId, 'Cognitive Services OpenAI User')
  scope: openAi
  properties: {
    // Built-in role: Cognitive Services OpenAI User
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalId: automationPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// OUTPUTS
// ============================================================================

output openAiName string = openAi.name
output openAiEndpoint string = openAi.properties.endpoint
output openAiId string = openAi.id

output speechName string = speech.name
output speechEndpoint string = speech.properties.endpoint
output speechId string = speech.id

output documentIntelligenceName string = documentIntelligence.name
output documentIntelligenceEndpoint string = documentIntelligence.properties.endpoint
output documentIntelligenceId string = documentIntelligence.id

output searchName string = search.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchId string = search.id
