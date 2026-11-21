# foundry-finops

A Pythonic SDK over the Azure REST APIs that track and control **Microsoft Foundry** spend: Foundry cost
controls, Cost Management (cost, forecast, budgets, exports), Azure Monitor token metrics, Log Analytics /
Application Insights KQL, model quota and deployment capacity, ARM tags, alert rules, and Azure API Management
AI-gateway token policies. It also includes client-side usage tracking with OpenTelemetry for per-team cost
attribution.

> Foundry's own Python SDK (`azure-ai-projects`) has no cost or budget API, and the Azure management libraries
> don't cover cost controls yet (API version `2026-11-15-preview`). This package calls the REST APIs directly with
> typed models, retries and paging.

```bash
pip install foundry-finops                 # core (requests + azure-identity)
pip install "foundry-finops[pandas]"       # Table.to_pandas()
pip install "foundry-finops[tracing]"      # OpenTelemetry / Application Insights helpers
```

## Documentation

* **[Usage guide](https://github.com/RuairiSpain/Azure-Foundry-FinOps/blob/main/foundry-finops/docs/USAGE.md)**:
  install, import, and every query type with the SDK call and the command line side by side (cost, budgets, exports,
  Foundry resources, cost controls, token metrics, KQL, quota, tags, alerts, API Management, prices, tracing, errors).
* **[API coverage review](https://github.com/RuairiSpain/Azure-Foundry-FinOps/blob/main/foundry-finops/docs/API_COVERAGE.md)**:
  which Azure cost and usage APIs (including preview ones) the SDK covers, what is missing, and what to add next.
* **[Download setup guide](https://github.com/RuairiSpain/Azure-Foundry-FinOps/blob/main/foundry-finops/docs/SETUP.md)**:
  `foundry-finops download`, roles, scheduling in GitHub Actions and troubleshooting.

## Quick start

```python
from foundry_finops import (FinOpsClient, CostControl, BudgetCategory, BudgetSharing, Threshold,
                            TimeGrain, CostQuery, Budget, BudgetNotification, LlmTokenLimit)

fo = FinOpsClient("<subscription-id>", resource_group="rg-ai")       # DefaultAzureCredential
acct = "contoso-foundry"

# 1. Real-time guardrail: $500/month per agent in one project, alert 80 %, block 100 %
conn = fo.cost_controls.find_event_grid_connection_id(acct)        # reuse Foundry's Event Grid connection
fo.cost_controls.create_or_update(acct, CostControl(
    name="fraud-risk-monthly", category=BudgetCategory.COST, amount=500, time_grain=TimeGrain.MONTHLY,
    start_date="2026-12-01", end_date="2027-07-01", filters={"project": ["fraud-risk"]},
    sharing=BudgetSharing.PER_AGENT, alert=Threshold(80, event_grid_connection_id=conn), block=Threshold(100)))

# 2. Billed cost by Foundry's automatic billing tags (project / deployment / agent.name)
by_project = fo.cost.cost_by("project", scope=fo.foundry.account_id(acct), days=30)
print(by_project.to_pandas().groupby("TagValue")["Cost"].sum())

# 3. Near-real-time tokens per deployment
tokens = fo.metrics.query_table(fo.foundry.account_id(acct), hours=24)

# 4. Cost Management budget (alerts on the bill, can't block)
fo.cost.create_or_update_budget(Budget(name="ai-platform", amount=25_000,
    notifications=[BudgetNotification(80, ["finops@contoso.com"]),
                   BudgetNotification(100, ["finops@contoso.com"], threshold_type="Forecasted")]))

# 5. Per-team token limit at the AI gateway
fo.apim.set_token_limit("contoso-ai-gateway", LlmTokenLimit(tokens_per_minute=60_000,
    token_quota=400_000_000, token_quota_period="Monthly"), scope="product:team-risk")
```

## Operation groups

| Attribute | REST surface | Highlights |
|---|---|---|
| `fo.foundry` | `Microsoft.CognitiveServices` accounts, projects, deployments, connections | `set_capacity()` (TPM), Event Grid topics |
| `fo.cost_controls` | `accounts[/projects]/costControls` (preview) | CRUD, `PATCH`, `If-None-Match`, `list_all()` across projects |
| `fo.cost` | `Microsoft.CostManagement` query, forecast, budgets, exports, tag inheritance | `CostQuery` builder, paging, `cost_by()` |
| `fo.metrics` | `Microsoft.Insights/metrics` | `FoundryMetrics` names, split by deployment/model |
| `fo.logs` | Log Analytics query API (workspace or App Insights resource) | returns `Table` |
| `fo.quota` | `locations/{region}/usages`, `models` | `QuotaUsage.percent_used` |
| `fo.tags` | `Microsoft.Resources/tags/default` | `merge()`, `replace()`, `remove()`, inventory |
| `fo.alerts` | metric alerts, scheduled query rules, action groups | typed `MetricAlert`, `LogAlert`, `ActionGroup` |
| `fo.apim` | `Microsoft.ApiManagement` products, subscriptions, policies | `LlmTokenLimit`, `LlmEmitTokenMetric`, policy upsert, inventory |
| `fo.pricing` | Azure Retail Prices API | `PriceSheet` estimates |

Every API version is overridable: `FinOpsClient(..., api_versions=ApiVersions(cost_management="2026-06-01"))`.

## Handling cost-control blocks in your app

```python
import openai
from foundry_finops import raise_for_budget, BudgetExceededError

try:
    client.responses.create(model="gpt-5.4-mini", input="…")
except openai.RateLimitError as e:
    try:
        raise_for_budget(e)            # raises BudgetExceededError if x-ms-budget-cause: budget_exceeded
    except BudgetExceededError as b:
        print("Blocked until", b.reset_at, "by", b.cost_control_id)   # don't retry
    raise                              # ordinary rate limit – retry with back-off
```

The SDK's own transport never retries a cost-control block; it retries 408/429/5xx with `Retry-After`.

## Per-team attribution with tracing

```python
from foundry_finops.tracing import configure_tracing, finops_context, UsageTracker
from foundry_finops import PriceSheet

configure_tracing(project_client=project_client)     # Foundry GenAI tracing → Application Insights
tracker = UsageTracker(PriceSheet.from_mapping({"gpt-5.4-mini": {"input": 0.25, "output": 2.0}}))  # your prices
with finops_context(team="Risk", cost_center="CC-4410"):
    resp = openai_client.responses.create(model="gpt-5.4-mini", input="…")
    tracker.record(resp, agent="fraud-case-review")
```

Ready-made KQL lives in `foundry_finops.queries`.

## Reporting facade, demo data and CLI

`foundry_finops.service` exposes one JSON-friendly interface (`LiveService` for Azure, `DemoService` with
illustrative data) used by the dashboard and the MCP connector. Try it without Azure:

```bash
foundry-finops --demo cost --by team
foundry-finops --demo kql x "CostDaily | summarize Cost=sum(Cost) by Project | order by Cost desc"

# Download everything for a project: one JSON file per query in ./FinOps/<today>/
foundry-finops --subscription <id> -g rg-ai download --account contoso-foundry --project fraud-risk \
    --start 2026-01-01 --end 2026-03-31
foundry-finops download --list                       # the query names (= the file names)
foundry-finops ... download ... --only cost_by_agent,budgets
```

### What `download` saves

* `FinOps/<today>/<query>.json` for each query, plus `_manifest.json` (parameters, and for every query: ok, skipped
  with the reason, or failed with the error). The exit code is 1 if any query failed.
* Cost data is billed cost per day, filtered to `--project` through Foundry's `project` billing tag (all projects
  when omitted). Ranges longer than a year are fetched in several windows and merged.
* Azure Monitor keeps 93 days of metrics, so older start dates are clamped (noted in the file). Metrics, quota,
  deployments and prices exist per Foundry resource or region, not per project.
* The KQL files need `--logs-target` and the gateway file needs `--apim-service` (or the `FINOPS_*` variables);
  otherwise they are skipped.
* Needs Cost Management Reader, Monitoring Reader and Reader on the resource group. If sign-in fails the command
  stops at once instead of failing every query.
* The folder holds billing data: it is listed in `.gitignore`; keep it out of source control.

## Example dashboard

`examples/dashboard` is a zero-dependency web app styled after the Azure portal and Log Analytics: overview KPIs,
cost analysis, token metrics, a KQL editor with tabs, schema pane, saved queries, results grid and chart
formatting, and create/edit/delete pages for cost controls, budgets, tags, capacity, alerts and gateway limits.
Every grid downloads as CSV/JSON and every chart as PNG. See its README.

## Permissions

Reading cost needs **Cost Management Reader**; metrics need **Monitoring Reader**; cost controls need a Foundry role to
read and Foundry Owner / Account Owner (resource) or Project Manager (project) to write; budgets need **Cost
Management Contributor**; APIM changes need **API Management Service Contributor**.

### Roles needed by `foundry-finops download`

`download` only reads, so read roles are enough. The shortest set is **Reader + Cost Management Reader + Monitoring
Reader** on the subscription, plus **Log Analytics Reader** for the KQL files.

| Queries (file names) | Role | Scope |
|---|---|---|
| `cost_*` (including `cost_by_pricing_model`, `cost_by_charge_type`, `cost_anomalies`), `cost_forecast`, `cost_dimensions`, `cost_exports`, `effective_prices`, `budgets`, `budget_status` | **Cost Management Reader** | The Foundry resource for cost queries; the resource group or subscription for budgets |
| `foundry_account`, `foundry_projects`, `foundry_deployments`, `foundry_connections`, `cost_controls`, `apim_gateway`, `tags` (account and project) | **Reader** | The resource group with the Foundry resource and the API Management instance |
| `quota_usage`, `quota_models`, `ptu_headroom`, `foundry_event_grid_topics`, tag inventory in `tags` | **Reader** | The subscription (these calls are subscription-wide) |
| `token_usage_by_*`, `estimated_cost_by_*`, `metric_definitions`, `alerts` | **Monitoring Reader** | The Foundry resource; the resource group for alerts |
| `kql_*` | **Log Analytics Reader** (workspace) or **Monitoring Reader** (Application Insights resource) | The resource that holds the telemetry |
| `retail_price_sheet` | None (public API) | n/a |

* A query without its role fails on its own (HTTP 403 in `_manifest.json`) and the others still download. A sign-in
  failure stops the whole run. To skip those queries up front, pass the identity's roles:
  `foundry-finops download ... --roles "Reader,Monitoring Reader"`, or `--roles auto` to read the real role assignments
  and scopes from Azure (see `docs/SETUP.md`).
* No data-plane roles (Cognitive Services User, Azure AI User) are needed: everything is management-plane reads.
* Cost data also depends on your billing arrangement. Enterprise Agreements need the enrollment's "view charges"
  settings enabled, Microsoft Customer Agreements need the billing profile to allow it, and CSP subscriptions need the
  partner to enable cost visibility. If cost queries return 403 or no rows with the roles above, check this first.
* Reading Foundry cost controls is a preview API. If `cost_controls` returns 403 with Reader, add a Foundry role such
  as Foundry User on the resource.
* The MCP connector runs with the signed-in user's roles, so the same table applies; its Entra app also needs the
  delegated permissions listed in `foundry-finops-cowork/docs/SETUP.md`.

## Development

```bash
python -m unittest discover -s tests -t .     # or: pytest
ruff check src tests && mypy
python -m build && twine check dist/*
```

Publishing is automated by `.github/workflows/release.yml` (PyPI trusted publishing plus a GitHub release on a `v*` tag).

## Status and caveats

* Alpha. Foundry cost controls are a public preview API; their spending totals are estimates priced with a snapshot of
  your negotiated prices and aren't reconciled with your bill.
* `PricingOperations.openai_price_sheet()` parses public retail meter names, which change over time – verify the
  result or load your own prices.
* Not an official Microsoft package.

MIT licensed.
