# Gateway policy: caller identity on every model call

**Status: prototype.** The policy has not been deployed to API Management. The test in `tests/test_apim_policy.py`
only checks that the XML parses and keeps the rules below. Expressions, the trace output and the log join all need a
real test before anyone relies on them. See `docs/DESIGN_USAGE_ATTRIBUTION.md` (sections 4.9 and 4.10).

## What it does

`llm-caller-attribution.policy.xml` goes on the API Management API that fronts the model deployments. Callers point at the
gateway; nothing changes in the agent code.

| Caller sends | Result |
|---|---|
| Valid bearer token with an `scp` claim (a signed-in person) | `caller_type=user`, `caller_oid` is the person's object ID |
| Valid bearer token without `scp` (an agent or service acting as itself) | `caller_type=agent`, `caller_oid` is that identity's object ID |
| No bearer token (for example a subscription key) | `caller_type=unknown`, no `caller_oid`. Not an error. |
| Bearer token that fails validation | `401`. An identity can't be forged by sending a bad token. |

`agent_id` is the token's `azp` (or `appid`) claim, the calling application's client ID, else an optional
`x-finops-agent-id` header, else `unknown`.

## Two outputs

1. **A coarse metric** (`finops` namespace) with `caller_type`, `agent_id` and `caller_tid`. API Management allows 100
   unique values per dimension and 1,000 active time series per namespace, and silently drops data past that. That is
   why the object ID is **not** a metric dimension.
2. **A per-call trace record** with the object ID and the request ID. This is where per-person usage comes from.

## Try it in a test subscription

`deploy/` has a Bicep template and two scripts. **They compile but have never been run against a real subscription.**

| File | What it does |
|---|---|
| `deploy/main.bicep` | Log Analytics workspace, workspace-based Application Insights, an API Management instance with a managed identity, one chat-completions API that forwards to your model endpoint, the policy, and diagnostic settings that send gateway logs to the workspace. Subscription keys stay required, so the gateway is not an open proxy to your model. |
| `deploy/deploy.sh` | Creates the resource group and deploys the template, after showing what it will create and asking to continue. `deploy.sh teardown` deletes the resource group. |
| `deploy/smoke-test.sh` | Sends a call with no token (expects 200), a call with a bad token (expects 401), and a call with your token, then runs the portal's gateway query against the workspace and waits for the rows. |

You provide the model endpoint, a gateway API registration whose ID is the audience, and (for the role assignment) the name
of a Foundry or Azure OpenAI account in the same resource group. API Management is billed while it exists and can take many
minutes to create. Run `deploy.sh teardown` when done.

What the template does **not** do, and you must check by hand:

- Turn on the API-level LLM logging setting. The template enables the `GatewayLlmLogs` category on the service, but the
  category name is unverified, and the per-API setting is not in the template.
- Create the API Management subscription and key, or the Entra app registration.
- Prove that `llm-emit-token-metric` accepts the dimension expressions or that the trace record reaches the workspace. The
  smoke test is how you find out.

## Using it

1. Create the named values `tenant-id` and `api-audience`.
2. Turn on Application Insights logging for the API, and the gateway's LLM logs in the diagnostic settings (the token
   counts per call live there).
3. Apply the policy to the LLM API.
4. Check one call as a person, one as an agent and one with no token. Compare the three records.

## Joining identity to tokens (untested)

The gateway's LLM log has the token counts per call; the trace record has the identity. Both carry the request ID. A
join of this shape should give per-caller cost, but table and column names must be checked against what your instance
actually writes:

```kusto
let who = traces
    | where customDimensions has "finops-attribution" or message has "caller_oid"
    | extend rec = parse_json(message)
    | project request_id = tostring(rec.request_id), caller_oid = tostring(rec.caller_oid),
              caller_type = tostring(rec.caller_type), agent_id = tostring(rec.agent_id);
ApiManagementGatewayLlmLog
| extend request_id = tostring(CorrelationId)
| join kind=leftouter who on request_id
| summarize prompt = sum(PromptTokens), completion = sum(CompletionTokens)
    by caller_oid = coalesce(caller_oid, ""), caller_type = coalesce(caller_type, "unknown"), ModelName
```

Rows with no matching trace record are calls that bypassed the policy; count them as `unknown`, not as an error.

## Reading it from the portal

The portal's live reader (`LiveBackend.usage_by_caller`, `foundry_finops.queries.usage_by_caller`) runs the join above
against `PORTAL_LOGS_TARGET` and feeds `GET /api/cost-centres/usage-split`. It is tokens only for this source. It has the
same untested status as the policy: it needs a Log Analytics workspace that holds both the Application Insights traces
and the gateway LLM log.

## Open checks before production

- The `validate-jwt` claim expressions return the right values for your token versions (v1 or v2).
- Whether `llm-emit-token-metric` accepts these dimension expressions on your gateway tier.
- Whether the `trace` message reaches Application Insights at the configured verbosity, and how the request ID joins.
- The exact name of the LLM log table and its columns.
- Whether the API schema type API Management uses for your Foundry deployments is one the metric policy supports.
