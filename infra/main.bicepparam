using './main.bicep'

// Default parameters for certaudio infrastructure
param location = 'centralus'

// AI Foundry location (must be a supported region for Standard Agent Setup)
// eastus is the closest supported region to centralus
param foundryLocation = 'eastus'

// Study Partner: Enable AI Foundry Agent with AI Search for RAG-powered chat (~$75+/month)
// Set to true to deploy AI Search + AI Foundry Agent Service
param enableStudyPartner = false
