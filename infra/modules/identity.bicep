// Identity Module (Conditional)
// Deploys: Azure AD B2C configuration for Static Web Apps
// Note: B2C tenant creation requires manual steps or separate deployment

// ============================================================================
// PARAMETERS
// ============================================================================

param resourcePrefix string

@description('Name of Static Web App to configure with B2C')
param staticWebAppName string

// ============================================================================
// VARIABLES
// ============================================================================

// B2C tenant names must be globally unique and follow specific naming rules
var b2cTenantName = '${replace(resourcePrefix, '-', '')}b2c'
var b2cDisplayName = 'Certification Audio Platform'

// ============================================================================
// NOTES ON B2C DEPLOYMENT
// ============================================================================
// Azure AD B2C tenant creation cannot be fully automated via Bicep/ARM.
// The recommended approach is:
// 1. Create B2C tenant manually in Azure Portal or via Azure CLI
// 2. Configure user flows (sign-up/sign-in) in B2C Portal
// 3. Register the Static Web App as an application in B2C
// 4. Update Static Web App configuration with B2C settings
//
// This module outputs the expected configuration values that should be
// used when setting up B2C manually.

// ============================================================================
// STATIC WEB APP AUTH CONFIGURATION
// ============================================================================

// Reference to existing Static Web App
resource staticWebApp 'Microsoft.Web/staticSites@2023-12-01' existing = {
  name: staticWebAppName
}

// Static Web App custom authentication settings
// These will be configured via staticwebapp.config.json
resource authConfig 'Microsoft.Web/staticSites/config@2023-12-01' = {
  parent: staticWebApp
  name: 'appsettings'
  properties: {
    AZURE_AD_B2C_TENANT_NAME: b2cTenantName
    AZURE_AD_B2C_CLIENT_ID: 'TO_BE_CONFIGURED'
    AZURE_AD_B2C_POLICY_NAME: 'B2C_1_signupsignin'
  }
}

// ============================================================================
// OUTPUTS
// ============================================================================

output b2cTenantName string = b2cTenantName
output b2cDisplayName string = b2cDisplayName
output b2cAuthorityDomain string = '${b2cTenantName}.b2clogin.com'
output b2cSignUpSignInPolicy string = 'B2C_1_signupsignin'

output manualConfigurationRequired string = '''
Azure AD B2C requires manual configuration:

1. Create B2C Tenant:
   az ad b2c tenant create --tenant-name ${b2cTenantName} --resource-group <rg> --location <location>

2. Create User Flow in Azure Portal:
   - Go to Azure AD B2C > User flows
   - Create "Sign up and sign in" flow named "B2C_1_signupsignin"
   - Enable Email signup, Display name, Given name

3. Register Application:
   - Go to Azure AD B2C > App registrations
   - Register new app for Static Web App
   - Add redirect URI: https://<staticwebapp>.azurestaticapps.net/.auth/login/aadb2c/callback
   - Copy Client ID to AZURE_AD_B2C_CLIENT_ID setting

4. Update staticwebapp.config.json with B2C settings
'''
