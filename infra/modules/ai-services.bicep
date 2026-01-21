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
@description('Location for Azure Speech (HD voices only in eastus, westeurope, southeastasia)')
param speechLocation string = 'eastus'
@description('Optional AAD object ID of an automation principal (e.g., GitHub OIDC service principal) that runs content-generation workflows. If provided, it is granted Azure AI Search Index Data Contributor on the Search service.')
param automationPrincipalId string = ''
// ============================================================================
// VARIABLES
// ============================================================================

var openAiName = '${resourcePrefix}-openai-${uniqueSuffix}'
var speechName = '${resourcePrefix}-speech-${uniqueSuffix}'
var docIntelName = '${resourcePrefix}-docintel-${uniqueSuffix}'
// Note: searchName removed - AI Search is now deployed separately as ephemeral resource

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
    // Keep this conservative to avoid subscription TPM quota validation failures.
    capacity: 10
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
// Deployed to speechLocation (eastus by default) for HD voice support
resource speech 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: speechName
  location: speechLocation
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

// NOTE: Azure AI Search is deployed separately as an ephemeral resource
// during content generation to save ~$250/month. See search-ephemeral.bicep.

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

// Data-plane RBAC: allow automation principal to synthesize speech.
// Note: The broader "Cognitive Services User" role is assigned via CLI in generate-content.yml
// to enable issueToken for voice preflight validation. Bicep role assignments are kept minimal
// to avoid RoleAssignmentUpdateNotPermitted errors on redeployment.
resource speechUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (automationPrincipalId != '') {
  name: guid(speech.id, automationPrincipalId, 'Cognitive Services Speech User')
  scope: speech
  properties: {
    // Built-in role: Cognitive Services Speech User
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'f2dc8367-1007-4938-bd23-fe263f013447')
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
output speechRegion string = speechLocation
output speechId string = speech.id

output documentIntelligenceName string = documentIntelligence.name
output documentIntelligenceEndpoint string = documentIntelligence.properties.endpoint
output documentIntelligenceId string = documentIntelligence.id

// Search endpoint placeholder - actual value comes from ephemeral deployment
output searchEndpoint string = ''
