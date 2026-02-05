using './main.bicep'

// Default parameters for certaudio infrastructure
param location = 'canadacentral'

// Study Partner: Enable persistent AI Search for RAG-powered chat (~$75/month)
// Set to true to deploy AI Search and enable the Study Partner feature
param enableStudyPartner = false
