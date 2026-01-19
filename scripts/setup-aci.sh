#!/usr/bin/env bash
# Setup Azure Container Instance infrastructure for CertAudio
# This creates all resources needed to run generation jobs in ACI
#
# Usage:
#   ./scripts/setup-aci.sh
#
# Prerequisites:
#   - Azure CLI logged in
#   - Existing CertAudio infrastructure (run deploy-infra.yml first)

set -euo pipefail

# Configuration
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-certaudio-dev}"
LOCATION="${AZURE_LOCATION:-centralus}"
IDENTITY_NAME="certaudio-aci-runner"
ACR_NAME=""  # Will be auto-generated

echo "=========================================="
echo "CertAudio ACI Setup"
echo "=========================================="
echo "Resource Group: $RESOURCE_GROUP"
echo "Location:       $LOCATION"
echo "=========================================="

# Get subscription ID
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
echo "Subscription:   $SUBSCRIPTION_ID"

# Find existing resources
echo ""
echo "Finding existing resources..."
OPENAI_NAME=$(az cognitiveservices account list -g "$RESOURCE_GROUP" --query "[?kind=='OpenAI'].name | [0]" -o tsv)
SPEECH_NAME=$(az cognitiveservices account list -g "$RESOURCE_GROUP" --query "[?kind=='SpeechServices'].name | [0]" -o tsv)
DOCINTEL_NAME=$(az cognitiveservices account list -g "$RESOURCE_GROUP" --query "[?kind=='FormRecognizer'].name | [0]" -o tsv)
COSMOS_NAME=$(az cosmosdb list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv)
STORAGE_NAME=$(az storage account list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv)
SEARCH_NAME=$(az search service list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv 2>/dev/null || echo "")

echo "  OpenAI:    $OPENAI_NAME"
echo "  Speech:    $SPEECH_NAME"
echo "  DocIntel:  $DOCINTEL_NAME"
echo "  Cosmos:    $COSMOS_NAME"
echo "  Storage:   $STORAGE_NAME"
echo "  Search:    ${SEARCH_NAME:-<ephemeral>}"

# Create Azure Container Registry
echo ""
echo "Creating Azure Container Registry..."
# ACR names must be globally unique, alphanumeric only
ACR_NAME="acr${STORAGE_NAME//-/}"
ACR_NAME="${ACR_NAME:0:50}"  # Max 50 chars

if az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" &>/dev/null; then
    echo "  ACR already exists: $ACR_NAME"
else
    az acr create \
        --name "$ACR_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        --sku Basic \
        --admin-enabled false \
        -o none
    echo "  Created: $ACR_NAME"
fi

ACR_LOGIN_SERVER=$(az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" --query loginServer -o tsv)
ACR_ID=$(az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)
echo "  Login Server: $ACR_LOGIN_SERVER"

# Create User-Assigned Managed Identity
echo ""
echo "Creating Managed Identity..."
if az identity show -n "$IDENTITY_NAME" -g "$RESOURCE_GROUP" &>/dev/null; then
    echo "  Identity already exists: $IDENTITY_NAME"
else
    az identity create \
        --name "$IDENTITY_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        -o none
    echo "  Created: $IDENTITY_NAME"
fi

IDENTITY_ID=$(az identity show -n "$IDENTITY_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)
IDENTITY_PRINCIPAL_ID=$(az identity show -n "$IDENTITY_NAME" -g "$RESOURCE_GROUP" --query principalId -o tsv)
IDENTITY_CLIENT_ID=$(az identity show -n "$IDENTITY_NAME" -g "$RESOURCE_GROUP" --query clientId -o tsv)
echo "  Principal ID: $IDENTITY_PRINCIPAL_ID"
echo "  Client ID:    $IDENTITY_CLIENT_ID"

# Wait for identity to propagate
echo ""
echo "Waiting for identity to propagate in Azure AD..."
sleep 30

# Assign RBAC roles
echo ""
echo "Assigning RBAC roles..."

assign_role() {
    local role="$1"
    local scope="$2"
    local name="$3"
    
    if az role assignment list --assignee "$IDENTITY_PRINCIPAL_ID" --scope "$scope" --query "[?roleDefinitionName=='$role']" -o tsv | grep -q .; then
        echo "  ✓ $name: $role (exists)"
    else
        az role assignment create \
            --assignee "$IDENTITY_PRINCIPAL_ID" \
            --role "$role" \
            --scope "$scope" \
            -o none 2>/dev/null || echo "  ⚠ $name: $role (may already exist)"
        echo "  ✓ $name: $role"
    fi
}

# ACR - pull images
assign_role "AcrPull" "$ACR_ID" "ACR"

# OpenAI - generate content
OPENAI_ID=$(az cognitiveservices account show -n "$OPENAI_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)
assign_role "Cognitive Services OpenAI User" "$OPENAI_ID" "OpenAI"

# Speech - TTS
SPEECH_ID=$(az cognitiveservices account show -n "$SPEECH_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)
assign_role "Cognitive Services Speech User" "$SPEECH_ID" "Speech"

# Document Intelligence - parse docs
DOCINTEL_ID=$(az cognitiveservices account show -n "$DOCINTEL_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)
assign_role "Cognitive Services User" "$DOCINTEL_ID" "DocIntel"

# Cosmos DB - read/write episodes
COSMOS_ID=$(az cosmosdb show -n "$COSMOS_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)
assign_role "Cosmos DB Built-in Data Contributor" "$COSMOS_ID" "Cosmos"

# Storage - upload blobs
STORAGE_ID=$(az storage account show -n "$STORAGE_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)
assign_role "Storage Blob Data Contributor" "$STORAGE_ID" "Storage"

# Search - index content (scope to resource group since search is ephemeral)
RG_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP"
assign_role "Search Index Data Contributor" "$RG_ID" "Search (RG scope)"
assign_role "Search Service Contributor" "$RG_ID" "Search Admin (RG scope)"

# Build and push Docker image
echo ""
echo "Building and pushing Docker image..."
az acr build \
    --registry "$ACR_NAME" \
    --image certaudio:latest \
    --file Dockerfile \
    .

# Output configuration
echo ""
echo "=========================================="
echo "ACI SETUP COMPLETE"
echo "=========================================="
echo ""
echo "Configuration:"
echo "  ACR_LOGIN_SERVER=$ACR_LOGIN_SERVER"
echo "  IDENTITY_NAME=$IDENTITY_NAME"
echo "  IDENTITY_ID=$IDENTITY_ID"
echo "  IDENTITY_CLIENT_ID=$IDENTITY_CLIENT_ID"
echo ""
echo "To run a generation job:"
echo "  ./scripts/run-aci-job.sh dp-700 comprehensive instructional"
echo ""
echo "Image: $ACR_LOGIN_SERVER/certaudio:latest"
echo ""
