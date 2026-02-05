using './main.bicep'

// Default parameters for certaudio infrastructure
param location = 'centralus'

// AI Foundry location (must be a supported region for Standard Agent Setup)
// swedencentral is a primary AI region with full feature support
param foundryLocation = 'swedencentral'

// Study Partner: Enable AI Foundry Agent with AI Search for RAG-powered chat (~$75+/month)
// Set to true to deploy AI Search + AI Foundry Agent Service
param enableStudyPartner = false
