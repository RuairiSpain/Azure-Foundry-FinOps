# Privacy

This page describes what the software in this repository does with data. It covers the code only. The
**Foundry FinOps** connector and plugin are self-hosted: you (or your organisation) run the MCP server in your own
Azure subscription, so the operator of a deployment is the data controller for it. If you publish a deployment to
other people, review this page with your own privacy and legal advisers and replace the contact details below.

## Summary

- The software **has no telemetry** and sends nothing to the authors.
- It reads **billing, usage and resource-configuration data** from your own Azure subscriptions, using **the signed-in
  user's own Azure permissions**.
- It **does not store** Azure data. Results are returned to the caller and are not kept on the server.
- Prompts and model responses are **not** read or recorded unless you turn that on yourself (see Tracing).

## Foundry FinOps MCP server (Cowork / Microsoft 365 Copilot plugin)

**Sign-in.** Users sign in with Microsoft Entra ID. The server validates the access token (signature, audience,
issuer, expiry and the `access_as_user` scope) and then exchanges it, on behalf of the user, for tokens to call
Azure Resource Manager and Log Analytics. Every Azure call therefore runs as that user and can only see what their
Azure role assignments allow.

**Data the server handles while answering a request**

| Data | Source | Where it goes |
|---|---|---|
| Cost, budgets, cost controls, quota, deployments, tags, alert rules, API Management products, subscriptions and token-limit policies | Azure Resource Manager and Azure Cost Management | Returned to the calling host (Cowork / Copilot) as the tool result |
| Token and request metrics | Azure Monitor | Same |
| Log query results | Log Analytics / Application Insights, using the KQL the user or assistant supplied | Same |
| User identity claims (object ID, tenant ID, user principal name, display name) | The Entra access token | Held in memory for the request; the object ID is written to the server log |

The data returned to the host is then handled under that host's own terms (for example Microsoft 365 Copilot or
Copilot Cowork) and your tenant's settings.

**Changes.** Tools that change Azure (budgets, cost controls, tags, deployment capacity, token limits) act only when
the assistant calls them and the host confirms with the user. They are the same operations the user could perform
with their own permissions. Setting `FINOPS_READ_ONLY=1` hides and blocks every write tool.

**Server-side storage and logs**

- Azure data is not persisted. There is no database or file storage.
- An in-memory cache holds one on-behalf-of credential per signed-in token for up to about 50 minutes (and never
  beyond the token's expiry). It is discarded when the process stops. Tokens are not written to disk or logs.
- The server log records, per tool call: the tool name, the user's object ID, success or failure and the duration.
  Tool arguments and results are not logged. Application errors may include Azure error messages. Where logs go
  and how long they are kept is decided by your hosting setup (for example Azure Container Apps or App Service).
- `GET /healthz` and the OAuth metadata endpoint return no user data.

**Secrets.** The on-behalf-of exchange uses either a managed identity (recommended) or the `MCP_CLIENT_SECRET`
environment variable. Keep it in a secret store; it is never returned by any tool. The `list_subscription_secrets`
SDK call returns API Management keys, but it is not exposed as an MCP tool.

**Demo mode** (`FINOPS_DEMO=1`) uses only built-in illustrative data and makes no Azure calls.

## Python SDK, CLI and example dashboard

- The SDK talks only to Azure endpoints for your cloud (Resource Manager, Log Analytics) and to the public Azure
  Retail Prices API, which is unauthenticated and receives only a price filter, not your identity or usage.
- Credentials come from `azure-identity`; tokens are cached in memory only.
- The example dashboard binds to `127.0.0.1`, has no sign-in of its own and acts with the server's Azure identity.
  Put it behind your own authentication before sharing it.

## Tracing and usage tracking (optional)

`configure_tracing` and `UsageTracker` are off unless you call them. When used they send token counts, model names,
estimated cost and the attributes you attach (such as team or cost centre) to **your own** Application Insights
resource. **Prompt and response content recording is off by default** (`enable_content_recording=False`); turning
it on can store personal or confidential text in your telemetry, so decide that deliberately. Do not put personal
data in the attributes.

## Third parties

Beyond Microsoft services you configure (Entra ID, Azure, Application Insights, Microsoft 365 Copilot or Cowork),
the software contacts no third party. The authors receive no data.

## Retention and your rights

Because nothing is stored by the software itself, retention is determined by the services you connect (Azure Cost
Management, Azure Monitor, Application Insights, your hosting platform's logs). Access, deletion and correction
requests are handled through those services by the operator of the deployment.

## Contact

Questions about this repository: open an issue at
https://github.com/RuairiSpain/Azure-Foundry-FinOps/issues. For a specific deployment, contact the organisation
that runs it.

_Last updated: 2026-11-21._
