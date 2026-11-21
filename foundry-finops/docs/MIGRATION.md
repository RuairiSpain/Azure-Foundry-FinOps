# Migrating to the Microsoft SDKs

This package is built so that moving to the Microsoft packages is a change of imports and one constructor, not a
rewrite. This guide covers three things: what changed from version 0.1, how to move the Foundry parts to the plain
Microsoft client today, and where each remaining part goes. The reasoning is in [DESIGN.md](DESIGN.md).

## 1. What `FinOpsClient` is

`FinOpsClient` subclasses `azure.mgmt.cognitiveservices.CognitiveServicesManagementClient`, which has had the Foundry
cost controls since **15.1.0b1** (16 November 2026, preview API `2026-11-15-preview`). Everything that client offers is
on `FinOpsClient` unchanged: `account_cost_controls`, `project_cost_controls`, `accounts`, `projects`, `deployments`,
`account_connections`, `usages`, `models` and the rest. This package adds groups for what Microsoft doesn't cover
(`cost`, `metrics`, `logs`, `tags`, `alerts`, `apim`, `pricing`, `policy`, `event_grid`, `authorization`) and two
modules of helpers, `foundry_finops.cost_controls` and `foundry_finops.resources`.

The rule that makes the move cheap: **the helpers take the client as their first argument and call only Microsoft
groups**, so they keep working on the plain Microsoft client.

## 2. From 0.1 to 0.2

Version 0.2 is a breaking release. The old groups `fo.foundry`, `fo.cost_controls` and `fo.quota` are gone, replaced by
the Microsoft groups and the helpers.

| 0.1 | 0.2 |
|---|---|
| `FinOpsClient(sub, credential, resource_group=...)` | `FinOpsClient(credential, sub, resource_group=...)` (the Microsoft order); `FinOpsClient.from_default_credential(sub)` |
| `cloud=US_GOV_CLOUD` | `cloud_setting=AzureClouds.AZURE_US_GOVERNMENT` (`from azure.core import AzureClouds`) |
| `fo.cost_controls.list(acct)` | `fo.account_cost_controls.list(rg, acct)` |
| `fo.cost_controls.list(acct, project=p)` | `fo.project_cost_controls.list(rg, acct, p)` |
| `fo.cost_controls.list_all(acct)` | `cost_controls.list_all(fo, rg, acct)` (Microsoft models) or `cost_controls.list_specs(fo, rg, acct)` (flat specs) |
| `fo.cost_controls.get(acct, name, project=p)` | `fo.project_cost_controls.get(rg, acct, p, name)` |
| `fo.cost_controls.create_or_update(acct, spec, project=p, only_if_new=True)` | `cost_controls.upsert(fo, rg, acct, spec, project=p, only_if_new=True)`, or `fo.project_cost_controls.create_or_update(rg, acct, p, spec.name, spec.to_resource(project=p), match_condition=MatchConditions.IfMissing)` |
| `...create_or_update(..., if_match=etag)` | `upsert(..., etag=etag)`, or `etag=etag, match_condition=MatchConditions.IfNotModified` on the Microsoft call |
| `fo.cost_controls.update(acct, name, amount=750)` | `fo.account_cost_controls.update(rg, acct, name, CostControlPatch(properties=CostControlPatchProperties(amount=750)))` |
| `fo.cost_controls.delete(acct, name, project=p)` | `fo.project_cost_controls.delete(rg, acct, p, name)` |
| `fo.cost_controls.exists / remove_alert / remove_block / applicable / project_budget / delete_project_cost_controls / get_usage_connection / set_usage_connection / find_event_grid_connection_id` | the same names in `foundry_finops.cost_controls`, with `(fo, rg, acct, ...)` (`project_budget` is `get_project_budget`) |
| `fo.foundry.account_id(acct)` | `fo.account_id(acct)` |
| `fo.foundry.list_accounts()` / `get_account(acct)` | `fo.accounts.list_by_resource_group(rg)` / `fo.accounts.get(rg, acct)` |
| `fo.foundry.list_projects(acct)` / `get_project(...)` | `fo.projects.list(rg, acct)` / `fo.projects.get(rg, acct, project)` |
| `fo.foundry.list_deployments(acct)` / `get_deployment(...)` | `fo.deployments.list(rg, acct)` / `fo.deployments.get(rg, acct, name)`; `resources.list_deployments(fo, rg, acct)` for the views |
| `fo.foundry.set_capacity(acct, name, 50)` | `resources.set_deployment_capacity(fo, rg, acct, name, 50)` |
| `fo.foundry.create_or_update_deployment / delete_deployment` | `fo.deployments.begin_create_or_update / begin_delete` |
| `fo.foundry.list_connections(acct)` | `fo.account_connections.list(rg, acct)` |
| `fo.foundry.list_event_grid_topics()` | `fo.event_grid.list_topics()` |
| `fo.quota.usages(location)` | `fo.usages.list(location)` (`resources.list_quota(fo, location)` adds `percent_used`) |
| `fo.quota.models(location)` | `fo.models.list(location)` |
| `Deployment`, `FoundryAccount`, `FoundryProject`, `QuotaUsage` | `DeploymentView`, `AccountView`, `ProjectView`, `QuotaView` (read views; the Microsoft models are `azure.mgmt.cognitiveservices.models.*`) |
| a cost control without dates | both `start_date` and `end_date` are required (the service requires them) |
| `NotFoundError` and the other errors | the same classes, now also `azure.core.exceptions` errors |

## 3. From this package to the plain Microsoft client

Start with a client that already is the Microsoft client. Nothing below needs a rewrite of the calls.

**Step 1. Constructor and import.** Change only these lines:

```python
# before
from foundry_finops import FinOpsClient
client = FinOpsClient(credential, "<subscription-id>", resource_group="rg-ai")

# after
from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient
client = CognitiveServicesManagementClient(credential, "<subscription-id>")
```

`resource_group` is not an argument of the Microsoft client; its groups always take it first, and so do the groups you
already call on `FinOpsClient`.

**Step 2. Calls on the Microsoft groups need no change.** `client.account_cost_controls`, `client.project_cost_controls`,
`client.accounts`, `client.projects`, `client.deployments`, `client.account_connections`, `client.usages` and
`client.models` are the same objects with the same signatures. `tests/test_official_parity.py` checks the signatures and
the request each one sends.

**Step 3. Keep the helpers.** `foundry_finops.cost_controls` and `foundry_finops.resources` run on the plain client:

```python
from foundry_finops import cost_controls

specs = list(cost_controls.list_specs(client, "rg-ai", "contoso-foundry"))
cost_controls.remove_alert(client, "rg-ai", "contoso-foundry", "fraud-risk-monthly", project="fraud-risk")
```

`CostControl` is an authoring spec. `spec.validate()` checks offline what the service would refuse, and
`spec.to_resource()` gives the Microsoft model. To go fully native, build the models yourself:

```python
from datetime import datetime, timezone
from azure.mgmt.cognitiveservices import models as m

resource = m.ProjectCostControl(properties=m.CostControlProperties(
    category="Cost", amount=500, time_grain="Monthly",
    time_period=m.CostControlTimePeriod(start_date=datetime(2026, 12, 1, tzinfo=timezone.utc),
                                        end_date=datetime(2027, 7, 1, tzinfo=timezone.utc)),
    filter=m.CostControlFilter(additional_info=m.CostControlAdditionalInfoFilter(
        name="project", values_property=["fraud-risk"])),
    grouping=[m.CostControlGrouping(name="agent")],
    notifications={"Actual_GreaterThanOrEqualTo_80_Percent": m.CostControlNotification(
        event_grid_connection_id="<connection-id>", threshold_type="Percentage", threshold=80, enabled=True)},
    provider_properties=m.CostControlProviderProperties(actions={
        "Block_GreaterThanOrEqualTo_100_Percent": m.CostControlAction(threshold_type="Percentage", threshold=100)}),
))
client.project_cost_controls.create_or_update("rg-ai", "contoso-foundry", "fraud-risk", "fraud-risk-monthly", resource)
```

**Step 4. The FinOps groups.** `client.cost`, `client.metrics` and the others don't exist on the Microsoft client. Use the
table in section 4, or keep `FinOpsClient` for them: it is the Microsoft client, so you can use both on one object.

### Things the Microsoft models do differently (15.1.0b1)

* Field names follow Python: `time_grain`, `event_grid_connection_id`, `provider_properties`. Two are renamed because
  they are Python keywords or reserved: the filter's `values` is **`values_property`**, and `and` is **`and_property`**.
  `as_dict()` returns the REST names (`timeGrain`, `values`).
* Dates are `datetime` objects, not strings.
* `etag` plus `match_condition=MatchConditions.IfNotModified` is `If-Match`; `MatchConditions.IfMissing` is
  `If-None-Match: *`. The spec's PATCH treats a missing `properties` as "keep everything".
* A model field set to `None` is left out of the request, but a `None` **value inside a dict** is sent as `null`. That
  is how a PATCH deletes a named alert or block (`notifications={"<name>": None}`); `cost_controls.remove_alert` and
  `remove_block` do this. To clear the account's Application Insights connection (`costControlConnections: null`) the body
  is sent as JSON bytes, which `update`/`begin_update` accept (`IO[bytes]`).
* `check_existence` raises `ResourceNotFoundError` for a missing cost control; `cost_controls.exists` returns `False`.
* Lists return `ItemPaged`; `begin_*` methods return `LROPoller`.
* Errors are plain `azure.core.exceptions` errors. This package's own errors are also azure-core errors, so
  `except azure.core.exceptions.HttpResponseError` works before and after.
* `FeatureNotRegistered` is not explained by the Microsoft client; `cost_controls.upsert` adds the registration command.
* The Microsoft client uses one `api_version` for all its groups (default `2026-11-15-preview`).

## 4. Where every call goes

The table is generated from `foundry_finops.migration.MAP`; run `foundry-finops migration-map` for the current one.
`tests/test_migration_map.py` resolves each Microsoft call against the installed packages
(`pip install "foundry-finops[conformance]"`).

| This package | Microsoft package | Microsoft client call |
|---|---|---|
| `client.account_cost_controls.*` | `azure-mgmt-cognitiveservices` | `account_cost_controls.create_or_update` inherited: same client |
| `client.project_cost_controls.*` | `azure-mgmt-cognitiveservices` | `project_cost_controls.create_or_update` inherited: same client |
| `client.accounts / projects / deployments / usages / models` | `azure-mgmt-cognitiveservices` | `accounts.get` inherited: same client |
| `client.cost.query` | `azure-mgmt-costmanagement` | `query.usage` the request body is the same JSON; this package builds it with CostQuery |
| `client.cost.forecast` | `azure-mgmt-costmanagement` | `forecast.usage` |
| `client.cost.dimensions` | `azure-mgmt-costmanagement` | `dimensions.list` |
| `client.cost.list_exports` | `azure-mgmt-costmanagement` | `exports.list` |
| `client.cost.get_export` | `azure-mgmt-costmanagement` | `exports.get` |
| `client.cost.create_or_update_export` | `azure-mgmt-costmanagement` | `exports.create_or_update` |
| `client.cost.delete_export` | `azure-mgmt-costmanagement` | `exports.delete` |
| `client.cost.run_export` | `azure-mgmt-costmanagement` | `exports.execute` |
| `client.cost.export_history` | `azure-mgmt-costmanagement` | `exports.get_execution_history` |
| `client.cost.list_budgets` | `azure-mgmt-consumption` | `budgets.list` Azure Cost Management budgets live in azure-mgmt-consumption |
| `client.cost.get_budget` | `azure-mgmt-consumption` | `budgets.get` |
| `client.cost.create_or_update_budget` | `azure-mgmt-consumption` | `budgets.create_or_update` |
| `client.cost.delete_budget` | `azure-mgmt-consumption` | `budgets.delete` |
| `client.cost.cost_by / effective_prices / set_tag_inheritance` | - | none convenience over the query API; no Microsoft equivalent |
| `client.metrics.query` | `azure-mgmt-monitor` | `metrics.list` metrics for one resource |
| `client.metrics.definitions` | `azure-mgmt-monitor` | `metric_definitions.list` |
| `client.metrics.estimated_cost / estimated_cost_totals / query_table` | - | none Foundry-specific views over the metrics API; no Microsoft equivalent |
| `client.logs.query_workspace` | `azure-monitor-query` | `query_workspace` azure-monitor-query |
| `client.logs.query_resource` | `azure-monitor-query` | `query_resource` |
| `client.alerts.list_metric_alerts` | `azure-mgmt-monitor` | `metric_alerts.list_by_resource_group` |
| `client.alerts.get_metric_alert` | `azure-mgmt-monitor` | `metric_alerts.get` |
| `client.alerts.create_or_update_metric_alert` | `azure-mgmt-monitor` | `metric_alerts.create_or_update` |
| `client.alerts.delete_metric_alert` | `azure-mgmt-monitor` | `metric_alerts.delete` |
| `client.alerts.list_log_alerts` | `azure-mgmt-monitor` | `scheduled_query_rules.list_by_resource_group` |
| `client.alerts.get_log_alert` | `azure-mgmt-monitor` | `scheduled_query_rules.get` |
| `client.alerts.create_or_update_log_alert` | `azure-mgmt-monitor` | `scheduled_query_rules.create_or_update` |
| `client.alerts.delete_log_alert` | `azure-mgmt-monitor` | `scheduled_query_rules.delete` |
| `client.alerts.list_action_groups` | `azure-mgmt-monitor` | `action_groups.list_by_resource_group` |
| `client.alerts.create_or_update_action_group` | `azure-mgmt-monitor` | `action_groups.create_or_update` |
| `client.alerts.delete_action_group` | `azure-mgmt-monitor` | `action_groups.delete` |
| `client.tags.get` | `azure-mgmt-resource` | `tags.get_at_scope` |
| `client.tags.merge` | `azure-mgmt-resource` | `tags.begin_update_at_scope` operation Merge |
| `client.tags.replace` | `azure-mgmt-resource` | `tags.begin_create_or_update_at_scope` |
| `client.tags.clear` | `azure-mgmt-resource` | `tags.begin_delete_at_scope` |
| `client.tags.tag_names` | `azure-mgmt-resource` | `tags.list` |
| `client.authorization.role_assignments` | `azure-mgmt-authorization` | `role_assignments.list_for_subscription` |
| `client.authorization.role_definition` | `azure-mgmt-authorization` | `role_definitions.get_by_id` |
| `client.policy.states` | `azure-mgmt-policyinsights` | `policy_states.list_query_results_for_resource` |
| `client.event_grid.list_topics` | `azure-mgmt-eventgrid` | `topics.list_by_subscription` |
| `client.apim.list_services` | `azure-mgmt-apimanagement` | `api_management_service.list_by_resource_group` |
| `client.apim.get_product` | `azure-mgmt-apimanagement` | `product.get` |
| `client.apim.list_products` | `azure-mgmt-apimanagement` | `product.list_by_service` |
| `client.apim.create_or_update_product` | `azure-mgmt-apimanagement` | `product.create_or_update` |
| `client.apim.delete_product` | `azure-mgmt-apimanagement` | `product.delete` |
| `client.apim.get_subscription` | `azure-mgmt-apimanagement` | `subscription.get` |
| `client.apim.list_subscriptions` | `azure-mgmt-apimanagement` | `subscription.list` |
| `client.apim.create_or_update_subscription` | `azure-mgmt-apimanagement` | `subscription.create_or_update` |
| `client.apim.delete_subscription` | `azure-mgmt-apimanagement` | `subscription.delete` |
| `client.apim.get_policy` | `azure-mgmt-apimanagement` | `policy.get` |
| `client.apim.set_policy` | `azure-mgmt-apimanagement` | `policy.create_or_update` |
| `client.apim.delete_policy` | `azure-mgmt-apimanagement` | `policy.delete` |
| `client.apim.set_token_limit / set_token_metrics / remove_token_limit / get_token_limits` | - | none edit the llm-token-limit and llm-emit-token-metric policy XML; no Microsoft equivalent |
| `client.pricing.retail_prices / openai_price_sheet` | - | none the public Retail Prices API has no SDK |
| `foundry_finops.cost_controls.*` | - | none helpers over the official groups; they keep working with the Microsoft client |

## 5. When the Microsoft package reaches general availability

`azure-mgmt-cognitiveservices` is a pre-release for cost controls. Until it is GA, this package pins
`>=15.1.0b1,<16`, and CI runs weekly against the newest release. When it ships:

1. Bump the lower bound; the parity tests show any renamed model or changed signature.
2. Nothing in your code changes if you use the Microsoft groups and the helpers.
3. If you call the Microsoft client directly, drop the `foundry-finops` constructor; the helpers still apply.
