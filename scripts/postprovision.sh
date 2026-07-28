#!/bin/sh
# Post-provision steps that Bicep cannot express.
#
# 1. Link the Static Web App to the Functions backend. This is what makes
#    /api/* reachable from the site and what injects x-ms-client-principal.
# 2. Enforce EasyAuth on that backend. Linking registers the Static Web Apps
#    identity provider but leaves requireAuthentication=false, which means the
#    Functions hostname still answers anonymous callers and the principal header
#    can be forged. The app's admin authorization depends on that header, so
#    enforcement is a correctness requirement, not hardening.
#
# Safe to re-run: every step is idempotent.

set -eu

say() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# azd exports outputs into the environment. Fall back to `azd env get-values`
# when the script is run by hand.
if [ -z "${AZURE_RESOURCE_GROUP:-}" ] && command -v azd >/dev/null 2>&1; then
    eval "$(azd env get-values 2>/dev/null | sed 's/^/export /')" || true
fi

RG="${AZURE_RESOURCE_GROUP:-${resourceGroupName:-}}"
SWA="${STATIC_WEB_APP_NAME:-${staticWebAppName:-}}"
FUNC="${FUNCTIONS_APP_NAME:-${functionsAppName:-}}"
LOC="${AZURE_LOCATION:-}"

[ -n "$RG" ] || fail "AZURE_RESOURCE_GROUP is not set. Run 'azd provision' first, or export it."

# Outputs are named differently across azd versions; discover from the group.
if [ -z "$SWA" ]; then
    SWA=$(az staticwebapp list -g "$RG" --query "[0].name" -o tsv 2>/dev/null || true)
fi
if [ -z "$FUNC" ]; then
    FUNC=$(az functionapp list -g "$RG" --query "[0].name" -o tsv 2>/dev/null || true)
fi
[ -n "$SWA" ] || fail "Could not determine the Static Web App name in $RG."
[ -n "$FUNC" ] || fail "Could not determine the Functions app name in $RG."
[ -n "$LOC" ] || LOC=$(az functionapp show -g "$RG" -n "$FUNC" --query location -o tsv)

BACKEND_ID=$(az functionapp show -g "$RG" -n "$FUNC" --query id -o tsv)

say "Resource group: $RG"
say "Static Web App: $SWA"
say "Functions app:  $FUNC"
say ""

# ---------------------------------------------------------------- 1. linking
CURRENT=$(az staticwebapp backends show \
    --name "$SWA" --resource-group "$RG" --environment-name default \
    --query "backendResourceId" -o tsv 2>/dev/null || true)

if [ "$CURRENT" = "$BACKEND_ID" ]; then
    say "Backend already linked."
else
    if [ -n "$CURRENT" ]; then
        say "A different backend is linked; unlinking it first."
        az staticwebapp backends unlink \
            --name "$SWA" --resource-group "$RG" --environment-name default \
            --remove-backend-auth --only-show-errors
    fi
    say "Linking $FUNC to $SWA..."
    az staticwebapp backends link \
        --name "$SWA" --resource-group "$RG" \
        --backend-resource-id "$BACKEND_ID" \
        --backend-region "$LOC" \
        --environment-name default --only-show-errors >/dev/null
fi

# Linking is what registers the provider. If it is missing, re-linking is the
# only supported way to restore it, and enforcing auth without it would lock the
# site out entirely.
AUTH_URL="https://management.azure.com${BACKEND_ID}/config/authsettingsV2"
az rest --method get --url "${AUTH_URL}/list?api-version=2023-01-01" -o json > /tmp/authsettings.json

PROVIDER=$(jq -r '.properties.identityProviders.azureStaticWebApps.enabled // false' /tmp/authsettings.json)
if [ "$PROVIDER" != "true" ]; then
    say "Static Web Apps auth provider missing; re-linking to restore it."
    az staticwebapp backends unlink \
        --name "$SWA" --resource-group "$RG" --environment-name default \
        --remove-backend-auth --only-show-errors
    az staticwebapp backends link \
        --name "$SWA" --resource-group "$RG" \
        --backend-resource-id "$BACKEND_ID" \
        --backend-region "$LOC" \
        --environment-name default --only-show-errors >/dev/null
    az rest --method get --url "${AUTH_URL}/list?api-version=2023-01-01" -o json > /tmp/authsettings.json
    PROVIDER=$(jq -r '.properties.identityProviders.azureStaticWebApps.enabled // false' /tmp/authsettings.json)
    [ "$PROVIDER" = "true" ] || fail "Could not register the Static Web Apps auth provider."
fi

# ------------------------------------------------------------ 2. enforcement
ENFORCED=$(jq -r '.properties.globalValidation.requireAuthentication // false' /tmp/authsettings.json)
if [ "$ENFORCED" = "true" ]; then
    say "Authentication already enforced."
else
    say "Enforcing authentication on $FUNC..."
    jq '{properties: (.properties
          | .globalValidation.requireAuthentication = true
          | .globalValidation.unauthenticatedClientAction = "Return401")}' \
        /tmp/authsettings.json > /tmp/authsettings-enforced.json

    az rest --method put \
        --url "${AUTH_URL}?api-version=2023-01-01" \
        --body @/tmp/authsettings-enforced.json >/dev/null

    # The auth module only reloads on restart.
    say "Restarting $FUNC so the auth change takes effect..."
    az functionapp restart -g "$RG" -n "$FUNC" --only-show-errors
fi

# --------------------------------------------------------------- 3. verify
say ""
say "Verifying the Functions hostname rejects anonymous callers..."
DIRECT="https://${FUNC}.azurewebsites.net/api/healthz"
i=1
while [ "$i" -le 12 ]; do
    CODE=$(curl -s -o /dev/null -w '%{http_code}' "$DIRECT" || true)
    if [ "$CODE" = "401" ] || [ "$CODE" = "403" ]; then
        say "Direct access blocked (HTTP $CODE)."
        break
    fi
    if [ "$i" -eq 12 ]; then
        fail "$FUNC still answers anonymous requests (HTTP $CODE). EasyAuth is not enforcing, so x-ms-client-principal can be forged and /api/admin/* is exposed."
    fi
    say "  attempt $i: HTTP $CODE, waiting for auth to propagate..."
    sleep 15
    i=$((i + 1))
done

SWA_HOST=$(az staticwebapp show -n "$SWA" -g "$RG" --query defaultHostname -o tsv)
say ""
say "Verifying the site can still reach its API..."
i=1
while [ "$i" -le 12 ]; do
    CODE=$(curl -s -o /dev/null -w '%{http_code}' "https://${SWA_HOST}/api/healthz" || true)
    if [ "$CODE" = "200" ]; then
        say "Site API reachable (HTTP 200)."
        break
    fi
    if [ "$i" -eq 12 ]; then
        fail "https://${SWA_HOST}/api/healthz returned HTTP $CODE. The backend link is not working."
    fi
    say "  attempt $i: HTTP $CODE, waiting for the link to propagate..."
    sleep 15
    i=$((i + 1))
done

say ""
say "Done. Claim admin access at https://${SWA_HOST}/admin.html"
say "Read the one-time token with:"
say "  az functionapp config appsettings list -g $RG -n $FUNC \\"
say "    --query \"[?name=='ADMIN_BOOTSTRAP_TOKEN'].value | [0]\" -o tsv"
