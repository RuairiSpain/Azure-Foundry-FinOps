# Changelog

## 0.1.0 – 2026-11-21

First release.

### Fixes (documentation conformance review, `docs/DOCS_CONFORMANCE.md`)
- A cost control now needs both `start_date` and `end_date` (the service requires a validity period; before, the SDK
  sent none and the service answered HTTP 400). The MCP tool requires them too.
- `cost_controls.update()` with no properties raises. The service treats an empty `properties` object as "clear every
  property".
- Usage windows are calendar-aligned in UTC (Daily 00:00, Weekly Monday, Monthly the 1st) instead of being anchored to
  the start date. A control counts from its start date, and a control that has not started or has ended says so.
- The overview forecast is `None` with `forecast_note` when Cost Management returns none; it no longer shows current
  spend as the forecast.
- `--roles`: the four Foundry roles (and their former Azure AI names) now cover cost controls and usage metrics.
  The `cost_controls` query also requires the project permissions (`accounts/projects/read`,
  `accounts/projects/costControls/read`).
- `CostControlAlertEvent.dedupe_key_for(per)` adds the agent or project only for a budget per agent or per project and
  includes the unit and start date. Events also expose `start_date` and `end_date`.

### Features
- Gaps from the API review closed with the existing APIs (see `docs/API_COVERAGE.md`, section 8):
  `estimated-cost` (preview `FoundryModelEstimatedCost` metric) and cost-control usage from it; effective prices and the
  implied discount from your billing (`prices --source billed --compare`); a discount percentage you supply
  (`PriceSheet.scaled`, `prices --discount-percent`, `download --discount-percent`); `anomalies`; budget threshold status;
  PTU headroom (`ptu`); shared-cost `allocate`; saved queries (`query`); OR/NOT filters, quantity and named time frames;
  full export options with `get_export` and `run_export_and_wait`; `etag`/`if_match` on cost controls; new `Deployment`
  fields; Cost Management `2026-06-01` and Cognitive Services `2026-11-01`. New download files:
  `estimated_cost_by_project`, `estimated_cost_by_deployment`, `cost_by_pricing_model`, `cost_by_charge_type`,
  `cost_anomalies`, `effective_prices`, `budget_status`, `ptu_headroom`.
- Review of the Azure cost and usage APIs against the SDK (`docs/API_COVERAGE.md`), with a prioritised list of additions.
- `foundry-finops cost --scope` queries any scope (subscription, resource group, resource, management group, billing
  scope), and `--by` accepts any tag name or Cost Management dimension as well as the five Foundry groups.
- Usage guide (`docs/USAGE.md`): install, import, and every query type with the SDK call and CLI command side by
  side. A test checks that every sample refers to real imports, methods, arguments and CLI flags.
- First release: `FinOpsClient` with foundry, cost_controls, cost, metrics, logs, quota, tags, alerts, apim and
  pricing operation groups.
- Typed models with ARM round-tripping; `Table` with CSV/JSON/pandas export.
- Retry policy honouring `Retry-After`; cost-control blocks (`x-ms-budget-cause`) raise `BudgetExceededError`
  and are never retried.
- APIM `llm-token-limit` / `llm-emit-token-metric` builders and policy upsert.
- OpenTelemetry usage tracking (`finops_context`, `UsageTracker`) and KQL library.
- Reporting facade (`service`), illustrative demo data with a KQL-subset engine, and a CLI.

- `foundry-finops download --roles auto` reads the signed-in identity's role assignments and role definitions from
  Azure and runs only the queries its permissions allow at the scope each query uses (custom roles, `notActions`,
  group-inherited roles and management-group assignments included). New `fo.authorization` operations and
  `foundry_finops.roles`.
- `foundry-finops download --roles "Reader,Monitoring Reader"` limits the download to the queries those Azure roles
  allow and records the others as skipped with the roles that would allow them. Without the switch everything is
  tried. `download --list --roles ...` is a dry run.
- `foundry-finops download`: runs every read query the SDK offers (daily cost by project/agent/deployment/team/
  model/meter, forecast, budgets, exports, cost controls with usage, deployments, token metrics, quota, tags,
  alerts, API Management, retail prices and the saved KQL queries) for a start/end date and saves one JSON file per
  query to `FinOps/<today>/<query>.json`, plus `_manifest.json` listing what succeeded, was skipped or failed.

### Hardening and fixes from the code review
- Foundry cost controls allow **one** alert and **one** Block action (the preview API answers HTTP 400 otherwise);
  this is validated, and absolute Token thresholds must be whole numbers.
- `foundry.set_capacity` sends a PATCH of `sku.capacity`; the earlier full PUT reset properties it didn't know about
  (service tier, spillover deployment, paused state).
- Cost Management budgets keep periods such as `Last7Days` and `BillingMonth` instead of rewriting them to `Monthly`.
**Security / integration**
- Credentials can no longer be sent to a non-ARM host: absolute URLs and `..` in caller-supplied scopes are
  refused; user-supplied names are percent-encoded as single path segments; OData filter values are escaped.
- `DefaultAzureCredential` now uses the sovereign-cloud authority for `US_GOV_CLOUD` / `CHINA_CLOUD`.
- APIM subscription scopes (`/products/x`, `/apis`) are expanded to the full resource ID the service expects.
- Dashboard: Host-header allow-list (DNS rebinding), body-size and Content-Length handling, socket timeout.
- MCP server: tool arguments are validated against `inputSchema` server-side; per-request Azure clients are
  closed (socket leak); `Origin` check when auth is off; 403 challenges use `insufficient_scope`;
  malformed JSON-RPC/Content-Length no longer kills a request thread; credential cache is bounded and keyed by hash.

**Correctness**
- Long-running operations finish on `204`/`200` from a `Location` poll (deleting a deployment used to wait 10 min).
- Deployment create/capacity changes wait for a terminal `provisioningState`.
- One automatic retry on `401` with a fresh token; token-acquisition failures raise `AuthenticationError`;
  `Retry-After` HTTP dates and the Cost Management rate-limit headers are honoured; paging loops are detected.
- `CostQuery.last_days` returns whole calendar days (the first day was almost entirely dropped).
- `create_or_update_export` no longer fails on 29 February; `datetime.utcnow()` removed.
- Cost-control status compares absolute thresholds as a percentage of the amount.
- Price lookup no longer maps `gpt-4o-mini` to `gpt-4o`; cached tokens are clamped to input tokens.
- Budgets are validated client-side (name, amount, contact required, max 5 per type); cost controls reject
  Quarterly/Annually; unknown service enum values no longer break listing.
- CLI: never silently falls back to demo data without `--demo`; works for `cost-controls`/`tokens` without an
  explicit account; friendly errors and exit codes; CSV for list results.
- Live overview degrades per section (`warnings`) instead of failing; forecast uses daily granularity;
  unsupported `group_by` values are rejected instead of mislabelled.

**Follow-up improvements**
- Cost controls report an estimated `usage` (with `usage_note`) per reset window: Cost budgets from billed cost
  filtered by the control's project/deployment/agent tags, Tokens budgets from Azure Monitor metrics. Controls that
  can't be reproduced (per-session, project/agent token budgets) stay "Usage unknown" with a reason.
- Alerts reuse the Event Grid connection of an existing cost control when none is given.
- APIM writes use the ETag they read (`If-Match: <etag>`) instead of `*`, so a concurrent edit yields a conflict
  instead of being overwritten; creates send no `If-Match`.
- Publishing is refused while `pyproject.toml` still contains `<your-org>`; `configure_plugin.py` warns about
  placeholder manifest URLs.

**Tooling**
- `hatchling>=1.27` (required for SPDX `license` + `license-files`); ruff and mypy now pass and mypy gates CI.
- `scripts/configure_plugin.py` builds the package before bumping the manifest version.
- Cowork Dockerfile builds from the suite root so the local SDK is installed; adds a health check.
