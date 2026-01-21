#!/usr/bin/env bash
# Run content generation locally from the dev container
#
# Usage:
#   ./scripts/run-local.sh dp-700 [instructional|podcast]
#
# Examples:
#   ./scripts/run-local.sh dp-700                           # Defaults: instructional
#   ./scripts/run-local.sh az-104 podcast                   # Podcast format
#
# Prerequisites:
#   - Azure CLI logged in (az login)
#   - Infrastructure deployed (deploy-infra.yml)
#
# This script:
#   1. Creates an ephemeral AI Search service
#   2. Runs the full generation pipeline
#   3. Optionally cleans up the Search service when done

set -euo pipefail

CERT_ID="${1:?Usage: $0 <certification-id> [audio-format]}"
AUDIO_FORMAT="${2:-instructional}"
FORCE_REGEN="${FORCE_REGENERATE:-false}"

# Optional: load environment variables from .env.local or .env at repo root
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

# Voices (can be overridden via env vars or .env.local)
# Default to Dragon HD GA voices; Preview voices (Ava3/Andrew3) only work in eastus/westeurope/southeastasia.
# See .env.example for options.
INSTRUCTIONAL_VOICE="${INSTRUCTIONAL_VOICE:-en-US-Andrew:DragonHDLatestNeural}"
PODCAST_HOST_VOICE="${PODCAST_HOST_VOICE:-en-US-Ava:DragonHDLatestNeural}"
PODCAST_EXPERT_VOICE="${PODCAST_EXPERT_VOICE:-en-US-Andrew:DragonHDLatestNeural}"

# Configuration
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-certaudio-dev}"
LOCATION="${AZURE_LOCATION:-centralus}"

# By default, keep the Search service so you can retry/resume long runs.
# Set CLEANUP_ON_EXIT=true to delete it when the script exits.
CLEANUP_ON_EXIT="${CLEANUP_ON_EXIT:-false}"

# Optional: attempt to grant Search data-plane RBAC to the current principal (best-effort).
# Recommended to keep this false and instead set SEARCH_ADMIN_KEY for local runs.
AUTO_GRANT_SEARCH_RBAC="${AUTO_GRANT_SEARCH_RBAC:-false}"

# Optional: path to a deep_discover JSON output file.
# If set, the script will skip discovery and reuse this file (useful for reruns).
DISCOVERY_RESULTS_FILE="${DISCOVERY_RESULTS_FILE:-}"

# Optional: attempt to grant Azure OpenAI data-plane role when running with Entra ID.
# This is best-effort and requires permissions to create role assignments.
AUTO_GRANT_OPENAI_RBAC="${AUTO_GRANT_OPENAI_RBAC:-true}"

# Optional: attempt to grant Azure Speech data-plane role when running with Entra ID.
AUTO_GRANT_SPEECH_RBAC="${AUTO_GRANT_SPEECH_RBAC:-true}"

# Optional: attempt to grant Storage Blob data-plane RBAC to the current principal.
AUTO_GRANT_STORAGE_RBAC="${AUTO_GRANT_STORAGE_RBAC:-true}"

echo "=========================================="
echo "CertAudio Local Generation"
echo "=========================================="
echo "Certification:   $CERT_ID"
echo "Audio Format:    $AUDIO_FORMAT"
echo "Instructional Voice: $INSTRUCTIONAL_VOICE"
echo "Podcast Host Voice:  $PODCAST_HOST_VOICE"
echo "Podcast Expert Voice:$PODCAST_EXPERT_VOICE"
echo "Force Regen:     $FORCE_REGEN"
echo "Cleanup on Exit: $CLEANUP_ON_EXIT"
echo "Auto-grant Search RBAC: $AUTO_GRANT_SEARCH_RBAC"
echo "Resource Group:  $RESOURCE_GROUP"
echo "=========================================="

if [[ -n "${SEARCH_ADMIN_KEY:-}" ]]; then
    echo "Search Auth:     SEARCH_ADMIN_KEY is set (key-based auth)"
else
    echo "Search Auth:     SEARCH_ADMIN_KEY not set (Entra ID / RBAC)"
fi

# Get subscription ID
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

# Resolve service endpoints
echo ""
echo "Resolving service endpoints..."
OPENAI_NAME=$(az cognitiveservices account list -g "$RESOURCE_GROUP" --query "[?kind=='OpenAI'].name | [0]" -o tsv)
SPEECH_NAME=$(az cognitiveservices account list -g "$RESOURCE_GROUP" --query "[?kind=='SpeechServices'].name | [0]" -o tsv)
COSMOS_NAME=$(az cosmosdb list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv)

# Prefer the data storage account (used for audio/scripts) rather than the Functions host storage.
# Allow override via STORAGE_ACCOUNT_NAME environment variable.
if [[ -z "${STORAGE_ACCOUNT_NAME:-}" ]]; then
    # In this environment the Functions storage often has publicNetworkAccess=Disabled.
    STORAGE_NAME=$(az storage account list -g "$RESOURCE_GROUP" --query "[?publicNetworkAccess!='Disabled'].name | [0]" -o tsv)
    if [[ -z "${STORAGE_NAME:-}" ]]; then
        STORAGE_NAME=$(az storage account list -g "$RESOURCE_GROUP" --query "[0].name" -o tsv)
        echo "  WARNING: Could not find a storage account with publicNetworkAccess enabled; using $STORAGE_NAME"
    fi
else
    STORAGE_NAME="${STORAGE_ACCOUNT_NAME}"
    echo "  Using STORAGE_ACCOUNT_NAME override: $STORAGE_NAME"
fi

export OPENAI_ENDPOINT=$(az cognitiveservices account show -n "$OPENAI_NAME" -g "$RESOURCE_GROUP" --query properties.endpoint -o tsv)
export SPEECH_ENDPOINT=$(az cognitiveservices account show -n "$SPEECH_NAME" -g "$RESOURCE_GROUP" --query properties.endpoint -o tsv)
export SPEECH_REGION=$(az cognitiveservices account show -n "$SPEECH_NAME" -g "$RESOURCE_GROUP" --query location -o tsv)
export SPEECH_DISABLE_LOCAL_AUTH=$(az cognitiveservices account show -n "$SPEECH_NAME" -g "$RESOURCE_GROUP" --query properties.disableLocalAuth -o tsv 2>/dev/null || echo "")
export COSMOS_DB_ENDPOINT=$(az cosmosdb show -n "$COSMOS_NAME" -g "$RESOURCE_GROUP" --query documentEndpoint -o tsv)
export STORAGE_ACCOUNT_NAME="$STORAGE_NAME"
export AZURE_SUBSCRIPTION_ID="$SUBSCRIPTION_ID"
export AZURE_RESOURCE_GROUP="$RESOURCE_GROUP"

echo "  OpenAI:  $OPENAI_ENDPOINT"
echo "  Speech:  $SPEECH_ENDPOINT ($SPEECH_REGION)"
if [[ -n "${SPEECH_DISABLE_LOCAL_AUTH:-}" ]]; then
    echo "  Speech Auth: disableLocalAuth=$SPEECH_DISABLE_LOCAL_AUTH"
fi
echo "  Cosmos:  $COSMOS_DB_ENDPOINT"
echo "  Storage: $STORAGE_ACCOUNT_NAME"

# Note: Cosmos DB account has disableLocalAuth=true, so we use Entra ID tokens (not keys).
echo "  Cosmos Auth: using Entra ID (Cosmos Native RBAC required)"

# Determine current principal object ID once (used for Cosmos/Storage/Speech RBAC grants)
# Note: in some tenants, `az account get-access-token` may return an opaque token (not a JWT),
# so we prefer Microsoft Graph lookups for the signed-in identity.
CURRENT_PRINCIPAL_OBJECT_ID=""
ACCOUNT_TYPE=$(az account show --query user.type -o tsv 2>/dev/null || echo "")
ACCOUNT_NAME=$(az account show --query user.name -o tsv 2>/dev/null || echo "")

if [[ "$ACCOUNT_TYPE" == "user" ]]; then
    CURRENT_PRINCIPAL_OBJECT_ID=$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)
    if [[ -z "$CURRENT_PRINCIPAL_OBJECT_ID" && -n "$ACCOUNT_NAME" ]]; then
        CURRENT_PRINCIPAL_OBJECT_ID=$(az ad user show --id "$ACCOUNT_NAME" --query id -o tsv 2>/dev/null || true)
    fi
elif [[ "$ACCOUNT_TYPE" == "servicePrincipal" && -n "$ACCOUNT_NAME" ]]; then
    # ACCOUNT_NAME is typically the appId for a service principal
    CURRENT_PRINCIPAL_OBJECT_ID=$(az ad sp show --id "$ACCOUNT_NAME" --query id -o tsv 2>/dev/null || true)
fi

if [[ -z "${CURRENT_PRINCIPAL_OBJECT_ID:-}" ]]; then
    echo "  WARNING: Could not resolve current principal object id; auto-grant RBAC may be skipped."
fi

if [[ -z "${ASSIGNEE_OBJECT_ID:-}" && -n "${CURRENT_PRINCIPAL_OBJECT_ID:-}" ]]; then
    # Let OpenAI/Search RBAC logic reuse this if needed
    ASSIGNEE_OBJECT_ID="$CURRENT_PRINCIPAL_OBJECT_ID"
fi

if [[ -n "$CURRENT_PRINCIPAL_OBJECT_ID" ]]; then
    COSMOS_ACCOUNT_ID=$(az cosmosdb show -n "$COSMOS_NAME" -g "$RESOURCE_GROUP" --query id -o tsv 2>/dev/null || true)
    if [[ -n "$COSMOS_ACCOUNT_ID" ]]; then
        # Check if role already assigned using Cosmos DB SQL role definitions
        # Built-in Data Contributor role ID: 00000000-0000-0000-0000-000000000002
        existing_cosmos_role=$(az cosmosdb sql role assignment list \
            --account-name "$COSMOS_NAME" \
            --resource-group "$RESOURCE_GROUP" \
            --query "[?principalId=='$CURRENT_PRINCIPAL_OBJECT_ID' && contains(roleDefinitionId, '00000000-0000-0000-0000-000000000002')] | length(@)" \
            -o tsv 2>/dev/null || echo "0")
        
        if [[ "$existing_cosmos_role" == "0" ]]; then
            echo "  Granting Cosmos DB Built-in Data Contributor role..."
            if az cosmosdb sql role assignment create \
                --account-name "$COSMOS_NAME" \
                --resource-group "$RESOURCE_GROUP" \
                --role-definition-id "00000000-0000-0000-0000-000000000002" \
                --principal-id "$CURRENT_PRINCIPAL_OBJECT_ID" \
                --scope "/" \
                --only-show-errors 1>/dev/null; then
                echo "  Cosmos RBAC: role assignment created"
            else
                echo "  Cosmos RBAC: could not create role (may need Owner/User Access Admin)"
            fi
            echo "  Waiting 90s for Cosmos RBAC propagation..."
            sleep 90
        else
            echo "  Cosmos RBAC: role assignment already exists"
        fi
    fi
fi

# Storage: this storage account forbids shared-key auth (KeyBasedAuthenticationNotPermitted).
# Use Entra ID + RBAC and optionally auto-grant the data-plane role.
allow_shared_key=$(az storage account show -n "$STORAGE_NAME" -g "$RESOURCE_GROUP" --query allowSharedKeyAccess -o tsv 2>/dev/null || echo "")
if [[ "$allow_shared_key" == "true" ]]; then
    if [[ -z "${AZURE_STORAGE_CONNECTION_STRING:-}" && -z "${STORAGE_ACCOUNT_KEY:-}" ]]; then
        storage_conn=$(az storage account show-connection-string \
            -n "$STORAGE_NAME" \
            -g "$RESOURCE_GROUP" \
            --query connectionString \
            -o tsv 2>/dev/null || true)
        if [[ -n "${storage_conn:-}" ]]; then
            export AZURE_STORAGE_CONNECTION_STRING="$storage_conn"
            echo "  Storage Auth: obtained connection string for this run"
        fi
    fi
else
    echo "  Storage Auth: shared key disabled; using Entra ID (RBAC required)"
fi

if [[ "$AUTO_GRANT_STORAGE_RBAC" == "true" && -n "${CURRENT_PRINCIPAL_OBJECT_ID:-}" ]]; then
    STORAGE_ID=$(az storage account show -n "$STORAGE_NAME" -g "$RESOURCE_GROUP" --query id -o tsv 2>/dev/null || true)
    if [[ -n "$STORAGE_ID" ]]; then
        existing_storage=$(az role assignment list \
            --assignee-object-id "$CURRENT_PRINCIPAL_OBJECT_ID" \
            --scope "$STORAGE_ID" \
            --query "[?roleDefinitionName=='Storage Blob Data Contributor'] | length(@)" \
            -o tsv 2>/dev/null || echo "0")
        if [[ "$existing_storage" == "0" ]]; then
            echo "  Granting Storage Blob Data Contributor role (best-effort)..."
            if az role assignment create \
                --assignee-object-id "$CURRENT_PRINCIPAL_OBJECT_ID" \
                --role "Storage Blob Data Contributor" \
                --scope "$STORAGE_ID" \
                --only-show-errors 1>/dev/null; then
                echo "  Storage RBAC: role assignment created"
            else
                echo "  Storage RBAC: could not create role (may need Owner/User Access Admin)"
            fi
            echo "  Waiting 60s for Storage RBAC propagation..."
            sleep 60
        else
            echo "  Storage RBAC: role assignment already exists"
        fi
    fi
fi

# Prefer OpenAI API key auth for local runs to avoid Entra RBAC data-action issues.
# If you want Entra ID only, set OPENAI_API_KEY="" and ensure RBAC is configured.
if [[ -z "${OPENAI_API_KEY:-}" && -z "${AZURE_OPENAI_API_KEY:-}" ]]; then
    openai_key=$(az cognitiveservices account keys list \
        -n "$OPENAI_NAME" \
        -g "$RESOURCE_GROUP" \
        --query key1 \
        -o tsv 2>/dev/null || true)
    if [[ -n "${openai_key:-}" ]]; then
        export OPENAI_API_KEY="$openai_key"
        echo "  OpenAI Auth: obtained API key for this run"
    else
        echo "  OpenAI Auth: could not obtain API key; will use Entra ID (RBAC)"

        if [[ "$AUTO_GRANT_OPENAI_RBAC" == "true" ]]; then
            echo "  Attempting to grant 'Cognitive Services OpenAI User' to current principal (best-effort)..."

            ASSIGNEE_OBJECT_ID=$(az account get-access-token --resource https://management.azure.com --query accessToken -o tsv | \
                python3 - <<'PY'
import base64, json, sys
token = sys.stdin.read().strip()
parts = token.split('.')
if len(parts) < 2:
    print('')
    raise SystemExit(0)
payload = parts[1]
payload += '=' * (-len(payload) % 4)
claims = json.loads(base64.urlsafe_b64decode(payload.encode('utf-8')).decode('utf-8'))
print(claims.get('oid') or claims.get('objectId') or '')
PY
            )

            if [[ -z "$ASSIGNEE_OBJECT_ID" ]]; then
                # Fallbacks using Entra ID graph lookups
                ACCOUNT_TYPE=$(az account show --query user.type -o tsv 2>/dev/null || echo "")
                ACCOUNT_NAME=$(az account show --query user.name -o tsv 2>/dev/null || echo "")

                if [[ "$ACCOUNT_TYPE" == "user" ]]; then
                    ASSIGNEE_OBJECT_ID=$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)
                    if [[ -z "$ASSIGNEE_OBJECT_ID" && -n "$ACCOUNT_NAME" ]]; then
                        ASSIGNEE_OBJECT_ID=$(az ad user show --id "$ACCOUNT_NAME" --query id -o tsv 2>/dev/null || true)
                    fi
                elif [[ "$ACCOUNT_TYPE" == "servicePrincipal" && -n "$ACCOUNT_NAME" ]]; then
                    # ACCOUNT_NAME is typically appId for SP
                    ASSIGNEE_OBJECT_ID=$(az ad sp show --id "$ACCOUNT_NAME" --query id -o tsv 2>/dev/null || true)
                fi
            fi

            if [[ -z "$ASSIGNEE_OBJECT_ID" ]]; then
                echo "  WARNING: Could not determine current principal objectId; OpenAI calls may fail with 401."
            else
                OPENAI_ID=$(az cognitiveservices account show -n "$OPENAI_NAME" -g "$RESOURCE_GROUP" --query id -o tsv 2>/dev/null || true)
                if [[ -z "$OPENAI_ID" ]]; then
                    echo "  WARNING: Could not resolve OpenAI resource ID; skipping role assignment."
                else
                    existing_user=$(az role assignment list \
                        --assignee-object-id "$ASSIGNEE_OBJECT_ID" \
                        --scope "$OPENAI_ID" \
                        --query "[?roleDefinitionName=='Cognitive Services OpenAI User'] | length(@)" \
                        -o tsv 2>/dev/null || echo "0")

                    existing_contrib=$(az role assignment list \
                        --assignee-object-id "$ASSIGNEE_OBJECT_ID" \
                        --scope "$OPENAI_ID" \
                        --query "[?roleDefinitionName=='Cognitive Services OpenAI Contributor'] | length(@)" \
                        -o tsv 2>/dev/null || echo "0")

                    if [[ "$existing_user" == "0" || "$existing_contrib" == "0" ]]; then
                        ACCOUNT_TYPE=$(az account show --query user.type -o tsv 2>/dev/null || echo "")
                        PRINCIPAL_TYPE=""
                        if [[ "$ACCOUNT_TYPE" == "user" ]]; then
                            PRINCIPAL_TYPE="User"
                        elif [[ "$ACCOUNT_TYPE" == "servicePrincipal" ]]; then
                            PRINCIPAL_TYPE="ServicePrincipal"
                        fi

                        if [[ -n "$PRINCIPAL_TYPE" ]]; then
                            if [[ "$existing_user" == "0" ]]; then
                                if az role assignment create \
                                    --assignee-object-id "$ASSIGNEE_OBJECT_ID" \
                                    --assignee-principal-type "$PRINCIPAL_TYPE" \
                                    --role "Cognitive Services OpenAI User" \
                                    --scope "$OPENAI_ID" \
                                    --only-show-errors 1>/dev/null; then
                                    echo "  Created role assignment: Cognitive Services OpenAI User"
                                else
                                    echo "  WARNING: Failed to create OpenAI User role assignment (need Owner/User Access Admin)."
                                fi
                            else
                                echo "  OpenAI User role assignment already exists"
                            fi

                            if [[ "$existing_contrib" == "0" ]]; then
                                if az role assignment create \
                                    --assignee-object-id "$ASSIGNEE_OBJECT_ID" \
                                    --assignee-principal-type "$PRINCIPAL_TYPE" \
                                    --role "Cognitive Services OpenAI Contributor" \
                                    --scope "$OPENAI_ID" \
                                    --only-show-errors 1>/dev/null; then
                                    echo "  Created role assignment: Cognitive Services OpenAI Contributor"
                                else
                                    echo "  WARNING: Failed to create OpenAI Contributor role assignment (need Owner/User Access Admin)."
                                fi
                            else
                                echo "  OpenAI Contributor role assignment already exists"
                            fi
                        else
                            if [[ "$existing_user" == "0" ]]; then
                                if az role assignment create \
                                    --assignee-object-id "$ASSIGNEE_OBJECT_ID" \
                                    --role "Cognitive Services OpenAI User" \
                                    --scope "$OPENAI_ID" \
                                    --only-show-errors 1>/dev/null; then
                                    echo "  Created role assignment: Cognitive Services OpenAI User"
                                else
                                    echo "  WARNING: Failed to create OpenAI User role assignment (need Owner/User Access Admin)."
                                fi
                            else
                                echo "  OpenAI User role assignment already exists"
                            fi

                            if [[ "$existing_contrib" == "0" ]]; then
                                if az role assignment create \
                                    --assignee-object-id "$ASSIGNEE_OBJECT_ID" \
                                    --role "Cognitive Services OpenAI Contributor" \
                                    --scope "$OPENAI_ID" \
                                    --only-show-errors 1>/dev/null; then
                                    echo "  Created role assignment: Cognitive Services OpenAI Contributor"
                                else
                                    echo "  WARNING: Failed to create OpenAI Contributor role assignment (need Owner/User Access Admin)."
                                fi
                            else
                                echo "  OpenAI Contributor role assignment already exists"
                            fi
                        fi
                    else
                        echo "  OpenAI role assignments already exist"
                    fi

                    echo "  Waiting 60s for OpenAI RBAC propagation..."
                    sleep 60
                fi
            fi
        fi
    fi
fi

# Speech: In this tenant we frequently run with disableLocalAuth=true (no keys).
# Prefer key auth only if local auth is enabled and a key can be retrieved.
if [[ "${SPEECH_DISABLE_LOCAL_AUTH:-}" != "true" && -z "${SPEECH_KEY:-}" ]]; then
    speech_key=$(az cognitiveservices account keys list \
        -n "$SPEECH_NAME" \
        -g "$RESOURCE_GROUP" \
        --query key1 \
        -o tsv 2>/dev/null || true)
    if [[ -n "${speech_key:-}" ]]; then
        export SPEECH_KEY="$speech_key"
        echo "  Speech Auth: obtained API key for this run"
    fi
fi

if [[ -z "${SPEECH_KEY:-}" ]]; then
    echo "  Speech Auth: using Entra ID (RBAC required)"

    if [[ "$AUTO_GRANT_SPEECH_RBAC" == "true" && -n "${CURRENT_PRINCIPAL_OBJECT_ID:-}" ]]; then
        echo "  Attempting to grant 'Cognitive Services Speech User' to current principal (best-effort)..."
        SPEECH_ID=$(az cognitiveservices account show -n "$SPEECH_NAME" -g "$RESOURCE_GROUP" --query id -o tsv 2>/dev/null || true)
        if [[ -n "$SPEECH_ID" ]]; then
            existing_speech=$(az role assignment list \
                --assignee-object-id "$CURRENT_PRINCIPAL_OBJECT_ID" \
                --scope "$SPEECH_ID" \
                --query "[?roleDefinitionName=='Cognitive Services Speech User'] | length(@)" \
                -o tsv 2>/dev/null || echo "0")

            if [[ "$existing_speech" == "0" ]]; then
                # Help az resolve principal type reliably.
                account_type=$(az account show --query user.type -o tsv 2>/dev/null || echo "")
                principal_type="ServicePrincipal"
                if [[ "$account_type" == "user" ]]; then
                    principal_type="User"
                fi

                if az role assignment create \
                    --assignee-object-id "$CURRENT_PRINCIPAL_OBJECT_ID" \
                    --assignee-principal-type "$principal_type" \
                    --role "Cognitive Services Speech User" \
                    --scope "$SPEECH_ID" \
                    --only-show-errors 1>/dev/null; then
                    echo "  Created role assignment: Cognitive Services Speech User"
                else
                    echo "  WARNING: Failed to create Speech User role assignment (need Owner/User Access Admin)."
                fi
                echo "  Waiting 90s for Speech RBAC propagation..."
                sleep 90
            else
                echo "  Speech role assignment already exists"
            fi
        else
            echo "  WARNING: Could not resolve Speech resource ID; skipping role assignment."
        fi
    fi
fi

# Create or reuse AI Search
echo ""

if [[ -n "${SEARCH_ENDPOINT:-}" ]]; then
        # Derive service name from endpoint, e.g. https://<name>.search.windows.net
        SEARCH_NAME="${SEARCH_ENDPOINT#https://}"
        SEARCH_NAME="${SEARCH_NAME%%.search.windows.net*}"
        echo "Reusing AI Search service: $SEARCH_NAME"
        echo "  Search:  $SEARCH_ENDPOINT"
else
        echo "Creating ephemeral AI Search service..."
        SEARCH_NAME="search-${CERT_ID}-$(date +%Y%m%d-%H%M%S)"
        az search service create \
            --name "$SEARCH_NAME" \
            --resource-group "$RESOURCE_GROUP" \
            --location "$LOCATION" \
            --sku basic \
            --partition-count 1 \
            --replica-count 1 \
            -o none

        export SEARCH_ENDPOINT="https://${SEARCH_NAME}.search.windows.net"
        echo "  Search:  $SEARCH_ENDPOINT"
fi

# Prefer admin-key auth for the ephemeral Search service (fastest + avoids RBAC propagation issues)
if [[ -z "${SEARCH_ADMIN_KEY:-}" ]]; then
    search_key=$(az search admin-key show \
        --service-name "$SEARCH_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --query primaryKey \
        -o tsv 2>/dev/null || true)

    if [[ -z "${search_key:-}" ]]; then
        # Some az versions use --name instead of --service-name
        search_key=$(az search admin-key show \
            --name "$SEARCH_NAME" \
            --resource-group "$RESOURCE_GROUP" \
            --query primaryKey \
            -o tsv 2>/dev/null || true)
    fi

    if [[ -n "${search_key:-}" ]]; then
        export SEARCH_ADMIN_KEY="$search_key"
        echo "  Search Auth: obtained admin key for this run"
    else
        echo "  Search Auth: could not obtain admin key; will use Entra ID (RBAC)"
        if [[ "$AUTO_GRANT_SEARCH_RBAC" != "true" ]]; then
            echo "  Enabling AUTO_GRANT_SEARCH_RBAC because admin key is unavailable"
            AUTO_GRANT_SEARCH_RBAC="true"
        fi
    fi
fi

echo ""
echo "Ensuring Search data-plane RBAC for current principal..."

if [[ -n "${SEARCH_ADMIN_KEY:-}" ]]; then
    echo "  Skipping RBAC grant because SEARCH_ADMIN_KEY is set"
elif [[ "$AUTO_GRANT_SEARCH_RBAC" != "true" ]]; then
    echo "  Skipping RBAC auto-grant (set AUTO_GRANT_SEARCH_RBAC=true to enable)"
else

# Determine current principal object ID by decoding ARM access token (works for user and SP)
ASSIGNEE_OBJECT_ID=$(az account get-access-token --resource https://management.azure.com --query accessToken -o tsv | \
    python3 - <<'PY'
import base64, json, sys
token = sys.stdin.read().strip()
parts = token.split('.')
if len(parts) < 2:
        print('')
        raise SystemExit(0)
payload = parts[1]
payload += '=' * (-len(payload) % 4)
claims = json.loads(base64.urlsafe_b64decode(payload.encode('utf-8')).decode('utf-8'))
print(claims.get('oid') or claims.get('objectId') or '')
PY
)

if [[ -z "$ASSIGNEE_OBJECT_ID" ]]; then
    echo "WARNING: Could not determine current principal objectId; Search indexing may fail with Forbidden."
else
    SEARCH_ID=$(az search service show --name "$SEARCH_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)
    if [[ -z "$SEARCH_ID" ]]; then
        echo "ERROR: Could not resolve Search resource ID."
        exit 1
    fi

    # Principal type mapping for az role assignment create
    ACCOUNT_TYPE=$(az account show --query user.type -o tsv 2>/dev/null || echo "")
    PRINCIPAL_TYPE=""
    if [[ "$ACCOUNT_TYPE" == "user" ]]; then
        PRINCIPAL_TYPE="User"
    elif [[ "$ACCOUNT_TYPE" == "servicePrincipal" ]]; then
        PRINCIPAL_TYPE="ServicePrincipal"
    fi

    existing=$(az role assignment list \
        --assignee-object-id "$ASSIGNEE_OBJECT_ID" \
        --scope "$SEARCH_ID" \
        --query "[?roleDefinitionName=='Search Index Data Contributor'] | length(@)" \
        -o tsv 2>/dev/null || echo "0")

    if [[ "$existing" == "0" ]]; then
        if [[ -n "$PRINCIPAL_TYPE" ]]; then
            az role assignment create \
                --assignee-object-id "$ASSIGNEE_OBJECT_ID" \
                --assignee-principal-type "$PRINCIPAL_TYPE" \
                --role "Search Index Data Contributor" \
                --scope "$SEARCH_ID" \
                --only-show-errors 1>/dev/null
        else
            az role assignment create \
                --assignee-object-id "$ASSIGNEE_OBJECT_ID" \
                --role "Search Index Data Contributor" \
                --scope "$SEARCH_ID" \
                --only-show-errors 1>/dev/null
        fi
        echo "  Created role assignment: Search Index Data Contributor"
    else
        echo "  Search Index Data Contributor role assignment already exists"
    fi
fi

fi

# Cleanup function
cleanup() {
    if [[ "$CLEANUP_ON_EXIT" == "true" ]]; then
        echo ""
        echo "Cleaning up ephemeral Search service: $SEARCH_NAME"
        az search service delete --name "$SEARCH_NAME" --resource-group "$RESOURCE_GROUP" --yes -o none 2>/dev/null || true
    else
        echo ""
        echo "Leaving Search service in place (CLEANUP_ON_EXIT=false): $SEARCH_NAME"
        echo "  Endpoint: $SEARCH_ENDPOINT"
    fi
}
trap cleanup EXIT

echo ""
echo "Waiting for Search service provisioning + RBAC propagation..."

for attempt in {1..30}; do
    state=$(az search service show --name "$SEARCH_NAME" --resource-group "$RESOURCE_GROUP" --query provisioningState -o tsv 2>/dev/null || echo "")
    if [[ "$state" == "succeeded" || "$state" == "Succeeded" ]]; then
        echo "  Search provisioningState=$state"
        break
    fi
    echo "  Search provisioningState=${state:-unknown} (attempt $attempt/30)"
    sleep 10
done

# Extra wait for RBAC to take effect (not needed for admin-key auth)
if [[ -z "${SEARCH_ADMIN_KEY:-}" ]]; then
    echo "Waiting 30s for Search RBAC propagation..."
    sleep 30
fi

# Run the generation pipeline
cd "$(dirname "$0")/../src/pipeline"

echo ""
echo "=========================================="
echo "PHASE 1: Content Discovery"
echo "=========================================="

if [[ -n "$DISCOVERY_RESULTS_FILE" ]]; then
    if [[ ! -f "$DISCOVERY_RESULTS_FILE" ]]; then
        echo "ERROR: DISCOVERY_RESULTS_FILE not found: $DISCOVERY_RESULTS_FILE"
        exit 1
    fi
    echo "Reusing discovery results: $DISCOVERY_RESULTS_FILE"
    DISCOVERY_FILE="$DISCOVERY_RESULTS_FILE"
else
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
fi

SKILLS_OUTLINE=$(cat "$DISCOVERY_FILE" | jq -c '.skillsOutline')
SOURCE_URLS=$(cat "$DISCOVERY_FILE" | jq -c '.sourceUrls')
if [[ -z "$DISCOVERY_RESULTS_FILE" ]]; then
    rm -f "$DISCOVERY_FILE"
fi

# Used by later Python snippets
export SKILLS_OUTLINE

echo ""
echo "=========================================="
echo "PHASE 2: Content Indexing"
echo "=========================================="

python3 -m tools.index_content \
    --certification-id "$CERT_ID" \
    --source-urls "$SOURCE_URLS"

index_rc=$?

if [[ $index_rc -ne 0 ]]; then
    echo "Indexing failed (exit=$index_rc). Retrying with backoff (RBAC can take time)..."
    for backoff in 15 30 60 120 180; do
        sleep "$backoff"
        python3 -m tools.index_content \
            --certification-id "$CERT_ID" \
            --source-urls "$SOURCE_URLS" && index_rc=0 && break
    done
fi

if [[ $index_rc -ne 0 ]]; then
    echo "ERROR: Indexing still failing after retries."
    exit $index_rc
fi

if [[ "$FORCE_REGEN" == "true" ]]; then
    echo ""
    echo "=========================================="
    echo "PHASE 2.5: Purge Existing Episodes (FORCE_REGENERATE=true)"
    echo "=========================================="

    export CERT_ID AUDIO_FORMAT
    python3 - <<'PY'
import os
from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError

cert_id = os.environ["CERT_ID"]
audio_format = os.environ["AUDIO_FORMAT"]
cosmos_endpoint = os.environ["COSMOS_DB_ENDPOINT"]
storage_account = os.environ["STORAGE_ACCOUNT_NAME"]

credential = DefaultAzureCredential()

print(f"Purging episodes for {cert_id}/{audio_format}...")

# Purge from Cosmos DB (if container exists)
# Note: Cosmos DB has disableLocalAuth=true, so we must use Entra ID tokens
print("Purging from Cosmos DB...")
try:
    cosmos = CosmosClient(cosmos_endpoint, credential)
    db = cosmos.get_database_client("certaudio")
    container = db.get_container_client("episodes")

    query = (
        "SELECT c.id FROM c "
        f"WHERE c.certificationId = '{cert_id}' AND c.format = '{audio_format}'"
    )
    items = list(container.query_items(query, enable_cross_partition_query=True))
    for item in items:
        try:
            container.delete_item(item["id"], partition_key=cert_id)
        except Exception as e:
            print(f"  Warning: Could not delete {item['id']}: {e}")
    print(f"Deleted {len(items)} episode documents")
except CosmosResourceNotFoundError:
    print("  Cosmos container 'episodes' does not exist yet - nothing to purge")
except Exception as e:
    print(f"  Warning: Could not access Cosmos DB: {e}")

# Purge from Blob Storage (if container exists)
print("Purging from Blob Storage...")
try:
    # Storage account has shared key disabled; use Entra ID tokens
    blob_url = f"https://{storage_account}.blob.core.windows.net"
    blob_service = BlobServiceClient(blob_url, credential)

    audio_container = blob_service.get_container_client("audio")
    scripts_container = blob_service.get_container_client("scripts")

    deleted_blobs = 0
    # Audio blobs
    if audio_container.exists():
        for blob in audio_container.list_blobs(name_starts_with=f"{cert_id}/{audio_format}/episodes/"):
            try:
                audio_container.delete_blob(blob.name)
                deleted_blobs += 1
            except Exception as e:
                print(f"  Warning: Could not delete {blob.name}: {e}")
    else:
        print("  Blob container 'audio' does not exist yet - nothing to purge")

    # Script + SSML blobs
    if scripts_container.exists():
        for prefix in (
            f"{cert_id}/{audio_format}/scripts/",
            f"{cert_id}/{audio_format}/ssml/",
        ):
            for blob in scripts_container.list_blobs(name_starts_with=prefix):
                try:
                    scripts_container.delete_blob(blob.name)
                    deleted_blobs += 1
                except Exception as e:
                    print(f"  Warning: Could not delete {blob.name}: {e}")
    else:
        print("  Blob container 'scripts' does not exist yet - nothing to purge")

    print(f"Deleted {deleted_blobs} blobs")
except ResourceNotFoundError:
    print("  Blob container 'content' does not exist yet - nothing to purge")
except Exception as e:
    print(f"  Warning: Could not access Blob Storage: {e}")

print("Purge complete")
PY
fi

echo ""
echo "=========================================="
echo "PHASE 3: Episode Generation"
echo "=========================================="

# Calculate batch info (match workflow logic: domains split into episode units by topics_per_episode)
BATCH_SIZE="${EPISODE_BATCH_SIZE:-10}"
TOPICS_PER_EPISODE="${TOPICS_PER_EPISODE:-5}"

export BATCH_SIZE TOPICS_PER_EPISODE

NUM_BATCHES=$(python3 - <<'PY'
import json, math, os

skills_outline = os.environ.get('SKILLS_OUTLINE', '[]')
batch_size = int(os.environ.get('BATCH_SIZE', '10'))
topics_per_ep = int(os.environ.get('TOPICS_PER_EPISODE', '5'))

skills = json.loads(skills_outline)

# deep_discover output is a list of domains with topics
main_skills = [s for s in skills if isinstance(s, dict) and s.get('topics')]

episode_units = 0
for skill in main_skills:
    n_topics = len(skill.get('topics', []))
    episode_units += max(1, math.ceil(n_topics / topics_per_ep))

if episode_units == 0:
    raise SystemExit('0')

num_batches = math.ceil(episode_units / batch_size)
print(str(num_batches))
PY
)

echo "Batch size: $BATCH_SIZE"
echo "Topics per episode: $TOPICS_PER_EPISODE"
echo "Number of batches: $NUM_BATCHES"

REGEN_FLAG=""
if [[ "$FORCE_REGEN" == "true" ]]; then
    REGEN_FLAG="--force-regenerate"
fi

for ((batch=0; batch<NUM_BATCHES; batch++)); do
    echo ""
    echo "--- Batch $batch of $NUM_BATCHES ---"
    
    python3 -m tools.generate_episodes \
        --certification-id "$CERT_ID" \
        --audio-format "$AUDIO_FORMAT" \
        --instructional-voice "$INSTRUCTIONAL_VOICE" \
        --podcast-host-voice "$PODCAST_HOST_VOICE" \
        --podcast-expert-voice "$PODCAST_EXPERT_VOICE" \
        --skills-outline "$SKILLS_OUTLINE" \
        --batch-index "$batch" \
        --batch-size "$BATCH_SIZE" \
        $REGEN_FLAG
done

echo ""
echo "=========================================="
echo "GENERATION COMPLETE"
echo "=========================================="
echo ""
echo "View your content at the web app or query Cosmos DB."
