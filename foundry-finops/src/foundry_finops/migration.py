"""Where each part of this package goes when you move to the Microsoft SDKs.

``MAP`` lists, for every operation of this package, the Microsoft client and method that does the same thing (or
``None`` where Microsoft has no SDK for it). ``tests/test_migration_map.py`` resolves each entry against the installed
Microsoft packages, so a name that is wrong, or one a new release renames, fails a test instead of a migration.

Print the table with ``foundry-finops migration-map``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Equivalent:
    ours: str  #: ``client.<group>.<method>`` in this package
    package: str | None  #: the Microsoft package (``pip install ...``); ``None`` when there is no Microsoft SDK
    client: str | None  #: dotted path of the Microsoft client class
    official: str | None  #: ``<group>.<method>`` on that client, or a method of the client itself
    note: str = ""

    @property
    def resolved(self) -> bool:
        return self.client is not None


_CM = ("azure-mgmt-costmanagement", "azure.mgmt.costmanagement.CostManagementClient")
_CO = ("azure-mgmt-consumption", "azure.mgmt.consumption.ConsumptionManagementClient")
_MON = ("azure-mgmt-monitor", "azure.mgmt.monitor.MonitorManagementClient")
_RES = ("azure-mgmt-resource", "azure.mgmt.resource.resources.ResourceManagementClient")
_APIM = ("azure-mgmt-apimanagement", "azure.mgmt.apimanagement.ApiManagementClient")
_AUTH = ("azure-mgmt-authorization", "azure.mgmt.authorization.AuthorizationManagementClient")
_POL = ("azure-mgmt-policyinsights", "azure.mgmt.policyinsights.PolicyInsightsClient")
_EG = ("azure-mgmt-eventgrid", "azure.mgmt.eventgrid.EventGridManagementClient")
_LOGS = ("azure-monitor-query", "azure.monitor.query.LogsQueryClient")
_CS = ("azure-mgmt-cognitiveservices", "azure.mgmt.cognitiveservices.CognitiveServicesManagementClient")


def _e(ours: str, pkg: tuple[str, str] | None, official: str | None, note: str = "") -> Equivalent:
    return Equivalent(ours, pkg[0] if pkg else None, pkg[1] if pkg else None, official, note)


MAP: tuple[Equivalent, ...] = (
    # --- already the Microsoft client: nothing to move, the name is the same
    _e("client.account_cost_controls.*", _CS, "account_cost_controls.create_or_update", "inherited: same client"),
    _e("client.project_cost_controls.*", _CS, "project_cost_controls.create_or_update", "inherited: same client"),
    _e("client.accounts / projects / deployments / usages / models", _CS, "accounts.get", "inherited: same client"),
    # --- Cost Management
    _e("client.cost.query", _CM, "query.usage", "the request body is the same JSON; this package builds it with CostQuery"),
    _e("client.cost.forecast", _CM, "forecast.usage"),
    _e("client.cost.dimensions", _CM, "dimensions.list"),
    _e("client.cost.list_exports", _CM, "exports.list"),
    _e("client.cost.get_export", _CM, "exports.get"),
    _e("client.cost.create_or_update_export", _CM, "exports.create_or_update"),
    _e("client.cost.delete_export", _CM, "exports.delete"),
    _e("client.cost.run_export", _CM, "exports.execute"),
    _e("client.cost.export_history", _CM, "exports.get_execution_history"),
    _e("client.cost.list_budgets", _CO, "budgets.list", "Azure Cost Management budgets live in azure-mgmt-consumption"),
    _e("client.cost.get_budget", _CO, "budgets.get"),
    _e("client.cost.create_or_update_budget", _CO, "budgets.create_or_update"),
    _e("client.cost.delete_budget", _CO, "budgets.delete"),
    _e("client.cost.cost_by / effective_prices / set_tag_inheritance", None, None,
       "convenience over the query API; no Microsoft equivalent"),
    # --- Azure Monitor
    _e("client.metrics.query", _MON, "metrics.list", "metrics for one resource"),
    _e("client.metrics.definitions", _MON, "metric_definitions.list"),
    _e("client.metrics.estimated_cost / estimated_cost_totals / query_table", None, None,
       "Foundry-specific views over the metrics API; no Microsoft equivalent"),
    _e("client.logs.query_workspace", _LOGS, "query_workspace", "azure-monitor-query"),
    _e("client.logs.query_resource", _LOGS, "query_resource"),
    _e("client.alerts.list_metric_alerts", _MON, "metric_alerts.list_by_resource_group"),
    _e("client.alerts.get_metric_alert", _MON, "metric_alerts.get"),
    _e("client.alerts.create_or_update_metric_alert", _MON, "metric_alerts.create_or_update"),
    _e("client.alerts.delete_metric_alert", _MON, "metric_alerts.delete"),
    _e("client.alerts.list_log_alerts", _MON, "scheduled_query_rules.list_by_resource_group"),
    _e("client.alerts.get_log_alert", _MON, "scheduled_query_rules.get"),
    _e("client.alerts.create_or_update_log_alert", _MON, "scheduled_query_rules.create_or_update"),
    _e("client.alerts.delete_log_alert", _MON, "scheduled_query_rules.delete"),
    _e("client.alerts.list_action_groups", _MON, "action_groups.list_by_resource_group"),
    _e("client.alerts.create_or_update_action_group", _MON, "action_groups.create_or_update"),
    _e("client.alerts.delete_action_group", _MON, "action_groups.delete"),
    # --- Resource Manager, authorization, policy, Event Grid
    _e("client.tags.get", _RES, "tags.get_at_scope"),
    _e("client.tags.merge", _RES, "tags.begin_update_at_scope", "operation Merge"),
    _e("client.tags.replace", _RES, "tags.begin_create_or_update_at_scope"),
    _e("client.tags.clear", _RES, "tags.begin_delete_at_scope"),
    _e("client.tags.tag_names", _RES, "tags.list"),
    _e("client.authorization.role_assignments", _AUTH, "role_assignments.list_for_subscription"),
    _e("client.authorization.role_definition", _AUTH, "role_definitions.get_by_id"),
    _e("client.policy.states", _POL, "policy_states.list_query_results_for_resource"),
    _e("client.event_grid.list_topics", _EG, "topics.list_by_subscription"),
    # --- API Management
    _e("client.apim.list_services", _APIM, "api_management_service.list_by_resource_group"),
    _e("client.apim.get_product", _APIM, "product.get"),
    _e("client.apim.list_products", _APIM, "product.list_by_service"),
    _e("client.apim.create_or_update_product", _APIM, "product.create_or_update"),
    _e("client.apim.delete_product", _APIM, "product.delete"),
    _e("client.apim.get_subscription", _APIM, "subscription.get"),
    _e("client.apim.list_subscriptions", _APIM, "subscription.list"),
    _e("client.apim.create_or_update_subscription", _APIM, "subscription.create_or_update"),
    _e("client.apim.delete_subscription", _APIM, "subscription.delete"),
    _e("client.apim.get_policy", _APIM, "policy.get"),
    _e("client.apim.set_policy", _APIM, "policy.create_or_update"),
    _e("client.apim.delete_policy", _APIM, "policy.delete"),
    _e("client.apim.set_token_limit / set_token_metrics / remove_token_limit / get_token_limits", None, None,
       "edit the llm-token-limit and llm-emit-token-metric policy XML; no Microsoft equivalent"),
    # --- no Microsoft SDK
    _e("client.pricing.retail_prices / openai_price_sheet", None, None, "the public Retail Prices API has no SDK"),
    # --- this package's cost control helpers
    _e("foundry_finops.cost_controls.*", None, None,
       "helpers over the official groups; they keep working with the Microsoft client"),
)


def table() -> str:
    """The map as a Markdown table."""
    rows = ["| This package | Microsoft package | Microsoft client call |", "|---|---|---|"]
    for e in MAP:
        target = f"`{e.official}`" if e.official else "none"
        pkg = f"`{e.package}`" if e.package else "-"
        note = f" {e.note}" if e.note else ""
        rows.append(f"| `{e.ours}` | {pkg} | {target}{note} |")
    return "\n".join(rows)
