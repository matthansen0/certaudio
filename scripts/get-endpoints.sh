#!/usr/bin/env bash
set -euo pipefail

# Prints the resource endpoints/names needed by GitHub Actions workflows (Generate/Refresh Content).
# Optionally sets them as GitHub repo secrets if you export SET_GH_SECRETS=true and have `gh` auth.
#
# Usage:
#   ./scripts/get-endpoints.sh <resource-group> [suffix]
#
# Examples:
#   ./scripts/get-endpoints.sh rg-certaudio-dev
#   ./scripts/get-endpoints.sh rg-certaudio-dev 21076443897
#   SET_GH_SECRETS=true ./scripts/get-endpoints.sh rg-certaudio-dev 21076443897

rg="${1:-${AZURE_RESOURCE_GROUP:-}}"
if [[ -z "$rg" ]]; then
  echo "Usage: $0 <resource-group> [suffix]" >&2
  exit 2
fi

suffix="${2:-}"
if [[ -z "$suffix" ]]; then
  suffix="${AZURE_UNIQUE_SUFFIX:-${UNIQUE_SUFFIX:-}}"
fi

if [[ -z "$suffix" ]]; then
  # Choose newest deployment suffix by max SWA numeric suffix.
  suffix=$(az staticwebapp list -g "$rg" --query "[].name" -o tsv \
    | sed -n 's/^certaudio-dev-swa-//p' \
    | sort -n \
    | tail -n 1)
fi

if [[ -z "$suffix" ]]; then
  echo "Could not determine suffix (no certaudio-dev-swa-* found in RG $rg)." >&2
  exit 1
fi

short="${suffix:0:10}"

openai="certaudio-dev-openai-$suffix"
speech="certaudio-dev-speech-$suffix"
docintel="certaudio-dev-docintel-$suffix"
cosmos="certaudio-dev-cosmos-$suffix"

# Storage account names are truncated to 24 chars in Bicep.
# For data storage account, the pattern is: certaudio{env}st{shortSuffix}
storage="certaudiodevst$short"

OPENAI_ENDPOINT=$(az cognitiveservices account show -g "$rg" -n "$openai" --query properties.endpoint -o tsv)
SPEECH_ENDPOINT=$(az cognitiveservices account show -g "$rg" -n "$speech" --query properties.endpoint -o tsv)
SPEECH_REGION=$(az cognitiveservices account show -g "$rg" -n "$speech" --query location -o tsv)
DOCUMENT_INTELLIGENCE_ENDPOINT=$(az cognitiveservices account show -g "$rg" -n "$docintel" --query properties.endpoint -o tsv)
COSMOS_DB_ENDPOINT=$(az cosmosdb show -g "$rg" -n "$cosmos" --query documentEndpoint -o tsv)
# Note: AI Search is now ephemeral (deployed only during content generation)
# The SEARCH_ENDPOINT is constructed dynamically in workflows, not fetched here

cat <<EOF
RG=$rg
SUFFIX=$suffix
SHORT_SUFFIX=$short

OPENAI_ENDPOINT=$OPENAI_ENDPOINT
SPEECH_ENDPOINT=$SPEECH_ENDPOINT
SPEECH_REGION=$SPEECH_REGION
DOCUMENT_INTELLIGENCE_ENDPOINT=$DOCUMENT_INTELLIGENCE_ENDPOINT
COSMOS_DB_ENDPOINT=$COSMOS_DB_ENDPOINT
STORAGE_ACCOUNT_NAME=$storage
EOF

if [[ "${SET_GH_SECRETS:-false}" == "true" ]]; then
  if ! command -v gh >/dev/null 2>&1; then
    echo "gh not found; cannot set GitHub secrets." >&2
    exit 1
  fi

  echo ""
  echo "Setting GitHub secrets in the current repo via gh..."
  gh secret set OPENAI_ENDPOINT -b"$OPENAI_ENDPOINT"
  gh secret set SPEECH_ENDPOINT -b"$SPEECH_ENDPOINT"
  gh secret set DOCUMENT_INTELLIGENCE_ENDPOINT -b"$DOCUMENT_INTELLIGENCE_ENDPOINT"
  gh secret set COSMOS_DB_ENDPOINT -b"$COSMOS_DB_ENDPOINT"
  gh secret set STORAGE_ACCOUNT_NAME -b"$storage"
  # Note: SEARCH_ENDPOINT is not set here - AI Search is ephemeral
  echo "Done."
fi
