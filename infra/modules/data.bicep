// Data Module
// Deploys: Azure Cosmos DB, Azure Storage Account

// ============================================================================
// PARAMETERS
// ============================================================================

param resourcePrefix string
param location string
param uniqueSuffix string
param certificationId string
param audioFormat string
param tags object

// ============================================================================
// VARIABLES
// ============================================================================

var cosmosDbName = '${resourcePrefix}-cosmos-${uniqueSuffix}'
var storageAccountName = replace('${resourcePrefix}stor${uniqueSuffix}', '-', '')
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

// Storage Account (private access only)
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
    allowSharedKeyAccess: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
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

// Container for audio content: {certificationId}/{format}/episodes/
// Container names must be 3-63 characters, lowercase letters, numbers, and hyphens
resource audioContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'audio-${certificationId}-${audioFormat}'
  properties: {
    publicAccess: 'None'
  }
}

// Container for scripts and metadata
resource scriptsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'scripts-${certificationId}-${audioFormat}'
  properties: {
    publicAccess: 'None'
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
