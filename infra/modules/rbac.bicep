// Data-plane RBAC for the Functions managed identity.
//
// These assignments used to be made with `az role assignment create` in the
// deploy workflow, which meant a deployment was only actually complete if CI
// ran. Declaring them here keeps `azd up` self-sufficient and makes the
// permissions reviewable alongside the resources they apply to.
//
// Everything is keyed on a deterministic guid(), so redeploying is a no-op
// rather than a conflict.

@description('Principal ID of the Functions app managed identity')
param functionsPrincipalId string

@description('Storage account holding episode audio, scripts and the published index')
param dataStorageAccountName string

@description('Storage account backing the Functions runtime and the content-jobs queue')
param funcStorageAccountName string

@description('Cosmos DB account name')
param cosmosDbAccountName string

@description('Cosmos DB database name')
param cosmosDbDatabaseName string

@description('Azure OpenAI account name')
param openAiName string

@description('Azure Speech account name')
param speechName string

@description('Azure AI Search service name')
param searchName string

// ---------------------------------------------------------------- role IDs
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var storageQueueDataContributor = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
var storageTableDataContributor = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var cognitiveServicesOpenAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var cognitiveServicesUser = 'a97b65f3-24c7-4388-baec-2e87135dc908'
var searchIndexDataContributor = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
// Cosmos uses its own RBAC system; this is the built-in data-plane contributor.
var cosmosSqlDataContributor = '00000000-0000-0000-0000-000000000002'

// -------------------------------------------------------- existing resources
resource dataStorage 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: dataStorageAccountName
}

resource funcStorage 'Microsoft.Storage/storageAccounts@2023-01-01' existing = {
  name: funcStorageAccountName
}

resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosDbAccountName
}

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openAiName
}

resource speech 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: speechName
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchName
}

// ------------------------------------------------------------- data storage
// Contributor, not Reader: generation writes episode audio, scripts and the
// published index to this account as well as streaming them back to players.
resource dataBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: dataStorage
  name: guid(dataStorage.id, functionsPrincipalId, storageBlobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
    principalId: functionsPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// --------------------------------------------------------- Functions storage
// The runtime uses identity-based access (AzureWebJobsStorage__credential),
// and the queue trigger for content-jobs needs the queue role specifically.
resource funcBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: funcStorage
  name: guid(funcStorage.id, functionsPrincipalId, storageBlobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
    principalId: functionsPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource funcQueueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: funcStorage
  name: guid(funcStorage.id, functionsPrincipalId, storageQueueDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageQueueDataContributor)
    principalId: functionsPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource funcTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: funcStorage
  name: guid(funcStorage.id, functionsPrincipalId, storageTableDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageTableDataContributor)
    principalId: functionsPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ------------------------------------------------------------- AI services
resource openAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: openAi
  name: guid(openAi.id, functionsPrincipalId, cognitiveServicesOpenAiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAiUser)
    principalId: functionsPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Speech has no dedicated TTS role; Cognitive Services User covers synthesis.
resource speechUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: speech
  name: guid(speech.id, functionsPrincipalId, cognitiveServicesUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUser)
    principalId: functionsPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Contributor because the Function writes the grounding index during generation
// and reads it back for Study Partner retrieval.
resource searchContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, functionsPrincipalId, searchIndexDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributor)
    principalId: functionsPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ------------------------------------------------------------------ Cosmos
// Cosmos SQL RBAC is a separate system from Azure RBAC and expects a
// fully-qualified data-plane scope ending in /dbs/<database>.
resource cosmosDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = {
  parent: cosmosDb
  name: guid(cosmosDb.id, functionsPrincipalId, cosmosSqlDataContributor)
  properties: {
    roleDefinitionId: '${cosmosDb.id}/sqlRoleDefinitions/${cosmosSqlDataContributor}'
    principalId: functionsPrincipalId
    scope: '${cosmosDb.id}/dbs/${cosmosDbDatabaseName}'
  }
}
