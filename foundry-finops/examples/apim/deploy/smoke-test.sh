#!/usr/bin/env bash
# Sends three calls through the test gateway, then runs the portal's gateway query against the workspace.
# PROTOTYPE: not yet run against a real deployment. Expected result, after ingestion delay (often 5 to 15 minutes):
#   a row with caller type "unknown" and no object ID, and a row with your object ID and type "user" (or "agent").
#
#   GATEWAY_URL=https://<apim>.azure-api.net SUBSCRIPTION_KEY=<key> DEPLOYMENT=<model deployment name> \
#   API_AUDIENCE=api://<client-id> WORKSPACE_CUSTOMER_ID=<guid> ./smoke-test.sh
#
#   Optional: API_VERSION (default 2024-10-21), WAIT_MINUTES (default 20; 0 runs the query once).
set -euo pipefail

for name in GATEWAY_URL SUBSCRIPTION_KEY DEPLOYMENT API_AUDIENCE WORKSPACE_CUSTOMER_ID; do
  if [ -z "${!name:-}" ]; then echo "Set $name" >&2; exit 2; fi
done
API_VERSION="${API_VERSION:-2024-10-21}"
WAIT_MINUTES="${WAIT_MINUTES:-20}"
url="${GATEWAY_URL%/}/openai/deployments/${DEPLOYMENT}/chat/completions?api-version=${API_VERSION}"
body='{"messages":[{"role":"user","content":"Say hello in three words."}],"max_tokens":16}'

call() {  # call <label> <expected status> [extra curl args...]
  local label="$1" want="$2"
  shift 2
  local got
  got="$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$url" \
    -H "Content-Type: application/json" -H "Ocp-Apim-Subscription-Key: ${SUBSCRIPTION_KEY}" "$@" -d "$body")"
  if [ "$got" = "$want" ]; then echo "ok   $label -> $got"; else echo "FAIL $label -> $got (wanted $want)"; failed=1; fi
}

failed=0
call "no bearer token (recorded as unknown)" 200
call "bad bearer token (rejected)" 401 -H "Authorization: Bearer not-a-token"
if token="$(az account get-access-token --resource "$API_AUDIENCE" --query accessToken -o tsv 2>/dev/null)"; then
  call "your token (recorded as user)" 200 -H "Authorization: Bearer ${token}"
else
  echo "skip your token: az could not get a token for $API_AUDIENCE (the Azure CLI may need to be allowed on the API registration)"
fi
[ "$failed" -eq 0 ] || { echo "A call did not behave as expected; check the gateway before reading the logs." >&2; exit 1; }

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
query="$(PYTHONPATH="$repo/src" python3 -c \
  "from foundry_finops import queries; print(queries.usage_by_caller('gateway', '${WORKSPACE_CUSTOMER_ID}'))")"

echo "Querying the workspace (log ingestion can take several minutes)..."
deadline=$(( $(date +%s) + WAIT_MINUTES * 60 ))
while :; do
  result="$(az monitor log-analytics query --workspace "$WORKSPACE_CUSTOMER_ID" --analytics-query "$query" --timespan P1D -o json)"
  count="$(printf '%s' "$result" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
  if [ "$count" -ge 2 ]; then break; fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "Fewer than two caller rows after ${WAIT_MINUTES} minutes. Rows so far: $count" >&2
    printf '%s\n' "$result"
    echo "See 'Open checks before production' in ../README.md: table and column names, trace output, request ID join." >&2
    exit 1
  fi
  sleep 60
done
printf '%s\n' "$result"
