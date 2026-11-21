# Architecture

## Request flow

1. The browser signs the person in with Microsoft Entra (authorization code with PKCE, MSAL) and gets an access token
   for the portal's API (`access_as_user`).
2. The backend validates the token (RS256, signing keys from the tenant's JWKS, audience, issuer, scope).
3. For **reads**, the backend exchanges that token for an Azure Resource Manager token (on-behalf-of) and calls Azure as
   the person. Azure RBAC filters the result.
4. The backend then applies the person's **portal grants** (stored in SQLite). Grants only narrow what Azure returned;
   they never add access.
5. For **writes** (cost controls, budgets, tags), the `WriteGuard` requires a portal grant with write permission and
   checks the person's own Azure permission for the operation. If both allow it, the portal's managed identity performs the
   write. Every allowed and denied write is recorded in the audit log.

## Portal grants

* Roles: `admin`, `owner`, `viewer`.
* Scope: `all`, `subscription`, `resource_group`, `account`, `project`, `cost_centre`.
* Administrators add and remove users in the Admin page. A user must exist in Entra; the portal looks them up through
  Microsoft Graph (`User.Read.All`, application permission) and stores the object ID.

## Cost centres

A cost centre has rules that assign cost to it. One basis is used at a time: `project`, `agent`, `deployment`, or
`tag:<key>`. Shares for the same match must add up to 100% or less. The cost centre report allocates the cost from the
SDK's cost queries.

## Notifications

* Rules choose a trigger (`event`, `percent_used`, `state`), a scope and a channel (`webhook`, `email`, `log`).
* Foundry cost controls publish alerts to an Event Grid topic. Event Grid delivers them to
  `/api/events/cost-control-alert`, which checks a shared secret, removes duplicates, stores the event, and sends the
  matching rules.
* Webhook targets must be https and must not resolve to private addresses.

## Data

SQLite holds users, grants, cost centres and rules, notification rules, alert events and the audit log. On App Service
the file is on `/home`, which persists. SQLite allows one writer, so the plan has one instance.

## Unverified

These were built from the documentation and have not run against a tenant:

1. Whether Cost Management accepts the Foundry resource ID as a query scope.
2. The Application Insights usage schema is not documented, so cost control usage shown in the portal is an estimate.
3. The column names of a two-tag grouping in `cost_by_project_agent`.
4. The write design (portal grant plus the user's own Azure permission, performed by the service identity) is a design
   choice that you should confirm with your security team.

Other limits: the Operations page has no error or latency metrics yet; there is no async SDK client; a hostname that
changes its address after validation could still reach a private address (DNS rebinding) for webhook targets.
