// Web Module
// Deploys: Azure Static Web Apps, Azure Functions

// ============================================================================
// PARAMETERS
// ============================================================================

param resourcePrefix string
param location string
param uniqueSuffix string
param storageAccountName string
param cosmosDbAccountName string
param cosmosDbDatabaseName string
@description('Optional AAD object ID of an automation principal (e.g., GitHub OIDC service principal) that should have Cosmos SQL Data Contributor permissions at the database scope.')
param automationPrincipalId string = ''
param openAiEndpoint string
param speechEndpoint string
@description('AI Search endpoint - optional since Search is deployed ephemerally during content generation')
param searchEndpoint string = ''
param tags object

// ============================================================================
// VARIABLES
// ============================================================================

var staticWebAppName = '${resourcePrefix}-swa-${uniqueSuffix}'
var functionsAppName = '${resourcePrefix}-func-${uniqueSuffix}'
var appServicePlanName = '${resourcePrefix}-asp-${uniqueSuffix}'
var appInsightsName = '${resourcePrefix}-insights-${uniqueSuffix}'
// Storage account names: 3-24 chars, lowercase alphanumeric only
// Format: certaudio{env}fn{uniqueSuffix} - always > 3 chars with our prefix
var funcStorageNameRaw = toLower(replace('${resourcePrefix}fn${uniqueSuffix}', '-', ''))
#disable-next-line BCP334
var funcStorageAccountName = take('${funcStorageNameRaw}000', 24)

// ============================================================================
// RESOURCES
// ============================================================================

// Application Insights for monitoring
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    Request_Source: 'rest'
    RetentionInDays: 30
  }
}

// Storage account for Functions
#disable-next-line BCP334
resource funcStorageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: funcStorageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    // Many tenants/subscriptions enforce this via policy; Functions supports managed-identity auth.
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

// App Service Plan for Functions (Basic)
// B1 plan - ~$13/mo, supports managed identity when shared key is disabled on storage
// Note: Consumption (Y1) requires shared key access for AzureWebJobsStorage which is blocked by policy
resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: location
  tags: tags
  sku: {
    name: 'B1'
    tier: 'Basic'
  }
  properties: {
    reserved: true // Linux
  }
}

// Reference to existing Cosmos DB account
resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosDbAccountName
}

// Azure Functions App
resource functionsApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionsAppName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      pythonVersion: '3.11'
      alwaysOn: true // Prevents cold starts - supported on Basic tier and above
      cors: {
        allowedOrigins: ['*']
        supportCredentials: false
      }
      appSettings: [
        // Managed-identity based AzureWebJobsStorage (no storage keys)
        {
          name: 'AzureWebJobsStorage__accountName'
          value: funcStorageAccount.name
        }
        {
          name: 'AzureWebJobsStorage__credential'
          value: 'managedidentity'
        }
        {
          name: 'AzureWebJobsStorage__blobServiceUri'
          value: 'https://${funcStorageAccount.name}.blob.${environment().suffixes.storage}'
        }
        {
          name: 'AzureWebJobsStorage__queueServiceUri'
          value: 'https://${funcStorageAccount.name}.queue.${environment().suffixes.storage}'
        }
        {
          name: 'AzureWebJobsStorage__tableServiceUri'
          value: 'https://${funcStorageAccount.name}.table.${environment().suffixes.storage}'
        }
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'python'
        }
        {
          name: 'APPINSIGHTS_INSTRUMENTATIONKEY'
          value: appInsights.properties.InstrumentationKey
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.properties.ConnectionString
        }
        {
          name: 'STORAGE_ACCOUNT_NAME'
          value: storageAccountName
        }
        {
          name: 'COSMOS_DB_ENDPOINT'
          value: cosmosDb.properties.documentEndpoint
        }
        {
          name: 'COSMOS_DB_DATABASE'
          value: cosmosDbDatabaseName
        }
        {
          name: 'OPENAI_ENDPOINT'
          value: openAiEndpoint
        }
        {
          name: 'SPEECH_ENDPOINT'
          value: speechEndpoint
        }
        {
          name: 'SEARCH_ENDPOINT'
          value: searchEndpoint
        }
        // Python v2 programming model requires worker indexing
        {
          name: 'AzureWebJobsFeatureFlags'
          value: 'EnableWorkerIndexing'
        }
        // Enable remote build so pip installs dependencies during deployment
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
      ]
    }
  }
}

// Ensure App Service Authentication/Authorization (EasyAuth) is disabled for this API.
resource functionsAuth 'Microsoft.Web/sites/config@2022-03-01' = {
  parent: functionsApp
  name: 'authsettingsV2'
  properties: {
    platform: {
      enabled: false
    }
    globalValidation: {
      requireAuthentication: false
      unauthenticatedClientAction: 'AllowAnonymous'
    }
  }
}

// NOTE: Role assignments for the Functions managed identity are created via Azure CLI
// in the deploy-infra workflow AFTER the Function App is deployed. This avoids
// Bicep idempotency issues with role assignments (RoleAssignmentUpdateNotPermitted,
// RoleAssignmentExists) that occur when the managed identity principalId changes.

// Azure Static Web Apps
resource staticWebApp 'Microsoft.Web/staticSites@2023-12-01' = {
  name: staticWebAppName
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    stagingEnvironmentPolicy: 'Enabled'
    allowConfigFileUpdates: true
    buildProperties: {
      appLocation: 'src/web'
      apiLocation: ''
      outputLocation: ''
    }
  }
}

// Link Functions to Static Web App as backend
resource staticWebAppBackend 'Microsoft.Web/staticSites/linkedBackends@2023-12-01' = {
  parent: staticWebApp
  // Static Web Apps allows only a single linked backend.
  // Use the Function App name to avoid creating a second backend.
  name: functionsApp.name
  properties: {
    backendResourceId: functionsApp.id
    region: location
  }
}

// Role assignment: Functions can read/write to Cosmos DB
// Note: Cosmos DB data-plane RBAC uses sqlRoleAssignments/sqlRoleDefinitions (not Microsoft.Authorization roleDefinitions).
var cosmosSqlDataContributorRoleDefinitionId = resourceId(
  'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions',
  cosmosDb.name,
  '00000000-0000-0000-0000-000000000002'
)

// NOTE: Cosmos role assignment for Functions MI is created via Azure CLI in the workflow
// to avoid Bicep idempotency issues with role assignments.

resource cosmosDbSqlDataContributorRoleAutomation 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = if (!empty(automationPrincipalId)) {
  parent: cosmosDb
  name: guid(cosmosDb.id, automationPrincipalId, 'sqlDataContributorAutomation')
  properties: {
    roleDefinitionId: cosmosSqlDataContributorRoleDefinitionId
    principalId: automationPrincipalId
    // Cosmos SQL RBAC expects a fully-qualified scope path (starts with /subscriptions)
    // and uses the data-plane database segment (/dbs/<dbName>).
    scope: '${cosmosDb.id}/dbs/${cosmosDbDatabaseName}'
  }
}

// ============================================================================
// OUTPUTS
// ============================================================================

output staticWebAppName string = staticWebApp.name
output staticWebAppUrl string = 'https://${staticWebApp.properties.defaultHostname}'
output staticWebAppId string = staticWebApp.id

output functionsAppName string = functionsApp.name
output functionsAppUrl string = 'https://${functionsApp.properties.defaultHostName}'
output functionsAppId string = functionsApp.id
output functionsAppPrincipalId string = functionsApp.identity.principalId
output funcStorageAccountName string = funcStorageAccount.name

output appInsightsName string = appInsights.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
