#!/usr/bin/env bash
# Deploys the test setup in main.bicep. PROTOTYPE: not yet run against a real subscription.
#
#   RESOURCE_GROUP=rg-finops-apim-test APIM_NAME=finops-apim-test-xyz PUBLISHER_EMAIL=you@example.com \
#   API_AUDIENCE=api://<client-id> BACKEND_URL=https://<account>.openai.azure.com/openai \
#   ./deploy.sh [--yes]
#
#   Optional: LOCATION (default eastus2), SKU (Developer|BasicV2|StandardV2, default BasicV2),
#             FOUNDRY_ACCOUNT (an account in the same resource group; the gateway is given access to it).
#
#   ./deploy.sh teardown      deletes the resource group (asks first unless --yes)
#
# API Management is billed while it exists. Run `teardown` when you have finished.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
action="deploy"
assume_yes=0
for arg in "$@"; do
  case "$arg" in
    deploy|teardown) action="$arg" ;;
    --yes|-y) assume_yes=1 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

need() {
  local missing=0
  for name in "$@"; do
    if [ -z "${!name:-}" ]; then echo "Set $name" >&2; missing=1; fi
  done
  [ "$missing" -eq 0 ] || exit 2
}

confirm() {
  [ "$assume_yes" -eq 1 ] && return 0
  read -r -p "$1 [y/N] " reply
  case "$reply" in y|Y|yes|YES) return 0 ;; *) echo "Cancelled."; exit 1 ;; esac
}

command -v az >/dev/null || { echo "The Azure CLI (az) is required" >&2; exit 2; }

if [ "$action" = "teardown" ]; then
  need RESOURCE_GROUP
  echo "This deletes the resource group '$RESOURCE_GROUP' and everything in it."
  confirm "Delete it?"
  az group delete --name "$RESOURCE_GROUP" --yes --no-wait
  echo "Deletion started. It continues in the background."
  exit 0
fi

need RESOURCE_GROUP APIM_NAME PUBLISHER_EMAIL API_AUDIENCE BACKEND_URL
LOCATION="${LOCATION:-eastus2}"
SKU="${SKU:-BasicV2}"
FOUNDRY_ACCOUNT="${FOUNDRY_ACCOUNT:-}"

echo "Subscription : $(az account show --query '[name, id]' -o tsv | tr '\t' ' ')"
echo "Will create  : resource group '$RESOURCE_GROUP' ($LOCATION), Log Analytics, Application Insights,"
echo "               API Management '$APIM_NAME' ($SKU), the policy, and diagnostic settings."
echo "Cost         : API Management is billed while it exists, and creation can take many minutes."
confirm "Continue?"

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$here/main.bicep" \
  --parameters apimName="$APIM_NAME" sku="$SKU" publisherEmail="$PUBLISHER_EMAIL" \
               apiAudience="$API_AUDIENCE" backendUrl="$BACKEND_URL" foundryAccountName="$FOUNDRY_ACCOUNT" \
  --query 'properties.outputs' --output json

cat <<MSG

Next:
  1. If FOUNDRY_ACCOUNT was empty, give the gateway's identity (output gatewayPrincipalId) the role
     "Cognitive Services OpenAI User" on the model resource.
  2. Create an API Management subscription for the API 'finops-llm' and note its key.
  3. Run smoke-test.sh with GATEWAY_URL, SUBSCRIPTION_KEY, DEPLOYMENT, API_AUDIENCE and WORKSPACE_CUSTOMER_ID.
MSG
