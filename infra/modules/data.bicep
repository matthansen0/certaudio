// Data Module
// Deploys: Azure Cosmos DB, Azure Storage Account

// ============================================================================
// PARAMETERS
// ============================================================================

param resourcePrefix string
param location string
param uniqueSuffix string
param tags object
@description('Optional AAD object ID of an automation principal (e.g., GitHub OIDC service principal) that runs content-generation workflows. If provided, it is granted Storage Blob Data Contributor on the storage account.')
param automationPrincipalId string = ''

// ============================================================================
// VARIABLES
// ============================================================================

var cosmosDbName = '${resourcePrefix}-cosmos-${uniqueSuffix}'
// Storage account names: 3-24 chars, lowercase alphanumeric only
// Format: certaudio{env}st{uniqueSuffix} - always > 3 chars with our prefix
var storageNameRaw = toLower(replace('${resourcePrefix}st${uniqueSuffix}', '-', ''))
#disable-next-line BCP334
var storageAccountName = take(storageNameRaw, 24)
var databaseName = 'certaudio'

// ============================================================================
// RESOURCES
// ============================================================================

// Azure Cosmos DB Account (serverless)
resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosDbName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    // Tenant policy enforces disableLocalAuth=true (modify effect).
    // Set it explicitly to avoid policy-driven drift and surprises.
    disableLocalAuth: true
    // Required for the Function App to reach Cosmos DB over the public endpoint.
    // For production, prefer Private Endpoints + VNet integration instead.
    publicNetworkAccess: 'Enabled'
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    enableFreeTier: false
  }
}

// Cosmos DB Database
resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmosDb
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

// Container: episodes
resource episodesContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'episodes'
  properties: {
    resource: {
      id: 'episodes'
      partitionKey: {
        paths: ['/certificationId']
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/"_etag"/?' }
        ]
      }
    }
  }
}

// Container: sources
resource sourcesContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'sources'
  properties: {
    resource: {
      id: 'sources'
      partitionKey: {
        paths: ['/certificationId']
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/"_etag"/?' }
        ]
      }
    }
  }
}

// Container: userProgress
resource userProgressContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: database
  name: 'userProgress'
  properties: {
    resource: {
      id: 'userProgress'
      partitionKey: {
        paths: ['/userId']
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/"_etag"/?' }
        ]
      }
    }
  }
}

// Storage Account
// NOTE: Tenant policy enforces allowSharedKeyAccess=false and allowBlobPublicAccess=false via modify effects.
// Set these explicitly to avoid policy-driven drift.
// publicNetworkAccess must be Enabled for GitHub Actions runners to access the storage account.
#disable-next-line BCP334
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    // Tenant policy enforces allowSharedKeyAccess=false (modify effect)
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    // Required for GitHub Actions access - runners are public
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// Blob service
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    cors: {
      corsRules: []
    }
  }
}

// Container for audio content - uses path prefixes: {certificationId}/{audioFormat}/episodes/
// Container names must be 3-63 characters, lowercase letters, numbers, and hyphens
resource audioContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'audio'
  properties: {
    publicAccess: 'None'
  }
}

// Container for scripts and metadata - uses path prefixes: {certificationId}/{audioFormat}/
resource scriptsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'scripts'
  properties: {
    publicAccess: 'None'
  }
}

// Data-plane RBAC: allow automation principal to upload/download blobs.
resource storageBlobDataContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (automationPrincipalId != '') {
  name: guid(storageAccount.id, automationPrincipalId, 'Storage Blob Data Contributor')
  scope: storageAccount
  properties: {
    // Built-in role: Storage Blob Data Contributor
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: automationPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// OUTPUTS
// ============================================================================

output cosmosDbAccountName string = cosmosDb.name
output cosmosDbEndpoint string = cosmosDb.properties.documentEndpoint
output cosmosDbId string = cosmosDb.id
output cosmosDbDatabaseName string = database.name

output storageAccountName string = storageAccount.name
output storageAccountId string = storageAccount.id
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
output audioContainerName string = audioContainer.name
output scriptsContainerName string = scriptsContainer.name
