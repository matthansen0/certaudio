#!/usr/bin/env bash
# Index content for Study Partner without generating audio (saves TTS tokens)
#
# Usage:
#   ./scripts/index-content.sh <certification-id> [index-name]
#
# Examples:
#   ./scripts/index-content.sh dp-700                    # Index into "dp-700-content"
#   ./scripts/index-content.sh dp-700 certification-content  # Index into combined index
#   ./scripts/index-content.sh ai-102 certification-content  # Add AI-102 to combined index
#
# This script:
#   1. Resolves the persistent AI Search service (deployed with enableStudyPartner=true)
#   2. Runs content discovery
#   3. Indexes content into Azure AI Search
#   4. Does NOT generate audio or use TTS
#
# Prerequisites:
#   - Azure CLI logged in (az login)
#   - Infrastructure deployed with enableStudyPartner=true

set -euo pipefail

CERT_ID="${1:?Usage: $0 <certification-id> [index-name]}"
INDEX_NAME="${2:-}"

# Optional: load environment variables from .env.local or .env at repo root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f ".env.local" ]]; then
    set -a
    # shellcheck disable=SC1091
    source ".env.local"
    set +a
elif [[ -f ".env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source ".env"
    set +a
fi

# Configuration
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-certaudio-dev}"

echo "=========================================="
echo "CertAudio Content Indexing (No Audio)"
echo "=========================================="
echo "Certification: $CERT_ID"
echo "Index Name:    ${INDEX_NAME:-${CERT_ID}-content}"
echo "Resource Group: $RESOURCE_GROUP"
echo "=========================================="

# Get subscription ID
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

# Resolve service endpoints
echo ""
echo "Resolving service endpoints..."
OPENAI_NAME=$(az cognitiveservices account list -g "$RESOURCE_GROUP" --query "[?kind=='OpenAI'].name | [0]" -o tsv)
SEARCH_NAME=$(az search service list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv 2>/dev/null || echo "")

if [[ -z "$SEARCH_NAME" ]]; then
    echo "ERROR: No AI Search service found in $RESOURCE_GROUP"
    echo "Make sure infrastructure is deployed with enableStudyPartner=true"
    exit 1
fi

export OPENAI_ENDPOINT=$(az cognitiveservices account show -n "$OPENAI_NAME" -g "$RESOURCE_GROUP" --query properties.endpoint -o tsv)
export SEARCH_ENDPOINT="https://${SEARCH_NAME}.search.windows.net"

echo "  OpenAI: $OPENAI_ENDPOINT"
echo "  Search: $SEARCH_ENDPOINT"

# Determine current principal for RBAC
CURRENT_PRINCIPAL_OBJECT_ID=""
ACCOUNT_TYPE=$(az account show --query user.type -o tsv 2>/dev/null || echo "")
ACCOUNT_NAME=$(az account show --query user.name -o tsv 2>/dev/null || echo "")

if [[ "$ACCOUNT_TYPE" == "user" ]]; then
    CURRENT_PRINCIPAL_OBJECT_ID=$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)
elif [[ "$ACCOUNT_TYPE" == "servicePrincipal" && -n "$ACCOUNT_NAME" ]]; then
    CURRENT_PRINCIPAL_OBJECT_ID=$(az ad sp show --id "$ACCOUNT_NAME" --query id -o tsv 2>/dev/null || true)
fi

# Grant Search RBAC if possible
if [[ -n "$CURRENT_PRINCIPAL_OBJECT_ID" ]]; then
    echo ""
    echo "Checking Search RBAC..."
    SEARCH_ID=$(az search service show -n "$SEARCH_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)
    
    existing_role=$(az role assignment list \
        --scope "$SEARCH_ID" \
        --assignee "$CURRENT_PRINCIPAL_OBJECT_ID" \
        --query "[?roleDefinitionName=='Search Index Data Contributor'] | length(@)" \
        -o tsv 2>/dev/null || echo "0")
    
    if [[ "$existing_role" == "0" ]]; then
        echo "  Granting Search Index Data Contributor..."
        if az role assignment create \
            --assignee-object-id "$CURRENT_PRINCIPAL_OBJECT_ID" \
            --assignee-principal-type User \
            --role "Search Index Data Contributor" \
            --scope "$SEARCH_ID" \
            -o none 2>/dev/null; then
            echo "  Role assigned. Waiting 30s for propagation..."
            sleep 30
        else
            echo "  Could not assign role (may need Owner/RBAC Admin)"
        fi
    else
        echo "  Search Index Data Contributor role already assigned"
    fi
fi

# Grant OpenAI RBAC
if [[ -n "$CURRENT_PRINCIPAL_OBJECT_ID" ]]; then
    echo ""
    echo "Checking OpenAI RBAC..."
    OPENAI_ID=$(az cognitiveservices account show -n "$OPENAI_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)
    
    existing_openai=$(az role assignment list \
        --scope "$OPENAI_ID" \
        --assignee "$CURRENT_PRINCIPAL_OBJECT_ID" \
        --query "[?roleDefinitionName=='Cognitive Services OpenAI User'] | length(@)" \
        -o tsv 2>/dev/null || echo "0")
    
    if [[ "$existing_openai" == "0" ]]; then
        echo "  Granting Cognitive Services OpenAI User..."
        if az role assignment create \
            --assignee-object-id "$CURRENT_PRINCIPAL_OBJECT_ID" \
            --assignee-principal-type User \
            --role "Cognitive Services OpenAI User" \
            --scope "$OPENAI_ID" \
            -o none 2>/dev/null; then
            echo "  Role assigned."
        else
            echo "  Could not assign role (may need Owner/RBAC Admin)"
        fi
    else
        echo "  Cognitive Services OpenAI User role already assigned"
    fi
fi

# Run the indexing pipeline
cd "$REPO_ROOT/src/pipeline"

echo ""
echo "=========================================="
echo "PHASE 1: Content Discovery"
echo "=========================================="

DISCOVERY_FILE=$(mktemp)

if [[ "$CERT_ID" == "test-cert" || "$CERT_ID" == "test" ]]; then
    python3 -m tools.deep_discover --test --output-file "$DISCOVERY_FILE"
else
    # Combined strategy (learning paths + exam skills)
    python3 -m tools.deep_discover \
        --certification-id "$CERT_ID" \
        --comprehensive \
        --output-file "$DISCOVERY_FILE"
fi

SOURCE_URLS=$(cat "$DISCOVERY_FILE" | jq -c '.sourceUrls')
rm -f "$DISCOVERY_FILE"

echo ""
echo "=========================================="
echo "PHASE 2: Content Indexing"
echo "=========================================="

INDEX_ARGS="--certification-id $CERT_ID --source-urls '$SOURCE_URLS'"
if [[ -n "$INDEX_NAME" ]]; then
    INDEX_ARGS="$INDEX_ARGS --index-name $INDEX_NAME"
    echo "Indexing into custom index: $INDEX_NAME"
else
    echo "Indexing into default index: ${CERT_ID}-content"
fi

# Run indexing with retry
index_rc=0
eval python3 -m tools.index_content $INDEX_ARGS || index_rc=$?

if [[ $index_rc -ne 0 ]]; then
    echo "Indexing failed (exit=$index_rc). Retrying with backoff..."
    for backoff in 15 30 60; do
        sleep "$backoff"
        eval python3 -m tools.index_content $INDEX_ARGS && index_rc=0 && break
    done
fi

if [[ $index_rc -ne 0 ]]; then
    echo "ERROR: Indexing failed after retries."
    exit $index_rc
fi

echo ""
echo "=========================================="
echo "✅ Indexing Complete"
echo "=========================================="
echo "Certification: $CERT_ID"
echo "Index: ${INDEX_NAME:-${CERT_ID}-content}"
echo ""
echo "Content is now available for Study Partner queries."
echo "Note: No audio was generated (use run-local.sh for full generation)."
