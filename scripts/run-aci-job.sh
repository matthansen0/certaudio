#!/usr/bin/env bash
# Run content generation as an Azure Container Instance job
# This avoids GitHub Actions' 6-hour timeout and token expiration issues
#
# Usage:
#   ./scripts/run-aci-job.sh dp-700 [comprehensive|deep|quick] [instructional|podcast-solo|podcast-duo]
#
# Examples:
#   ./scripts/run-aci-job.sh dp-700                           # Defaults: comprehensive, instructional
#   ./scripts/run-aci-job.sh az-104 deep podcast-duo          # AZ-104 deep discovery, podcast format
#
# Prerequisites:
#   - Run ./scripts/setup-aci.sh first (one-time setup)
#   - Azure CLI logged in

set -euo pipefail

CERT_ID="${1:?Usage: $0 <certification-id> [discovery-mode] [audio-format]}"
DISCOVERY_MODE="${2:-comprehensive}"
AUDIO_FORMAT="${3:-instructional}"

# Configuration
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-certaudio-dev}"
LOCATION="${AZURE_LOCATION:-centralus}"
IDENTITY_NAME="certaudio-aci-runner"

# Generate unique job name
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
JOB_NAME="certaudio-${CERT_ID}-${TIMESTAMP}"

echo "=========================================="
echo "CertAudio ACI Job Runner"
echo "=========================================="
echo "Certification:   $CERT_ID"
echo "Discovery Mode:  $DISCOVERY_MODE"
echo "Audio Format:    $AUDIO_FORMAT"
echo "Job Name:        $JOB_NAME"
echo "=========================================="

# Get subscription ID
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

# Get managed identity
echo "Resolving managed identity..."
IDENTITY_ID=$(az identity show \
  --name "$IDENTITY_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query id -o tsv 2>/dev/null) || {
    echo "ERROR: Managed identity '$IDENTITY_NAME' not found."
    echo "Run ./scripts/setup-aci.sh first."
    exit 1
}

IDENTITY_CLIENT_ID=$(az identity show \
  --name "$IDENTITY_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query clientId -o tsv)

# Get ACR and image
echo "Resolving container registry..."
STORAGE_NAME=$(az storage account list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv)
ACR_NAME="acr${STORAGE_NAME//-/}"
ACR_NAME="${ACR_NAME:0:50}"
ACR_LOGIN_SERVER=$(az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" --query loginServer -o tsv 2>/dev/null) || {
    echo "ERROR: Container registry '$ACR_NAME' not found."
    echo "Run ./scripts/setup-aci.sh first."
    exit 1
}
CONTAINER_IMAGE="$ACR_LOGIN_SERVER/certaudio:latest"

# Get service endpoints
echo "Resolving service endpoints..."
OPENAI_NAME=$(az cognitiveservices account list -g "$RESOURCE_GROUP" --query "[?kind=='OpenAI'].name | [0]" -o tsv)
SPEECH_NAME=$(az cognitiveservices account list -g "$RESOURCE_GROUP" --query "[?kind=='SpeechServices'].name | [0]" -o tsv)
COSMOS_NAME=$(az cosmosdb list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv)

OPENAI_ENDPOINT=$(az cognitiveservices account show -n "$OPENAI_NAME" -g "$RESOURCE_GROUP" --query properties.endpoint -o tsv)
SPEECH_ENDPOINT=$(az cognitiveservices account show -n "$SPEECH_NAME" -g "$RESOURCE_GROUP" --query properties.endpoint -o tsv)
SPEECH_REGION=$(az cognitiveservices account show -n "$SPEECH_NAME" -g "$RESOURCE_GROUP" --query location -o tsv)
COSMOS_ENDPOINT=$(az cosmosdb show -n "$COSMOS_NAME" -g "$RESOURCE_GROUP" --query documentEndpoint -o tsv)

# Check for existing search or note it will be created
SEARCH_NAME=$(az search service list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv 2>/dev/null || echo "")
if [[ -n "$SEARCH_NAME" ]]; then
    SEARCH_ENDPOINT="https://${SEARCH_NAME}.search.windows.net"
else
    echo "NOTE: No AI Search found. The job will create an ephemeral one."
    SEARCH_ENDPOINT=""
fi

echo ""
echo "Configuration:"
echo "  Image:    $CONTAINER_IMAGE"
echo "  Identity: $IDENTITY_NAME"
echo "  OpenAI:   $OPENAI_ENDPOINT"
echo "  Speech:   $SPEECH_ENDPOINT ($SPEECH_REGION)"
echo "  Cosmos:   $COSMOS_ENDPOINT"
echo "  Storage:  $STORAGE_NAME"
echo "  Search:   ${SEARCH_ENDPOINT:-<will create>}"

echo ""
echo "Creating container instance..."
az container create \
  --name "$JOB_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --image "$CONTAINER_IMAGE" \
  --registry-login-server "$ACR_LOGIN_SERVER" \
  --acr-identity "$IDENTITY_ID" \
  --assign-identity "$IDENTITY_ID" \
  --cpu 1 \
  --memory 2 \
  --restart-policy Never \
  --environment-variables \
    AZURE_CLIENT_ID="$IDENTITY_CLIENT_ID" \
    AZURE_SUBSCRIPTION_ID="$SUBSCRIPTION_ID" \
    AZURE_RESOURCE_GROUP="$RESOURCE_GROUP" \
    OPENAI_ENDPOINT="$OPENAI_ENDPOINT" \
    SPEECH_ENDPOINT="$SPEECH_ENDPOINT" \
    SPEECH_REGION="$SPEECH_REGION" \
    COSMOS_DB_ENDPOINT="$COSMOS_ENDPOINT" \
    STORAGE_ACCOUNT_NAME="$STORAGE_NAME" \
    SEARCH_ENDPOINT="$SEARCH_ENDPOINT" \
    CERTIFICATION_ID="$CERT_ID" \
    AUDIO_FORMAT="$AUDIO_FORMAT" \
    DISCOVERY_MODE="$DISCOVERY_MODE" \
    TTS_MAX_WORKERS="10" \
  --command-line "python3 -m tools.generate_all --certification-id $CERT_ID --audio-format $AUDIO_FORMAT --discovery-mode $DISCOVERY_MODE" \
  -o none

echo ""
echo "=========================================="
echo "Job started: $JOB_NAME"
echo "=========================================="
echo ""
echo "Track progress:"
echo "  az container logs -n $JOB_NAME -g $RESOURCE_GROUP --follow"
echo ""
echo "Check status:"
echo "  az container show -n $JOB_NAME -g $RESOURCE_GROUP --query instanceView.state -o tsv"
echo ""
echo "Delete when done:"
echo "  az container delete -n $JOB_NAME -g $RESOURCE_GROUP -y"
echo ""

# Ask if user wants to follow logs
read -p "Follow logs now? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    az container logs -n "$JOB_NAME" -g "$RESOURCE_GROUP" --follow
fi
