# Documentation conformance review

Compares the SDK with the Microsoft Learn pages for Foundry cost and fleet features (fetched 21 Nov 2026, pages last
updated 17 to 19 Nov 2026) and with the `2026-11-15-preview` Cognitive Services OpenAPI definition.

Sources (all under `https://learn.microsoft.com/en-us/azure/foundry/`): `concepts/cost-controls`,
`how-to/create-cost-controls`, `concepts/cost-management-permissions`, `how-to/view-subscription-costs`,
`how-to/view-project-costs`, `how-to/analyze-costs-cost-management`, `concepts/manage-costs`, `control-plane/monitor-*`.

Status values: **Bug** (SDK contradicts the source), **Gap** (documented, not implemented), **Unverified** (no source
confirms it, needs a live tenant), **OK** (matches). Every Bug below was reproduced against the code before the fixes. C1 to C7 are fixed (see the changelog); each has a test in `tests/test_conformance_fixes.py`.

## Bugs (fixed)

| ID | Finding | Source | SDK location | Proposed fix |
|---|---|---|---|---|
| C1 | `timePeriod` is required, with both `startDate` and `endDate`. The SDK omits it when no dates are given, so the service answers HTTP 400. The docs also say every cost control has an end date. | OpenAPI `CostControlProperties.required`, `CostControlTimePeriod.required`; Limitations | `models.py` `CostControl.validate` / `to_dict` | Require both dates, end later than start. Raise a clear `ValueError`. |
| C2 | `update()` with no properties sends `{"properties": {}}`. The spec says an empty object **clears all properties**. | OpenAPI `CostControlPatch` | `operations/cost_controls.py` `update` | Reject an empty update. Add explicit helpers to remove the alert or the block (a `null` entry deletes it). |
| C3 | Reset windows are computed wrongly. Weekly renews each **Monday 00:00 UTC** and Monthly on the **1st**, both calendar-aligned. The SDK anchors Weekly to the start date and Monthly to the start day. Reproduced: a weekly control started 4 Nov gives 18 Nov instead of Monday 16 Nov; a monthly one started 7 Nov gives 7 Nov instead of 1 Nov. Usage estimates and states use this window. | OpenAPI `CostControlTimeGrain`; create page ("Resets at ... Dec 1, 12:00 AM UTC") | `service.py` `window_start` | Use calendar-aligned UTC windows. Clip to the validity period. |
| C4 | The overview forecast falls back to month-to-date spend when no forecast is returned, so the "projected total" shows current spend. The docs say the forecast can be empty ("Not enough billing history to forecast yet"). | `how-to/view-project-costs` troubleshooting | `service.py` overview (`forecast = spend`) | Return `None` with the reason. Never substitute another number. |
| C5 | Role-name matching does not know the Foundry roles. The docs say all four Foundry roles, and Reader, can view cost controls and read usage metrics. The SDK listed only "azure ai account owner" and "azure ai owner", and "cognitive services contributor", which no page names. | `concepts/cost-management-permissions` (Foundry roles table), `control-plane/monitor-operations` prerequisites | `export.py` `_FOUNDRY`, `_MONITOR` | Add Foundry User, Foundry Project Manager, Foundry Account Owner, Foundry Owner and their former Azure AI names to the cost control and usage queries. Keep Cognitive Services Contributor, whose definition holds `Microsoft.CognitiveServices/*`, with a comment saying so. |
| C6 | The `cost_controls` query lists the account's cost controls and every project's. It needs `accounts/projects/read` and `accounts/projects/costControls/read` as well, and the usage estimate needs `Microsoft.Insights/metrics/read` and `Microsoft.CostManagement/query/read`. The requirement lists only `accounts/costControls/read`, so `--roles auto` can allow a query that then fails. | permission table (`projects/costControls/read`) | `roles.py` `QUERY_REQUIREMENTS` | Add the project permissions. Mark the usage estimate as optional (skipped without those permissions). |
| C7 | Dedupe key. The docs say to keep the first alert per source, limit, threshold and window end, **plus** agent or project only for a budget per agent or per project. The SDK always includes both, so a shared budget notifies once per agent. Reproduced. It also omits `unit` and `startDate`, which the docs use to tell cost controls on one resource apart (the event has no name). | `how-to/create-cost-controls` "Receive alerts" | `models.py` `CostControlAlertEvent.dedupe_key` | `dedupe_key_for(per)` takes the sharing mode; the key also holds `unit` and `startDate`. `dedupe_key` still includes the agent or project, so it never hides an alert for a budget per agent. |

## Unverified (needs a tenant)

| ID | Finding | Why unverified |
|---|---|---|
| U1 | The download command queries Cost Management at the **Foundry resource ID**. The OpenAPI scope lists only subscription, resource group, billing and management-group scopes. The docs say the portal reads cost at the subscription or resource group, and a role on only the Foundry resource or project "isn't enough". `roles.py` treats a role held on the Foundry resource as sufficient. | No source says the resource scope works for the Query API. If it fails, query the resource group with a `ResourceId` filter and require the role there. |
| U2 | `ptu_headroom` requires `Microsoft.CognitiveServices/locations/usages/read`. | Not on the Learn pages. It matches the Cognitive Services usages API, but it is from general knowledge. |
| U3 | The `FoundryModelEstimatedCost` preview metric used for near-real-time cost. | Not on any fetched Learn page. It came from the Azure Monitor metric definitions earlier. |
| U4 | `fo.policy` reads with `POST .../policyStates/latest/queryResults` (api `2019-10-01`, from the Policy Insights spec). The Foundry docs list `Microsoft.PolicyInsights/*/read` for the policy cards, and the SDK's role check uses that string. | Whether the Reader role can call that `POST` is not in the docs. |

## Gaps

| ID | Documented | Status |
|---|---|---|
| G1 | The cost control states. | Done, except *Needs setup*, which can't be derived from the definition. `evaluate_cost_control`, `CostControlState`. |
| G2 | Block and budget-state headers; alert fields `costBasis`, `tokensConsumed`, token counts, `callCost`, dates; one notification per threshold. | Done: `BudgetHeaders`, the extended `CostControlAlertEvent`, `AlertDeduper`. |
| G3 | Filter rules. | Done in `CostControl.validate`. |
| G4 | The account's Application Insights connection ("Usage data"). | Done: `get_usage_connection`, `set_usage_connection`, `FoundryAccount.app_insights_connection_id`. |
| G5 | `HEAD` exists check; `null` PATCH entries delete an alert or block. | Done: `exists`, `remove_alert`, `remove_block`. |
| G6 | `Microsoft.PolicyInsights/*/read` (fleet policy cards). | Done: `fo.policy`, `policy_violations`. The queries are `POST` reads; whether Reader can run them is unverified (U4). |
| G7 | Custom role "Foundry Cost Reader". | Done: `foundry_cost_reader_definition`. |
| G8 | The Application Insights schema behind "Closest to limit (estimated)". | Still blocked: the docs don't publish it. Usage stays an estimate built from billed cost or metrics. |

Also added from the plan: the project budget card (`project_budget`), the project's applicable cost controls and
`delete_project_cost_controls` (the `CostControlInUse` guard).

## Verified OK

* Cost control name rule (1 to 63 letters, numbers, hyphens, underscores), categories `Cost` and `Tokens`, time grains
  Daily, Weekly, Monthly, filter names and `In` operator, grouping (one entry; `project` only for account-owned).
* At most one alert and one Block action; percentage up to 100; absolute up to the amount; whole numbers for tokens;
  alert at or below block; a session filter takes at most 20 IDs; an agent filter needs exactly one project.
* `PUT` replace, `If-None-Match: *` create-only, `If-Match` with the entity tag, `PATCH`, `DELETE`, project path form,
  immutable `category`, 409 mapped to `ConflictError`, `FeatureNotRegistered` hint.
* Blocked requests: 429 with `x-ms-budget-cause: budget_exceeded` is never retried; both policy-ID header names are
  read; `x-ms-budget-window-reset` is exposed; streamed blocks are detected by the message text.
* Billing tags `project`, `deployment` and `agent.name`; cost lag of 8 to 24 hours; Cost Management query and forecast
  with actual cost included; Cost Management Reader scope behaviour is described in the docs but see U1.
