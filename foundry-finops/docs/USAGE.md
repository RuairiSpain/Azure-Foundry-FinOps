# Using foundry-finops: install, import and every query type

This guide shows how to install the package, import it, and run every kind of query from Python (the SDK) and from the
command line, side by side. Copy any part you need.

* [1. Install](#1-install)
* [2. Sign in and permissions](#2-sign-in-and-permissions)
* [3. Import and create a client](#3-import-and-create-a-client)
* [4. Working with results](#4-working-with-results)
* [5. Query types: SDK and CLI side by side](#5-query-types-sdk-and-cli-side-by-side)
  * [Billed cost](#51-billed-cost) · [Budgets and exports](#52-cost-management-budgets-and-exports) ·
    [Foundry resources](#53-foundry-accounts-projects-and-deployments) · [Cost controls](#54-foundry-cost-controls) ·
    [Token metrics](#55-token-usage-azure-monitor-metrics) · [KQL logs](#56-logs-kql) · [Quota](#57-quota) ·
    [Tags](#58-tags) · [Alerts](#59-alerts) · [API Management](#510-api-management-ai-gateway) ·
    [Prices](#511-retail-prices-and-cost-estimates) · [Your roles](#512-your-azure-roles) ·
    [Reports from API data](#513-reports-computed-from-api-data-anomalies-allocation-ptu)
* [6. Track usage from your own code (tracing)](#6-track-usage-from-your-own-code-tracing)
* [7. Handle errors and budget blocks](#7-handle-errors-and-budget-blocks)
* [8. Download everything](#8-download-everything)
* [9. Reporting facade and demo mode](#9-reporting-facade-and-demo-mode)
* [10. Command line reference](#10-command-line-reference)
* [11. Cheat sheet: one row per query type](#11-cheat-sheet-one-row-per-query-type)

Samples use placeholders such as `<subscription-id>`, `rg-ai` and `contoso-foundry`. Replace them with your own values.
Billed cost lags usage by 8 to 24 hours; token metrics are near real time.

---

## 1. Install

You need Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install foundry-finops
foundry-finops --help                # the command line is installed with the package
python -c "import foundry_finops; print(foundry_finops.__version__)"
```

Optional extras add features that need more libraries:

| Extra | Install | Adds |
|---|---|---|
| `pandas` | `pip install "foundry-finops[pandas]"` | `Table.to_pandas()` for DataFrames |
| `tracing` | `pip install "foundry-finops[tracing]"` | OpenTelemetry export to Application Insights ([section 6](#6-track-usage-from-your-own-code-tracing)) |
| `dashboard` | `pip install "foundry-finops[dashboard]"` | FastAPI and uvicorn, for building your own web front end |
| `dev` | `pip install "foundry-finops[dev]"` | pytest, ruff, mypy and build tools, for contributors |

Combine them: `pip install "foundry-finops[pandas,tracing]"`.

Upgrade with `pip install --upgrade foundry-finops`. To run the latest unreleased code instead:

```bash
git clone https://github.com/RuairiSpain/Azure-Foundry-FinOps.git
pip install ./Azure-Foundry-FinOps/foundry-finops
```

## 2. Sign in and permissions

The SDK and CLI use `DefaultAzureCredential`, which finds your sign-in in this order: environment variables for a
service principal, workload identity, managed identity, your `az login` session, and a few more. The simplest start on a
laptop is:

```bash
az login
az account set --subscription <subscription-id>
```

The signed-in identity needs read roles. The short version is **Reader + Cost Management Reader + Monitoring Reader** on
the subscription, plus **Log Analytics Reader** for KQL. The roles for each query are listed in the
[README](../README.md#roles-needed-by-foundry-finops-download) and in [SETUP.md](SETUP.md). Writing (budgets, cost
controls, tags, deployments, APIM) needs matching contributor roles.

## 3. Import and create a client

Everything starts from `FinOpsClient`. It has one attribute for each group of queries:

| Attribute | What it covers |
|---|---|
| `fo.cost` | Cost Management: cost queries, forecast, budgets, exports |
| `fo.foundry` | Foundry accounts, projects, deployments, connections |
| `fo.cost_controls` | Foundry cost controls (real-time budgets that alert or block) |
| `fo.metrics` | Azure Monitor token and request metrics |
| `fo.logs` | KQL against Log Analytics or Application Insights |
| `fo.quota` | Regional model quota (TPM) |
| `fo.tags` | Azure resource tags |
| `fo.alerts` | Metric alerts, log alerts, action groups |
| `fo.apim` | API Management AI gateway: products, subscriptions, token-limit policies |
| `fo.pricing` | Azure retail prices and token cost estimates |
| `fo.authorization` | Your role assignments |

```python
from foundry_finops import FinOpsClient

fo = FinOpsClient("<subscription-id>", resource_group="rg-ai")
print(fo)                                    # FinOpsClient(subscription_id='...', resource_group='rg-ai')
```

Use it as a context manager so the HTTP session is closed:

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    for project in fo.foundry.list_projects("contoso-foundry"):
        print(project.name)
```

`resource_group` is the default for calls that need one. Most methods also accept `resource_group=` to override it.

### Choosing how to authenticate

```python
from azure.identity import AzureCliCredential, ClientSecretCredential, ManagedIdentityCredential
from foundry_finops import FinOpsClient, StaticTokenCredential

sub = "<subscription-id>"

fo = FinOpsClient(sub)                                                   # DefaultAzureCredential (the default)
fo = FinOpsClient(sub, AzureCliCredential())                             # your `az login`
fo = FinOpsClient(sub, ManagedIdentityCredential())                      # inside Azure
fo = FinOpsClient(sub, ClientSecretCredential("<tenant-id>", "<client-id>", "<client-secret>"))
fo = FinOpsClient(sub, StaticTokenCredential("<bearer-token>"))          # a token you already have (tests, scripts)
```

Any object with a `get_token(*scopes)` method works as a credential. Don't hard-code secrets: read them from the
environment or a secret store.

### Options

```python
import requests

from foundry_finops import US_GOV_CLOUD, ApiVersions, FinOpsClient, RetryPolicy

session = requests.Session()
session.proxies = {"https": "http://proxy.contoso.com:8080"}

fo = FinOpsClient(
    "<subscription-id>",
    resource_group="rg-ai",
    cloud=US_GOV_CLOUD,                                    # PUBLIC_CLOUD (default), US_GOV_CLOUD or CHINA_CLOUD
    api_versions=ApiVersions(cost_management="2025-03-01"),  # override any REST API version
    retry=RetryPolicy(total=8, backoff_factor=1.0, max_backoff=90),
    timeout=120,                                           # seconds per request
    session=session,                                       # proxies, custom adapters, tests
)
```

Requests that fail with 408, 429 or 5xx are retried, honouring `Retry-After`. A 429 caused by a Foundry cost-control
block is never retried ([section 7](#7-handle-errors-and-budget-blocks)).

Useful top-level imports (everything below is importable from `foundry_finops`):

```python
from foundry_finops import (
    FinOpsClient, PUBLIC_CLOUD, US_GOV_CLOUD, CHINA_CLOUD, ApiVersions, RetryPolicy, StaticTokenCredential,
    # models
    Budget, BudgetNotification, BudgetCategory, BudgetSharing, CostControl, Threshold, ThresholdType, TimeGrain,
    CostQuery, Granularity, Deployment, MetricAlert, LogAlert, ActionGroup, ApimProduct, ApimSubscription, Table,
    # APIM policy builders and prices
    LlmTokenLimit, LlmEmitTokenMetric, PriceSheet, ModelPrice, FoundryMetrics,
    # ids and errors
    foundry_account_id, foundry_project_id, ResourceId,
    FinOpsError, HttpResponseError, AuthenticationError, ForbiddenError, NotFoundError, ConflictError,
    ThrottledError, BudgetExceededError, ServiceError, is_budget_exceeded, raise_for_budget,
)
```

## 4. Working with results

Cost, metrics and log queries return a `Table`. It is iterable and converts to the formats you need:

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    table = fo.cost.cost_by("project", scope=fo.foundry.account_id("contoso-foundry"), days=7)

    print(table.column_names)          # ['Cost', 'UsageDate', 'TagKey', 'TagValue', 'Currency']
    print(len(table))                  # number of rows
    for row in table:                  # one dict per row
        print(row["UsageDate"], row["TagValue"], row["Cost"])

    records = table.to_records()       # list of dicts
    csv_text = table.to_csv()          # CSV string
    as_json = table.to_json()          # {"name": ..., "columns": [...], "rows": [...]}
    frame = table.to_pandas()          # pandas.DataFrame (needs the pandas extra)
```

Other methods return typed objects (`CostControl`, `Budget`, `Deployment` and so on). Each has `to_json()` for a
JSON-friendly dict, and the original service response is kept in `.raw`:

```python
import json

from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    for dep in fo.foundry.list_deployments("contoso-foundry"):
        print(json.dumps(dep.to_json(), indent=2))
```

`list_*` methods return iterators that follow paging for you. Wrap them in `list(...)` if you need a list.

---

## 5. Query types: SDK and CLI side by side

The command line covers the reports you use most. For everything else, `foundry-finops download` saves the same data
as JSON files ([section 8](#8-download-everything)), and the SDK does it all. Each part below shows the SDK first and
the command line after it.

CLI commands need `--subscription` and `-g` before the sub-command, or the environment variables
`FINOPS_SUBSCRIPTION_ID` and `FINOPS_RESOURCE_GROUP`:

```bash
export FINOPS_SUBSCRIPTION_ID=<subscription-id>
export FINOPS_RESOURCE_GROUP=rg-ai
export FINOPS_FOUNDRY_ACCOUNT=contoso-foundry
```

### 5.1 Billed cost

Foundry stamps its model charges with billing tags (`project`, `deployment`, `agent.name`) in Cost Management. To group
by those tags, query at the Foundry resource's scope.

**Cost grouped by a tag or dimension**

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    account_id = fo.foundry.account_id("contoso-foundry")

    by_project = fo.cost.cost_by("project", scope=account_id, days=30)               # Foundry project tag
    by_agent = fo.cost.cost_by("agent.name", scope=account_id, days=30)              # agent tag
    by_deployment = fo.cost.cost_by("deployment", scope=account_id, days=30)         # model deployment tag
    by_team = fo.cost.cost_by("team", scope=account_id, days=30)                     # your own tag
    by_model = fo.cost.cost_by("MeterSubCategory", scope=account_id, days=30)        # a dimension (capital letter)
    totals = fo.cost.cost_by("project", scope=account_id, days=30, daily=False)      # one row per group
    january = fo.cost.cost_by("project", scope=account_id, start="2026-01-01", end="2026-01-31")
    print(by_project.to_csv())
```

A name that starts with a capital letter is treated as a Cost Management dimension. For a tag whose name is
capitalised, pass `dimension=False`.

**Build your own query**

```python
from foundry_finops import CostQuery, FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    account_id = fo.foundry.account_id("contoso-foundry")

    q = (
        CostQuery.between("2026-01-01", "2026-03-31")      # or CostQuery.last_days(30) / CostQuery.month_to_date()
        .daily()                                           # or .total() for one row per group
        .group_by_tag("project")                           # group by a billing tag
        .group_by("MeterSubCategory")                      # and by a dimension
        .where_tag("deployment", ["gpt-5.4-mini"])         # filter by tag values
        .where("ResourceGroupName", ["rg-ai"])             # filter by dimension values
    )
    table = fo.cost.query(q, scope=account_id)
    print(table.to_records()[:3])

    amortized = CostQuery.month_to_date().amortized().total()   # amortized instead of actual cost
```

`scope` can be a subscription (`/subscriptions/<id>`), a resource group, a single resource ID, a management group or a
billing scope. Without it the client's resource group (or subscription) is used.

**Forecast and dimensions**

```python
from foundry_finops import CostQuery, FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    account_id = fo.foundry.account_id("contoso-foundry")

    future = CostQuery.between("2026-12-01", "2026-12-31").daily()
    forecast = fo.cost.forecast(future, scope=account_id)               # actual to date plus forecast
    names = fo.cost.dimensions(scope=account_id)                        # which dimensions you can group or filter by
```

**Named time frames, OR and NOT filters, quantity, and saved queries**

```python
from foundry_finops import CostQuery, FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    account_id = fo.foundry.account_id("contoso-foundry")

    last_month = CostQuery.last_month().group_by_tag("project")        # also week_to_date, billing_month_to_date,
    table = fo.cost.query(last_month, scope=account_id)                #      last_billing_month, current_month

    q = (
        CostQuery.month_to_date()
        .where_any(("tag", "team", ["risk"]), ("dimension", "ResourceGroupName", ["rg-ai"]))   # team=risk OR rg=rg-ai
        .where_not("tag", "env", ["dev"])                                                       # AND NOT env=dev
        .quantity()                                                                             # also return UsageQuantity
    )
    print(fo.cost.query(q, scope=account_id).to_records()[:3])

    q.save("finops-query.json")                      # a saved "view" you can keep in source control
    same = CostQuery.load("finops-query.json")       # or CostQuery.from_dict(body) for a raw request body
```

A query can group by at most two things; a third raises `ValueError` before anything is sent.

**Reservations and savings plans**: group by the `PricingModel` or `ChargeType` dimension to see on-demand against
reserved cost, and purchases against usage (`fo.cost.dimensions(scope=...)` lists the dimensions your scope supports):

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    account_id = fo.foundry.account_id("contoso-foundry")
    print(fo.cost.cost_by("PricingModel", scope=account_id, days=30, daily=False).to_records())
    print(fo.cost.cost_by("ChargeType", scope=account_id, days=30, daily=False).to_records())
```

**CLI**

```bash
foundry-finops cost --by PricingModel --account contoso-foundry          # on-demand vs reservation vs savings plan
foundry-finops cost --by ChargeType --account contoso-foundry
foundry-finops query finops-query.json --scope /subscriptions/<subscription-id>/resourceGroups/rg-ai   # a saved query
# Billed cost per day, by project / agent / deployment / model / team (last 30 days by default)
foundry-finops cost --by project --account contoso-foundry
foundry-finops cost --by agent --days 7 --account contoso-foundry
foundry-finops --format csv cost --by team --days 90 --account contoso-foundry > cost-by-team.csv
foundry-finops --format json cost --by model --account contoso-foundry

# Any scope with --scope: a subscription, resource group, resource ID, management group or billing scope
foundry-finops cost --by ResourceGroupName --scope /subscriptions/<subscription-id>            # a dimension (capitalised)
foundry-finops cost --by cost-center --scope /subscriptions/<subscription-id>/resourceGroups/rg-ai   # any tag name
foundry-finops cost --by project --scope /subscriptions/<subscription-id>/resourceGroups/rg-ai/providers/Microsoft.CognitiveServices/accounts/contoso-foundry
```

`--by` takes `project`, `agent`, `deployment`, `model` or `team`, or any tag name (lower case) or Cost Management
dimension (capitalised). Without `--scope`, the Foundry tags use the Foundry resource (`--account`) and everything else
uses the resource group. The `project` tag only exists at the Foundry resource's scope, so pass that resource ID as
`--scope` to group by project from another account. `--scope` can't be combined with `--demo`.

The `--format` option (`table`, `json` or `csv`) goes before the sub-command.

### 5.2 Cost Management budgets and exports

Budgets alert on billed cost. They cannot block requests (use [cost controls](#54-foundry-cost-controls) for that).

**Budgets**

```python
from foundry_finops import Budget, BudgetNotification, FinOpsClient, TimeGrain

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    for b in fo.cost.list_budgets():                                    # at the client's resource group scope
        print(b.name, b.amount, b.current_spend, b.forecast_spend, b.currency)

    budget = fo.cost.get_budget("ai-platform")
    print(budget.etag)                                                  # keep it to avoid overwriting concurrent edits

    fo.cost.create_or_update_budget(Budget(
        name="ai-platform",
        amount=25_000,
        time_grain=TimeGrain.MONTHLY,
        start_date="2026-12-01",                                        # first day of a month
        filter={"tags": {"name": "team", "operator": "In", "values": ["Support"]}},    # optional
        notifications=[
            BudgetNotification(80, ["finops@contoso.com"]),                                   # actual spend at 80 %
            BudgetNotification(100, ["finops@contoso.com"], threshold_type="Forecasted"),     # forecast reaches 100 %
        ],
    ))
    fo.cost.delete_budget("ai-platform")

    # Budgets at another scope
    sub_budgets = list(fo.cost.list_budgets(scope="/subscriptions/<subscription-id>"))
```

Each notification needs at least one email, action group ID or role to notify.

**Which thresholds has the spend reached?** The service returns a budget's current and forecast spend, so the SDK can
compare them with each notification's threshold. It makes no extra call and says "unknown" (`None`) when the service
reported no spend. It is the budget's own arithmetic, not a record of emails that were sent:

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    for b in fo.cost.list_budgets():
        print(b.name, b.percent_used(), b.percent_forecast())
        for n in b.notification_status():
            print("  ", n["notification"], n["type"], n["threshold_percent"], "reached" if n["reached"] else n["reached"])
```

**Scheduled exports to storage (the recommended way to get bulk billing data)**

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    fo.cost.create_or_update_export(
        "foundry-daily",
        storage_account_id="/subscriptions/<subscription-id>/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/finopsdata",
        container="costexports",
        root_folder="foundry-finops",
        recurrence="Daily",
        cost_type="ActualCost",
        timeframe="MonthToDate",
    )
    print(fo.cost.list_exports())
    print(fo.cost.get_export("foundry-daily"))                 # one export, with its eTag
    fo.cost.run_export("foundry-daily")                        # start a run and return
    run = fo.cost.run_export_and_wait("foundry-daily")         # or wait for it: status, fileName, manifestFile, ...
    print(fo.cost.export_history("foundry-daily"))
    fo.cost.delete_export("foundry-daily")

    # Every documented option: FOCUS format, Parquet, compression, partitioning, overwrite behaviour, columns
    fo.cost.create_or_update_export(
        "foundry-focus",
        storage_account_id="/subscriptions/<subscription-id>/resourceGroups/rg-data/providers/Microsoft.Storage/storageAccounts/finopsdata",
        container="costexports",
        cost_type="FocusCost",                  # Usage, ActualCost, AmortizedCost, FocusCost, PriceSheet, Reservation*
        file_format="Parquet",                  # Csv or Parquet
        compression="snappy",                   # gzip, snappy or none
        partition_data=True,
        overwrite_behavior="CreateNewReport",   # or OverwritePreviousReport
        description="Daily FOCUS export for FinOps",
        granularity="Daily",                    # or Monthly
        recurrence="Daily",                     # Daily, Weekly, Monthly or Annually
    )

    # Apply resource-group and subscription tags to cost data (EA, MCA and subscription scopes only)
    fo.cost.set_tag_inheritance(True, scope="/subscriptions/<subscription-id>")
```

**CLI**

```bash
foundry-finops budgets                                   # amount, current and forecast spend, percent used
foundry-finops --format json budgets
foundry-finops download --only budgets,budget_status,cost_exports --start 2026-01-01 --end 2026-03-31
```

### 5.3 Foundry accounts, projects and deployments

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    acct = "contoso-foundry"

    for a in fo.foundry.list_accounts():                    # Foundry (AIServices) resources in the resource group
        print(a.name, a.location, a.kind, a.sku)
    account = fo.foundry.get_account(acct)
    account_id = fo.foundry.account_id(acct)                # the ARM resource ID, used as a scope elsewhere

    for p in fo.foundry.list_projects(acct):
        print(p.name, p.display_name)
    project = fo.foundry.get_project(acct, "fraud-risk")

    for d in fo.foundry.list_deployments(acct):
        print(d.name, d.model_name, d.model_version, d.sku_name, d.capacity, d.tokens_per_minute)
        print("  ", d.is_provisioned, d.service_tier, d.deployment_state, d.spillover_deployment)
    deployment = fo.foundry.get_deployment(acct, "gpt-5.4-mini")

    for conn in fo.foundry.list_connections(acct):          # raw dicts; includes the Event Grid connection for alerts
        print(conn.get("name"))
    topics = list(fo.foundry.list_event_grid_topics())      # Event Grid topics in the subscription
```

**Change a deployment's capacity (its share of regional quota)**

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    fo.foundry.set_capacity("contoso-foundry", "gpt-5.4-mini", capacity=50)        # 50 = 50,000 TPM on Standard SKUs

    fo.foundry.create_or_update_deployment(
        "contoso-foundry", "gpt-5.4-mini-eu",
        model_name="gpt-5.4-mini", model_version="2026-03-01", sku_name="GlobalStandard", capacity=20,
        tags={"team": "risk"},
    )
    fo.foundry.delete_deployment("contoso-foundry", "gpt-5.4-mini-eu")
```

These calls wait until Azure finishes (`wait=True`). Pass `wait=False` to return immediately.

`set_capacity` changes only `sku.capacity` (a PATCH), so a `Priority` service tier, a spillover deployment or a paused
state stay as they were. For provisioned SKUs (`ProvisionedManaged`, `GlobalProvisionedManaged`, ...) the capacity is in
PTUs and `tokens_per_minute` is `None`: PTUs are not converted to tokens. `service_tier`, `deployment_state` and
`spillover_deployment` need the 2026-11-01 API version or newer (the default).

**CLI**

```bash
foundry-finops tokens contoso-foundry                    # token usage per deployment (section 5.5)
foundry-finops download --only foundry_account,foundry_projects,foundry_deployments,foundry_connections \
    --start 2026-01-01 --end 2026-03-31
```

### 5.4 Foundry cost controls

Cost controls are real-time budgets that alert (through Event Grid) or block requests (HTTP 429) when spend or tokens
reach a threshold. This is a preview API. Changes take effect within about 15 minutes.

**Read**

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    acct = "contoso-foundry"

    for cc in fo.cost_controls.list(acct):                              # the Foundry resource's own controls
        print(cc.name, cc.category, cc.amount, cc.time_grain, cc.filters, cc.alerts, cc.blocks)
    project_controls = list(fo.cost_controls.list(acct, project="fraud-risk"))
    everything = list(fo.cost_controls.list_all(acct))                  # resource level plus every project
    one = fo.cost_controls.get(acct, "fraud-risk-monthly", project="fraud-risk")
    conn_id = fo.cost_controls.find_event_grid_connection_id(acct)      # reuse the connection Foundry already made
```

**Create or update**

```python
from foundry_finops import BudgetCategory, BudgetSharing, CostControl, FinOpsClient, Threshold, TimeGrain

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    acct = "contoso-foundry"
    conn_id = fo.cost_controls.find_event_grid_connection_id(acct)

    # $500 a month for each agent in one project: alert at 80 %, block at 100 %.
    # The service allows one alert and one Block action per cost control.
    fo.cost_controls.create_or_update(
        acct,
        CostControl(
            name="fraud-risk-monthly",
            category=BudgetCategory.COST,                       # or BudgetCategory.TOKENS
            amount=500,
            time_grain=TimeGrain.MONTHLY,                       # Daily, Weekly or Monthly
            start_date="2026-12-01",                            # both dates are required (UTC)
            end_date="2027-07-01",                              # counting stops at the start of this day
            filters={"project": ["fraud-risk"]},                # project, deployment, agent, sessionId, tokenType
            sharing=BudgetSharing.PER_AGENT,                    # SHARED, PER_AGENT, PER_SESSION, PER_PROJECT
            alert=Threshold(80, event_grid_connection_id=conn_id),
            block=Threshold(100),
        ),
        project="fraud-risk",                                   # create it inside the project
        only_if_new=True,                                       # fail with ConflictError if it already exists
    )

    # Change one property. The budget type (category) can't be changed. Pass at least one property: the service
    # treats an empty update as "clear everything", so the SDK refuses it.
    fo.cost_controls.update(acct, "fraud-risk-monthly", project="fraud-risk", amount=750)

    # Optimistic concurrency: change or delete only if nobody edited it since you read it
    current = fo.cost_controls.get(acct, "fraud-risk-monthly", project="fraud-risk")
    fo.cost_controls.update(acct, "fraud-risk-monthly", project="fraud-risk", amount=800, if_match=current.etag)
    fo.cost_controls.delete(acct, "fraud-risk-monthly", project="fraud-risk", if_match=current.etag)
```

Create cost controls one at a time: concurrent creates on one Foundry resource can fail with HTTP 409. A cost control
has **at most one alert and one Block action**. Filters on `agent` or `sessionId` need the feature
`Microsoft.CognitiveServices/CostControlsByIdentity` registered on the subscription; without it the service answers
403 `FeatureNotRegistered` and the SDK adds the registration command to the error message.

**Cost control with estimated usage (reporting facade)**

Foundry has no usage API for cost controls, so `LiveService` estimates usage for the current reset window. For Cost
budgets that are shared and filter at most on project and deployment it uses the near-real-time
`FoundryModelEstimatedCost` metric ([section 5.5](#55-token-usage-azure-monitor-metrics)); the project name is matched
against the metric's own `ProjectId` values, and if it can't be matched it uses billed cost instead of guessing (8 to 24
hours behind). Token budgets use the token metrics. `usage_note` says which source was used:

```python
from foundry_finops.service import LiveService, Settings

svc = LiveService(Settings(subscription_id="<subscription-id>", resource_group="rg-ai", foundry_account="contoso-foundry"))
for row in svc.list_cost_controls():
    print(row["name"], row["usage"], row["percent_used"], row["state"], row.get("usage_note"))
```

**CLI**

```bash
foundry-finops cost-controls contoso-foundry             # includes estimated usage, percent used and state
foundry-finops --format json cost-controls contoso-foundry
```

### 5.5 Token usage (Azure Monitor metrics)

Metrics are near real time and available per Foundry resource and deployment (not per project). They are kept for 93
days.

```python
from foundry_finops import FinOpsClient, FoundryMetrics

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    account_id = fo.foundry.account_id("contoso-foundry")

    # Tokens in the last 24 hours, one series per deployment
    series = fo.metrics.query(account_id, FoundryMetrics.TOKENS, hours=24)
    for s in series:
        print(s.metric, s.dimensions, s.total)               # e.g. TokenTransaction {'modeldeploymentname': 'gpt'} 123456

    # Requests per model, hourly, for a date range
    by_model = fo.metrics.query(
        account_id,
        [FoundryMetrics.REQUESTS, FoundryMetrics.TOTAL_TOKENS],
        start="2026-11-01", end="2026-11-07",
        interval="PT1H", aggregation="Total",
        split_by=[FoundryMetrics.MODEL_DIMENSION],           # FoundryMetrics.DEPLOYMENT_DIMENSION is the default
    )

    table = fo.metrics.query_table(account_id, hours=48)     # the same data as a flat Table, one row per point
    print(table.to_csv())

    definitions = fo.metrics.definitions(account_id)         # every metric the resource exposes
```

**Near-real-time estimated cost** per project, deployment or model comes from the `FoundryModelEstimatedCost` metric
(Preview). Microsoft documents it as an *estimate in USD for Foundry models*, not an invoice: billed cost from Cost
Management stays the system of record. Dimension values are returned exactly as Azure reports them:

```python
from foundry_finops import FinOpsClient, FoundryMetrics

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    account_id = fo.foundry.account_id("contoso-foundry")

    totals = fo.metrics.estimated_cost_totals(account_id, hours=24)                       # per ProjectId, highest first
    by_deploy = fo.metrics.estimated_cost_totals(account_id, hours=24, by=(FoundryMetrics.DEPLOYMENT_DIMENSION,))
    both = fo.metrics.estimated_cost_totals(account_id, hours=24, by=("ProjectId", "ModelDeploymentName"))
    over_time = fo.metrics.estimated_cost(account_id, hours=24, interval="PT1H")          # one record per interval
    print(totals[:3], over_time[:2])
```

Provisioned (PTU) utilisation, cached tokens and more are ordinary metrics: `FoundryMetrics.PROVISIONED_UTILIZATION_V2`,
`ACTIVE_TOKENS`, `CACHE_MATCH_RATE`, `MODEL_ROUTER_*`, `AUDIO_*_TOKENS` and the Anthropic cache metrics, all usable with
`fo.metrics.query(...)` exactly like the token metrics above.

Metric names live on `FoundryMetrics`: `INPUT_TOKENS` (`ProcessedPromptTokens`), `OUTPUT_TOKENS` (`GeneratedTokens`),
`TOTAL_TOKENS` (`TokenTransaction`), `REQUESTS` (`AzureOpenAIRequests`), `LATENCY_TTLT_MS`, and the `MODEL_*` names for
non-OpenAI models. Azure Monitor returns dimension names in lower case.

**CLI**

```bash
foundry-finops tokens contoso-foundry --hours 48                     # tokens and requests per deployment
foundry-finops --format csv tokens contoso-foundry --hours 168 > tokens.csv
foundry-finops estimated-cost contoso-foundry --hours 24                       # estimated USD per project (Preview metric)
foundry-finops estimated-cost contoso-foundry --by project,deployment --hours 72
foundry-finops --format csv estimated-cost contoso-foundry --over-time --interval PT1H --hours 24
foundry-finops ptu contoso-foundry --hours 24                                   # provisioned deployments (section 5.13)
foundry-finops download --only token_usage_by_deployment,token_usage_by_model,metric_definitions,estimated_cost_by_project \
    --start 2026-11-01 --end 2026-11-07
```

### 5.6 Logs (KQL)

Run KQL against a Log Analytics workspace (by its workspace ID, a GUID) or an Application Insights resource (by its
resource ID). This is where client-side tracing and APIM token metrics land.

```python
from foundry_finops import FinOpsClient, queries

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    workspace_id = "<workspace-guid>"
    app_insights_id = "/subscriptions/<subscription-id>/resourceGroups/rg-ai/providers/microsoft.insights/components/finops-ai"

    table = fo.logs.query_workspace(workspace_id, "AppTraces | summarize count() by SeverityLevel", timespan="P7D")
    table = fo.logs.query_resource(app_insights_id, queries.TOKENS_BY_MODEL, timespan="P30D")
    table = fo.logs.query(app_insights_id, queries.TOKENS_BY_TEAM, timespan="2026-01-01T00:00:00Z/2026-02-01T00:00:00Z")
    print(table.to_records()[:5])

    for title, kql in queries.ALL.items():                   # ready-made queries for token use, cost and blocks
        print(title)
```

`timespan` is an ISO 8601 duration (`PT24H`, `P7D`) or a `start/end` interval. `query()` picks the right call: a value that
starts with `/subscriptions/` is treated as a resource ID, anything else as a workspace ID.

**CLI**

```bash
foundry-finops kql <workspace-id-or-app-insights-resource-id> "traces | summarize count() by message" --timespan P7D
foundry-finops --format json kql <workspace-id> "AppTraces | take 10"
foundry-finops download --logs-target <workspace-id> --start 2026-01-01 --end 2026-01-31   # runs the saved KQL files
```

### 5.7 Quota

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    for q in fo.quota.usages("eastus2"):                       # subscription quota per model and SKU, in thousands of TPM
        print(q.display_name, q.current_value, q.limit, q.available, q.percent_used)

    only_openai = list(fo.quota.usages("eastus2", filter="contains(name.value, 'OpenAI')"))
    models = list(fo.quota.models("eastus2"))                  # models and SKUs available in the region
    in_account = list(fo.quota.account_usages("contoso-foundry"))
```

**CLI**

```bash
foundry-finops quota --location eastus2
foundry-finops --format csv quota --location swedencentral
foundry-finops download --only quota_usage,quota_models --location eastus2 --start 2026-01-01 --end 2026-01-31
```

### 5.8 Tags

Tags such as `team`, `cost-center` and `owner` are how you split cost beyond Foundry's own project, deployment and agent
tags. Any scope (subscription, resource group or resource) works.

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    account_id = fo.foundry.account_id("contoso-foundry")

    print(fo.tags.get(account_id))                                          # {'team': 'risk'}
    fo.tags.merge(account_id, {"team": "risk", "cost-center": "CC-4410"})   # add or update, keep the others
    fo.tags.replace(account_id, {"team": "risk"})                           # replace every tag
    fo.tags.remove(account_id, "cost-center")                               # remove by name
    fo.tags.clear(account_id)                                               # remove all

    for t in fo.tags.tag_names():                                           # every tag name in the subscription
        print(t.get("tagName"), t.get("count", {}).get("value"))
    for r in fo.tags.resources_with_tag("team", "risk"):                    # resources with a tag value
        print(r["id"])
    inventory = fo.tags.list_tagged("Microsoft.CognitiveServices/accounts") # resources of one type with their tags
```

Billing tags that Foundry adds (`project`, `deployment`, `agent.name`) are not ARM tags and can't be edited.

**CLI** (read through the download command)

```bash
foundry-finops download --only tags --start 2026-01-01 --end 2026-01-31
```

### 5.9 Alerts

**Metric alert on token use**

```python
from foundry_finops import ActionGroup, FinOpsClient, MetricAlert

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    account_id = fo.foundry.account_id("contoso-foundry")

    group = fo.alerts.create_or_update_action_group(ActionGroup(
        name="finops-oncall", short_name="finops",
        emails={"finops": "finops@contoso.com"},
        webhooks={"teams": "https://contoso.example/hooks/finops"},
    ))

    fo.alerts.create_or_update_metric_alert(MetricAlert(
        name="gpt-hourly-tokens",
        description="More than 5M tokens in an hour on gpt-5.4",
        scopes=[account_id],
        metric_name="TokenTransaction",
        operator="GreaterThan", threshold=5_000_000, time_aggregation="Total",
        window_size="PT1H", evaluation_frequency="PT15M", severity=2,
        dimensions=[{"name": "ModelDeploymentName", "operator": "Include", "values": ["gpt-5.4"]}],
        action_group_ids=[group.id],
    ))
```

**Log alert on a KQL result (for example tokens per team from your own tracing)**

```python
from foundry_finops import FinOpsClient, LogAlert

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    fo.alerts.create_or_update_log_alert(LogAlert(
        name="team-token-spike",
        location="eastus2",                                        # region of the target workspace
        scopes=["/subscriptions/<subscription-id>/resourceGroups/rg-ai/providers/microsoft.insights/components/finops-ai"],
        query='traces | where message == "llm.usage" | summarize tokens = sum(toint(customDimensions["total_tokens"])) by tostring(customDimensions["finops.team"])',
        metric_measure_column="tokens", time_aggregation="Total", operator="GreaterThan", threshold=2_000_000,
        window_size="PT1H", evaluation_frequency="PT15M",
        action_group_ids=["<action-group-resource-id>"],
    ))
```

**List, read and delete**

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    metric_alerts = list(fo.alerts.list_metric_alerts())
    log_alerts = list(fo.alerts.list_log_alerts())
    groups = list(fo.alerts.list_action_groups())
    alert = fo.alerts.get_metric_alert("gpt-hourly-tokens")
    fo.alerts.delete_metric_alert("gpt-hourly-tokens")
    fo.alerts.delete_log_alert("team-token-spike")
    fo.alerts.delete_action_group("finops-oncall")
```

Budget alerts live on [budgets](#52-cost-management-budgets-and-exports), and real-time cost-control alerts go to Event
Grid ([section 7](#7-handle-errors-and-budget-blocks) shows how to read them).

**CLI**

```bash
foundry-finops download --only alerts --start 2026-01-01 --end 2026-01-31
```

### 5.10 API Management (AI gateway)

Put API Management in front of Foundry to give each team its own key, token limit and token metrics.

**Read**

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    gw = "contoso-ai-gateway"

    services = list(fo.apim.list_services())
    apis = list(fo.apim.list_apis(gw))
    products = list(fo.apim.list_products(gw))                       # one per team is a common pattern
    subs = list(fo.apim.list_subscriptions(gw))
    inventory = fo.apim.token_limit_inventory(gw)                    # every token-limit policy across scopes
    xml = fo.apim.get_policy(gw, "global")                           # raw policy XML, or None
    limits = fo.apim.get_token_limits(gw, "product:team-risk")
```

Policy scopes are `"global"`, `"product:<id>"`, `"api:<id>"` and `"operation:<api>/<operation>"`.

**Create a team: product, subscription key and token limit**

```python
from foundry_finops import ApimProduct, ApimSubscription, FinOpsClient, LlmEmitTokenMetric, LlmTokenLimit

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    gw = "contoso-ai-gateway"

    fo.apim.create_or_update_product(
        gw, ApimProduct(name="team-risk", display_name="Risk team", subscriptions_limit=5), api_ids=["foundry-openai"],
    )
    fo.apim.create_or_update_subscription(
        gw, ApimSubscription(name="risk-fraud-app", display_name="Fraud app", scope="/products/team-risk"),
    )
    keys = fo.apim.list_subscription_secrets(gw, "risk-fraud-app")   # treat as secrets: never log or commit them

    # 20,000 tokens a minute (HTTP 429 over it) and 5M tokens a month (HTTP 403 over it)
    fo.apim.set_token_limit(
        gw,
        LlmTokenLimit(counter_key="@(context.Subscription.Id)", tokens_per_minute=20_000,
                      token_quota=5_000_000, token_quota_period="Monthly"),
        scope="product:team-risk",
    )
    # Send token counts to Application Insights with a Team dimension
    fo.apim.set_token_metrics(gw, LlmEmitTokenMetric(namespace="foundry-finops"), scope="global")

    fo.apim.remove_token_limit(gw, "product:team-risk")
    fo.apim.delete_subscription(gw, "risk-fraud-app")
    fo.apim.delete_product(gw, "team-risk")
```

**Edit the policy XML yourself**

```python
from foundry_finops import FinOpsClient, LlmTokenLimit
from foundry_finops.operations.apim import upsert_policy_fragment

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    gw = "contoso-ai-gateway"
    xml, etag = fo.apim.get_policy_with_etag(gw, "api:foundry-openai")                  # keep the ETag
    new_xml = upsert_policy_fragment(xml, LlmTokenLimit(tokens_per_minute=5_000).to_xml())
    fo.apim.set_policy(gw, new_xml, "api:foundry-openai", etag=etag)                    # 412 if someone else changed it
```

**CLI** (read through the download command; add `--apim-service` or `FINOPS_APIM_SERVICE`)

```bash
foundry-finops download --only apim_gateway --apim-service contoso-ai-gateway --start 2026-01-01 --end 2026-01-31
```

### 5.11 Retail prices, effective prices and cost estimates

Three sources of prices, from least to most accurate for you. Billed cost always comes from Cost Management; these feed
*estimates* from token counts (`PriceSheet.estimate`, `UsageTracker`).

**1. Public retail prices** (no sign-in), optionally reduced by a discount **you** supply:

```python
from foundry_finops import FinOpsClient, PriceSheet

with FinOpsClient("<subscription-id>") as fo:
    for item in fo.pricing.retail_prices("serviceName eq 'Foundry Models' and armRegionName eq 'eastus2'"):
        print(item["meterName"], item["unitOfMeasure"], item["retailPrice"])      # raw price items for any OData filter

    retail = fo.pricing.openai_price_sheet("eastus2")                             # best-effort USD per 1M tokens
    discounted = fo.pricing.openai_price_sheet("eastus2", discount_percent=15)    # 15 % off every price
    same = retail.scaled(15)                                                      # the same, on any sheet
    print(discounted.source, discounted.discount_percent)                         # retail 15.0

# Or write your own prices (from your price sheet or agreement)
sheet = PriceSheet.from_mapping({"gpt-5.4-mini": {"input": 0.25, "output": 2.0, "cached_input": 0.025}})
cost = sheet.estimate("gpt-5.4-mini-2026-03-01", input_tokens=1_200_000, output_tokens=300_000, cached_tokens=200_000)
print(cost)                                                                       # None if the model isn't in the sheet
```

`discount_percent` is applied exactly as given (0 up to, not including, 100) and recorded on the sheet. The SDK never
looks a discount up. Successive discounts compound. A dated model name such as `gpt-5.4-mini-2026-03-01` matches the base
entry `gpt-5.4-mini`, but `gpt-4o-mini` never matches `gpt-4o`.

**2. Effective prices from your own billing.** Cost divided by billed quantity per meter, so they include whatever
discounts and agreements apply to you:

```python
from foundry_finops import FinOpsClient
from foundry_finops.operations.pricing import effective_discounts

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    account_id = fo.foundry.account_id("contoso-foundry")

    effective = fo.cost.effective_prices(scope=account_id, days=30)     # one Cost Management query: cost + quantity by meter
    for r in effective.rows:
        print(r.meter, r.unit, r.unit_price, r.price_per_1m_tokens)     # per-1M price only for token-priced meters (1K / 1M units)
    print(effective.skipped)                                            # meters with no billed quantity: no price is invented

    billed = effective.to_price_sheet()                                 # a PriceSheet (source="billed") for estimates
    retail = fo.pricing.openai_price_sheet("eastus2")
    for row in effective_discounts(billed, retail):                     # the discount your bill implies, per model and kind
        print(row["model"], row["kind"], row["billed_per_1m"], row["retail_per_1m"], row["discount_percent"])
```

A meter needs billed usage in the period to get a price. Meters that aren't priced per token get a unit price but no per-1M
figure.

**CLI**

```bash
foundry-finops prices --location eastus2                                   # retail, per 1M tokens (no sign-in needed)
foundry-finops prices --location eastus2 --discount-percent 15             # the same with your 15 % discount applied
foundry-finops --subscription <subscription-id> -g rg-ai prices --source billed --account contoso-foundry --days 30
foundry-finops --subscription <subscription-id> -g rg-ai prices --source billed --compare --account contoso-foundry
foundry-finops download --only retail_price_sheet,effective_prices --discount-percent 15 --start 2026-01-01 --end 2026-01-31
```

`--discount-percent` works with retail prices only. `--compare` prints the discount your billing implies for each model
and price kind.

### 5.12 Your Azure roles

```python
from foundry_finops import FinOpsClient

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    me = fo.authorization.principal_id()                       # object ID of the signed-in identity
    for a in fo.authorization.role_assignments(me):            # direct and group-inherited assignments
        props = a["properties"]
        role = fo.authorization.role_definition(props["roleDefinitionId"])["properties"]["roleName"]
        print(role, props["scope"])
```

To check which download queries those roles allow, see [section 8](#8-download-everything) (`--roles auto`).

### 5.13 Reports computed from API data: anomalies, allocation, PTU

These reports are calculated from data the Azure APIs return. Each states its method, and none of them estimates or fills
in a number.

**Cost anomalies.** Days whose billed cost is a statistical outlier against the same group's earlier days (modified
z-score of the day against the median of the previous 28 days; 3.5 is the usual cut-off). Days with no row count as zero,
because Cost Management returns no row when nothing was charged. This finds outliers in your data; it does not know about a
planned launch, and it is not Azure's own anomaly alerting:

```python
from foundry_finops import FinOpsClient
from foundry_finops.analysis import anomalies_from_table

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    table = fo.cost.cost_by("project", scope=fo.foundry.account_id("contoso-foundry"), days=60)
    for a in anomalies_from_table(table, window=28, min_history=7, threshold=3.5, min_change=5.0, direction="up"):
        print(a.date, a.group, a.cost, "vs baseline", a.baseline, f"({a.percent_change}%)", "score", a.score)
```

**Allocating a shared cost.** Spread a cost you can't tag by project (a gateway, a telemetry workspace) in proportion to a
measure you choose. The weights are your input and the result is an allocation, not billed cost. The parts add up to the
rounded total exactly:

```python
from foundry_finops import CostQuery, FinOpsClient, FoundryMetrics
from foundry_finops.analysis import allocate_cost

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    apim_id = "/subscriptions/<subscription-id>/resourceGroups/rg-ai/providers/Microsoft.ApiManagement/service/contoso-ai-gateway"
    total = sum(r["Cost"] for r in fo.cost.query(CostQuery.last_days(30).total(), scope=apim_id))

    series = fo.metrics.query(fo.foundry.account_id("contoso-foundry"), [FoundryMetrics.TOTAL_TOKENS], hours=30 * 24,
                              interval="P1D", split_by=[FoundryMetrics.DEPLOYMENT_DIMENSION])
    tokens = {s.dimensions["modeldeploymentname"]: s.total for s in series}

    for row in allocate_cost(total, tokens):
        print(row["key"], row["share_percent"], row["allocated"])
```

**PTU headroom.** For each provisioned deployment: capacity in PTUs, service tier and state, utilisation from the
`AzureOpenAIProvisionedManagedUtilizationV2` metric (mean of the interval averages, and the maximum), whether the maximum
reached 100 percent (Microsoft documents that calls are throttled with HTTP 429 from there), and the regional quota row
whose name ends in `.<sku>.<model>`. Anything Azure didn't return is `None`:

```python
from foundry_finops import FinOpsClient
from foundry_finops.analysis import ptu_headroom

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    for r in ptu_headroom(fo, "contoso-foundry", hours=24):
        print(r["deployment"], r["capacity_ptu"], r["utilization_avg_percent"], r["utilization_max_percent"],
              r["at_or_over_100_percent"], r["quota_used"], r["quota_limit"], r["quota_available"])
```

**CLI**

```bash
foundry-finops cost --by project --days 60 --account contoso-foundry > /dev/null     # (the data anomalies work on)
foundry-finops anomalies --by project --days 60 --account contoso-foundry
foundry-finops anomalies --by deployment --days 90 --threshold 4 --min-increase 5 --direction both --account contoso-foundry
foundry-finops allocate contoso-foundry --cost-scope /subscriptions/<subscription-id>/resourceGroups/rg-ai/providers/Microsoft.ApiManagement/service/contoso-ai-gateway --by deployment --days 30
foundry-finops allocate contoso-foundry --total 1250.00 --by model --days 30           # a total you supply
foundry-finops ptu contoso-foundry --hours 48
foundry-finops download --only cost_anomalies,ptu_headroom --start 2026-01-01 --end 2026-03-31
```

---

## 6. Track usage from your own code (tracing)

Foundry's metrics stop at the deployment. To attribute tokens and estimated cost to a team, app, agent or user, record
usage next to each model call. This needs the `tracing` extra.

```python
import os

from foundry_finops import PriceSheet
from foundry_finops.tracing import UsageTracker, configure_tracing, extract_usage, finops_context

# 1. Send traces and metrics to Application Insights (call once at start-up)
configure_tracing(os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"], service_name="fraud-app")
#    or: configure_tracing(project_client=my_ai_project_client)   # reads the connection string from the project

# 2. Record each response with FinOps attributes
tracker = UsageTracker(price_sheet=PriceSheet.from_mapping({"gpt-5.4-mini": {"input": 0.25, "output": 2.0}}))

with finops_context(team="fraud", cost_center="CC-4410"):         # attributes for everything inside the block
    response = openai_client.responses.create(model="gpt-5.4-mini", input="hello")   # your existing call
    record = tracker.record(response, agent="case-review")         # also accepts a chat-completions response or a dict
    print(record.total_tokens, record.cost, record.attributes)

print(extract_usage({"model": "m", "usage": {"prompt_tokens": 10, "completion_tokens": 5}}))   # (model, in, out, cached, reasoning)
```

Prompt and response content recording is off by default (`enable_content_recording=False`). Turn it on only if you accept
storing that text in your telemetry.

The records appear in Application Insights and can be queried with the ready-made KQL
(`foundry_finops.queries.TOKENS_BY_TEAM`, `TOKENS_BY_MODEL`, `TOKENS_BY_AGENT`, `APIM_TOKENS_BY_DIMENSION`,
`BUDGET_BLOCKS`, or all of them in `queries.ALL`):

```python
from foundry_finops import FinOpsClient, queries

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    print(fo.logs.query("<app-insights-resource-id>", queries.TOKENS_BY_TEAM, timespan="P30D").to_records())
```

## 7. Handle errors and budget blocks

All errors derive from `FinOpsError`. HTTP failures carry the status, the service's error code, the response headers and
the request:

```python
from foundry_finops import (
    AuthenticationError, BudgetExceededError, ConflictError, FinOpsClient, FinOpsError, ForbiddenError,
    HttpResponseError, NotFoundError, ThrottledError,
)

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    try:
        fo.cost_controls.get("contoso-foundry", "missing")
    except NotFoundError:
        print("no such cost control")
    except ForbiddenError as e:                 # 403: you lack a role
        print("missing a role:", e.message)
    except AuthenticationError:                 # 401, or no usable credential
        print("sign in again")
    except ConflictError:                       # 409 / 412: someone changed it, or it already exists
        print("changed concurrently")
    except ThrottledError as e:                 # 429 after the retries ran out
        print("retry after", e.retry_after)
    except HttpResponseError as e:              # any other failure
        print(e.status_code, e.code, e.message, e.method, e.url)
    except FinOpsError as e:                    # anything else raised by this package
        print(e)
```

`ValueError` is raised for bad arguments (a missing name, an invalid threshold) before any request is sent.

**Telling a cost-control block from ordinary throttling.** When a Foundry cost control blocks a request, the model call
returns HTTP 429 with the header `x-ms-budget-cause: budget_exceeded`. Retrying will not help until the window resets.
`raise_for_budget` turns the OpenAI client's exception into a `BudgetExceededError`:

```python
import openai

from foundry_finops import BudgetExceededError, raise_for_budget

try:
    try:
        client.responses.create(model="gpt-5.4-mini", input="hi")        # your openai.OpenAI client
    except openai.RateLimitError as e:
        raise_for_budget(e)       # raises BudgetExceededError if a cost control blocked it, else returns
        raise                     # ordinary throttling: handle or retry as usual
except BudgetExceededError as e:
    print("blocked until", e.reset_at, "by cost control", e.cost_control_id)
```

For streaming calls, the block arrives as an error event inside an HTTP 200 response: pass its text to
`is_budget_exceeded(headers, body)`.

**Reading cost-control alerts.** Alerts are delivered through Event Grid as CloudEvents. Foundry sends one per request
while usage is over a threshold, so de-duplicate with `dedupe_key_for`. The event doesn't carry the cost control's name,
so the key uses the source, unit, limit, start date, threshold and window end. Pass `per="agent"` or `per="project"` for
a budget shared per agent or per project, and nothing for a budget everything shares (one notification is enough):

```python
from foundry_finops.models import CostControlAlertEvent

seen: set = set()

def handle(cloud_event: dict) -> None:
    event = CostControlAlertEvent.from_cloud_event(cloud_event)
    key = event.dedupe_key_for("agent")        # None for a shared budget, "project" for a budget per project
    if key in seen:
        return
    seen.add(key)
    print(f"{event.agent_name or event.project_name}: {event.total_consumed} of {event.limit} used ({event.threshold})")
```

---

## 8. Download everything

`foundry-finops download` runs every read query above for a Foundry resource (optionally one project) over a date range
and saves one JSON file per query: `FinOps/<today>/<query-name>.json`, plus `_manifest.json`. The full guide, including
sign-in, scheduling in GitHub Actions and troubleshooting, is in [SETUP.md](SETUP.md).

```bash
# Everything for one project, January to March 2026
foundry-finops --subscription <subscription-id> -g rg-ai download \
    --account contoso-foundry --project fraud-risk --start 2026-01-01 --end 2026-03-31

# The whole Foundry resource, with the KQL and gateway files
foundry-finops download --start 2026-01-01 --end 2026-03-31 \
    --logs-target <workspace-id-or-app-insights-resource-id> --apim-service contoso-ai-gateway

# A subset, written to another folder
foundry-finops download --start 2026-03-01 --end 2026-03-31 --only cost_by_agent,budgets --output-dir ~/reports

# See the query names (these are the file names)
foundry-finops download --list

# Only the queries your roles allow
foundry-finops download --start 2026-01-01 --end 2026-03-31 --roles "Reader,Monitoring Reader"
foundry-finops download --start 2026-01-01 --end 2026-03-31 --roles auto          # read the roles from Azure
foundry-finops download --list --roles auto                                       # dry run
```

The same thing from Python:

```python
from datetime import date
from pathlib import Path

from foundry_finops import FinOpsClient
from foundry_finops.export import QUERIES, ExportContext, run_export
from foundry_finops.roles import discover
from foundry_finops.service import LiveService, Settings

with FinOpsClient("<subscription-id>", resource_group="rg-ai") as fo:
    svc = LiveService(Settings(subscription_id="<subscription-id>", resource_group="rg-ai", foundry_account="contoso-foundry"), fo)
    ctx = ExportContext(client=fo, service=svc, account="contoso-foundry", project="fraud-risk",
                        start=date(2026, 1, 1), end=date(2026, 3, 31))

    print([q.name for q in QUERIES])                                        # every query name
    result = run_export(ctx, Path("."), only=["cost_by_agent", "budgets"])  # writes ./FinOps/<today>/*.json
    for r in result.results:
        print(r.name, r.status, r.rows, r.detail)                           # ok, skipped or failed

    # Limit to what the signed-in identity's roles allow
    scopes = {"subscription": "/subscriptions/<subscription-id>", "resource_group": fo.cost.resource_group_scope(),
              "account": fo.foundry.account_id("contoso-foundry"), "apim": fo.cost.resource_group_scope(),
              "anywhere": "/subscriptions/<subscription-id>"}
    run_export(ctx, Path("."), access=discover(fo, scopes))
```

## 9. Reporting facade and demo mode

`foundry_finops.service` wraps the client in one JSON-friendly interface that the example dashboard and the MCP connector
share. `DemoService` returns illustrative data with no Azure calls, which is handy for trying things out and for tests.

```python
from foundry_finops.service import DemoService, LiveService, Settings, from_env

demo = DemoService()
print(demo.overview()["kpis"])                                    # spend, forecast, tokens, cost controls, quota
print(demo.cost_series("team", days=30)["rows"][:3])              # by project, agent, deployment, model or team
print(demo.token_series("deployment", hours=48)["columns"])
print(demo.list_cost_controls()[0]["state"])
print(demo.kql("CostDaily | summarize Cost=sum(Cost) by Team")["rows"])

live = LiveService(Settings(subscription_id="<subscription-id>", resource_group="rg-ai",
                            foundry_account="contoso-foundry", location="eastus2"))
print(live.overview()["kpis"])                                    # a section that fails is listed under "warnings"

svc = from_env()      # reads FINOPS_* variables: a LiveService, or a DemoService if no subscription is set
```

The CLI has the same demo mode: add `--demo` to `cost`, `tokens`, `cost-controls`, `budgets`, `quota` or `kql`. It never
calls Azure and never mixes with real data (without `--demo` and without a subscription the CLI stops with an error).

```bash
foundry-finops --demo cost --by team
foundry-finops --demo cost-controls
foundry-finops --demo tokens --hours 48
foundry-finops --demo budgets
foundry-finops --demo quota
foundry-finops --demo kql x "CostDaily | summarize Cost=sum(Cost) by Project | order by Cost desc"
```

To run the example dashboard against demo data or your subscription, see
[`examples/dashboard/README.md`](../examples/dashboard/README.md).

---

## 10. Command line reference

```
foundry-finops [--subscription ID] [-g RESOURCE_GROUP] [--format {table,json,csv}] [--demo] <command> [options]
```

The global options come **before** the command.

| Global option | Meaning | Environment variable |
|---|---|---|
| `--subscription ID` | Azure subscription | `FINOPS_SUBSCRIPTION_ID` |
| `-g`, `--resource-group NAME` | Default resource group | `FINOPS_RESOURCE_GROUP` |
| `--format table\|json\|csv` | Output format (default `table`) | n/a |
| `--demo` | Use illustrative data, no Azure calls | n/a |

| Command | Options | Shows |
|---|---|---|
| `cost-controls [ACCOUNT]` | `ACCOUNT` or `FINOPS_FOUNDRY_ACCOUNT` | Cost controls with estimated usage and state |
| `cost` | `--by GROUP` (default `project`; also any tag or dimension), `--days N` (default 30), `--account NAME`, `--scope SCOPE` | Billed cost per day by group |
| `tokens [ACCOUNT]` | `--hours N` (default 24) | Tokens and requests per deployment |
| `budgets` | none | Cost Management budgets |
| `quota` | `--location REGION` (default `eastus2`, or `FINOPS_LOCATION`) | Regional model quota |
| `kql TARGET QUERY` | `--timespan ISO8601` (default `P1D`) | A KQL query against a workspace ID or Application Insights resource ID |
| `estimated-cost [ACCOUNT]` | `--hours N`, `--by project,deployment,model,version,region`, `--over-time`, `--interval` | Near-real-time **estimated** cost from the preview metric |
| `prices` | `--source retail\|billed`, `--location`, `--discount-percent N` (retail), `--account`, `--scope`, `--days`, `--compare` (billed) | Price per 1M tokens: retail (optional discount you supply) or effective from your billing |
| `anomalies` | `--by`, `--days`, `--account`, `--scope`, `--threshold`, `--min-increase`, `--window`, `--min-history`, `--direction` | Days whose billed cost is an outlier |
| `ptu [ACCOUNT]` | `--hours N`, `--location` | Provisioned deployments: capacity, utilisation, quota |
| `allocate [ACCOUNT]` | `--total AMOUNT` or `--cost-scope SCOPE`, `--by deployment\|model`, `--days N` | A shared cost split by token share (an allocation) |
| `query FILE` | `--scope` | Run a Cost Management query saved as JSON |
| `download` | `--account`, `--project`, `--start`, `--end`, `--output-dir`, `--only`, `--roles`, `--list`, `--location`, `--logs-target`, `--apim-service`, `--discount-percent` | Every query, saved as JSON ([section 8](#8-download-everything)) |

Other environment variables: `FINOPS_FOUNDRY_ACCOUNT`, `FINOPS_LOCATION`, `FINOPS_LOGS_TARGET`, `FINOPS_APIM_SERVICE`,
`FINOPS_TEAM_TAG` (the tag that holds your team name, default `team`).

Output formats:

```bash
foundry-finops --demo cost --by project                   # a text table
foundry-finops --demo --format json cost --by project     # JSON records
foundry-finops --demo --format csv cost --by project      # CSV for spreadsheets
```

Exit codes: `0` success, `1` a service or sign-in error (or any failed query in `download`), `2` invalid arguments.

## 11. Cheat sheet: one row per query type

| Query type | SDK call | CLI | `download` file |
|---|---|---|---|
| Cost per day | `fo.cost.query(CostQuery.between(a, b).daily(), scope=...)` | `cost` | `cost_daily_total` |
| Cost by project | `fo.cost.cost_by("project", scope=account_id)` | `cost --by project` | `cost_by_project` |
| Cost by agent | `fo.cost.cost_by("agent.name", scope=account_id)` | `cost --by agent` | `cost_by_agent` |
| Cost by deployment | `fo.cost.cost_by("deployment", scope=account_id)` | `cost --by deployment` | `cost_by_deployment` |
| Cost by team | `fo.cost.cost_by("team", scope=account_id)` | `cost --by team` | `cost_by_team` |
| Cost by model | `fo.cost.cost_by("MeterSubCategory", scope=account_id)` | `cost --by model` | `cost_by_model` |
| Cost by meter | `CostQuery(...).group_by("Meter")` | | `cost_by_meter` |
| Forecast | `fo.cost.forecast(query, scope=...)` | | `cost_forecast` |
| Cost dimensions | `fo.cost.dimensions(scope=...)` | | `cost_dimensions` |
| Cost by pricing model / charge type | `fo.cost.cost_by("PricingModel", scope=...)` | `cost --by PricingModel` | `cost_by_pricing_model`, `cost_by_charge_type` |
| Cost anomalies | `anomalies_from_table(table)` | `anomalies` | `cost_anomalies` |
| Effective prices from billing | `fo.cost.effective_prices(scope=...)` | `prices --source billed` | `effective_prices` |
| Budgets | `fo.cost.list_budgets()` | `budgets` | `budgets` |
| Budget thresholds reached | `budget.notification_status()` | `budgets` | `budget_status` |
| Cost exports | `fo.cost.list_exports()` | | `cost_exports` |
| Cost controls | `fo.cost_controls.list_all(account)` | `cost-controls` | `cost_controls` |
| Foundry account | `fo.foundry.get_account(account)` | | `foundry_account` |
| Projects | `fo.foundry.list_projects(account)` | | `foundry_projects` |
| Deployments | `fo.foundry.list_deployments(account)` | | `foundry_deployments` |
| Connections | `fo.foundry.list_connections(account)` | | `foundry_connections` |
| Event Grid topics | `fo.foundry.list_event_grid_topics()` | | `foundry_event_grid_topics` |
| Tokens by deployment | `fo.metrics.query(account_id, ...)` | `tokens` | `token_usage_by_deployment` |
| Estimated cost (preview metric) | `fo.metrics.estimated_cost_totals(account_id)` | `estimated-cost` | `estimated_cost_by_project`, `estimated_cost_by_deployment` |
| PTU headroom | `ptu_headroom(fo, account)` | `ptu` | `ptu_headroom` |
| Shared cost allocation | `allocate_cost(total, weights)` | `allocate` | |
| Saved cost query | `CostQuery.load(path)` | `query FILE` | |
| Tokens by model | `fo.metrics.query(account_id, ..., split_by=[FoundryMetrics.MODEL_DIMENSION])` | | `token_usage_by_model` |
| Metric definitions | `fo.metrics.definitions(account_id)` | | `metric_definitions` |
| KQL | `fo.logs.query(target, kql)` | `kql` | `kql_*` (five saved queries) |
| Quota usage | `fo.quota.usages(location)` | `quota` | `quota_usage` |
| Quota models | `fo.quota.models(location)` | | `quota_models` |
| Tags | `fo.tags.get(scope)`, `fo.tags.list_tagged(type)` | | `tags` |
| Alerts | `fo.alerts.list_metric_alerts()` and related | | `alerts` |
| API Management | `fo.apim.token_limit_inventory(service)` | | `apim_gateway` |
| Retail prices (+ your discount) | `fo.pricing.openai_price_sheet(region, discount_percent=15)` | `prices --discount-percent 15` | `retail_price_sheet` |
| Your roles | `fo.authorization.role_assignments(principal_id)` | `download --roles auto` | |

If something here doesn't work for you, open an issue with the command or code, the error, and the Azure role you hold
(no IDs or secrets).
