#!/usr/bin/env bash
# Creates the Entra app registration the portal signs people in with, and prints the values to give to main.bicep.
# Needs: az login as someone who can create app registrations and grant admin consent (Application Administrator or higher).
# Usage: create-app-registration.sh <portal-url> [display-name]
set -euo pipefail
url="${1:?usage: $0 <portal-url> [display-name]}"
name="${2:-Foundry FinOps Portal}"

app_id=$(az ad app create --display-name "$name" --sign-in-audience AzureADMyOrg \
  --enable-access-token-issuance false --query appId -o tsv)
object_id=$(az ad app show --id "$app_id" --query id -o tsv)

# Single-page app redirect (auth code + PKCE) and the API scope the SPA requests.
az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications/$object_id" \
  --headers "Content-Type=application/json" \
  --body "{\"spa\":{\"redirectUris\":[\"$url\",\"http://localhost:5173\"]},\"identifierUris\":[\"api://$app_id\"],
  \"api\":{\"requestedAccessTokenVersion\":2,\"oauth2PermissionScopes\":[{\"id\":\"$(uuidgen)\",\"adminConsentDisplayName\":\"Use the portal as the signed-in user\",
  \"adminConsentDescription\":\"Lets the portal call Azure on behalf of the signed-in user.\",\"userConsentDisplayName\":\"Use the portal as you\",
  \"userConsentDescription\":\"Lets the portal call Azure on your behalf.\",\"isEnabled\":true,\"type\":\"User\",\"value\":\"access_as_user\"}]}}"

# Delegated Azure Service Management (user_impersonation) for the on-behalf-of calls, and Graph User.Read.All (application)
# so administrators can look people up by name.
az ad app permission add --id "$app_id" --api 797f4846-ba00-4fd7-ba43-dac1f8f63013 \
  --api-permissions 41094075-9dad-400e-a0bd-54e686782033=Scope
az ad app permission add --id "$app_id" --api 00000003-0000-0000-c000-000000000000 \
  --api-permissions df021288-bdef-4463-88db-98f22de89214=Role
az ad sp create --id "$app_id" >/dev/null
az ad app permission admin-consent --id "$app_id" || echo "Grant admin consent in the portal registration if the line above failed."

secret=$(az ad app credential reset --id "$app_id" --display-name portal --years 1 --query password -o tsv)

cat <<OUT
portalClientId     = $app_id
portalClientSecret = (printed below once; store it now)
$secret
OUT
