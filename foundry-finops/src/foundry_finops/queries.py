"""Ready-made KQL for Application Insights / Log Analytics.

Tables follow the Application Insights (classic) names; for workspace-based queries replace
``traces`` → ``AppTraces``, ``dependencies`` → ``AppDependencies`` and ``customDimensions`` →
``Properties``.
"""

#: Token usage per model from Foundry GenAI tracing spans (gen_ai semantic conventions).
TOKENS_BY_MODEL = """
dependencies
| where isnotempty(customDimensions["gen_ai.usage.input_tokens"])
| extend model = tostring(customDimensions["gen_ai.request.model"]),
         input_tokens = toint(customDimensions["gen_ai.usage.input_tokens"]),
         output_tokens = toint(customDimensions["gen_ai.usage.output_tokens"])
| summarize input_tokens = sum(input_tokens), output_tokens = sum(output_tokens), calls = count()
          by model, bin(timestamp, 1h)
| order by timestamp asc
"""

#: Tokens and estimated cost per agent (Foundry sets gen_ai.agent.name on agent spans).
TOKENS_BY_AGENT = """
dependencies
| where isnotempty(customDimensions["gen_ai.usage.input_tokens"])
| extend agent = tostring(customDimensions["gen_ai.agent.name"]),
         tokens = toint(customDimensions["gen_ai.usage.input_tokens"]) + toint(customDimensions["gen_ai.usage.output_tokens"])
| summarize tokens = sum(tokens), calls = count() by agent, bin(timestamp, 1d)
"""

#: Records written by foundry_finops.tracing.UsageTracker.
TOKENS_BY_TEAM = """
traces
| where message == "llm.usage"
| extend team = tostring(customDimensions["finops.team"]),
         model = tostring(customDimensions["model"]),
         tokens = toint(customDimensions["total_tokens"]),
         cost = todouble(customDimensions["cost"])
| summarize tokens = sum(tokens), cost = sum(cost), calls = count() by team, model, bin(timestamp, 1d)
"""

#: Token metrics emitted by APIM <llm-emit-token-metric> (customMetrics).
APIM_TOKENS_BY_DIMENSION = """
customMetrics
| where name in ("Prompt Tokens", "Completion Tokens", "Total Tokens")
| extend team = tostring(customDimensions["Team"]), subscription = tostring(customDimensions["Subscription ID"])
| summarize tokens = sum(valueSum) by name, team, subscription, bin(timestamp, 1h)
"""

#: Requests blocked by Foundry cost controls, from client-side dependency telemetry.
BUDGET_BLOCKS = """
dependencies
| where resultCode == "429"
| where customDimensions has "budget_exceeded" or tostring(customDimensions["error.type"]) has "Budget"
| summarize blocked = count() by target, bin(timestamp, 1h)
"""

ALL = {
    "Tokens by model (tracing)": TOKENS_BY_MODEL,
    "Tokens by agent (tracing)": TOKENS_BY_AGENT,
    "Tokens and cost by team (UsageTracker)": TOKENS_BY_TEAM,
    "APIM token metrics by team": APIM_TOKENS_BY_DIMENSION,
    "Cost-control blocks": BUDGET_BLOCKS,
}


# --------------------------------------------------------------- usage by caller
#: Sources of per-caller usage. ``client``: ``llm.usage`` records written by ``foundry_finops.tracing.UsageTracker``.
#: ``gateway``: the per-call trace records of ``examples/apim/llm-caller-attribution.policy.xml`` joined to the gateway's
#: token log by request ID.
USAGE_SOURCES = ("client", "gateway")

_USAGE_CLIENT_CLASSIC = """
traces
| where message == "llm.usage"
| extend oid = tostring(customDimensions["finops.caller_oid"]),
         kind = tostring(customDimensions["finops.caller_type"]),
         cost = todouble(customDimensions["cost"]),
         tokens = tolong(customDimensions["total_tokens"])
| summarize cost = sum(cost), tokens = sum(tokens), calls = count() by oid, kind
"""

_USAGE_CLIENT_WORKSPACE = """
AppTraces
| where Message == "llm.usage"
| extend oid = tostring(Properties["finops.caller_oid"]),
         kind = tostring(Properties["finops.caller_type"]),
         cost = todouble(Properties["cost"]),
         tokens = tolong(Properties["total_tokens"])
| summarize cost = sum(cost), tokens = sum(tokens), calls = count() by oid, kind
"""

#: UNTESTED against a live gateway: the table and column names are what Microsoft documents for the gateway's LLM log and
#: Application Insights traces in a workspace, and must be checked on your instance. Tokens only: the gateway log has no
#: price, so ``cost`` is null.
_USAGE_GATEWAY_WORKSPACE = """
let who = AppTraces
    | where Message has "caller_oid"
    | extend rec = parse_json(Message)
    | where isnotempty(tostring(rec.request_id))
    | project request_id = tostring(rec.request_id), oid = tostring(rec.caller_oid), kind = tostring(rec.caller_type);
ApiManagementGatewayLlmLog
| extend request_id = tostring(CorrelationId)
| join kind=leftouter who on request_id
| extend oid = coalesce(oid, ""), kind = coalesce(kind, "unknown")
| summarize tokens = sum(tolong(TotalTokens)), calls = count() by oid, kind
| extend cost = real(null)
"""


def is_app_insights_target(target: str) -> bool:
    """True for an Application Insights resource ID (classic table names), false for a workspace ID or workspace resource ID."""
    return "/microsoft.insights/components/" in target.lower()


def usage_by_caller(source: str, target: str) -> str:
    """KQL that returns one row per caller: ``oid, kind, cost, tokens, calls``. The time range goes in the request's timespan.

    ``target`` is the ``logs_target`` the query will run against: an Application Insights resource ID uses the classic
    table names, a Log Analytics workspace uses ``AppTraces``. The gateway source needs a workspace that holds both the
    Application Insights traces and the gateway's LLM log.
    """
    if source not in USAGE_SOURCES:
        raise ValueError(f"usage source must be one of {USAGE_SOURCES} (got {source!r})")
    classic = is_app_insights_target(target)
    if source == "client":
        return (_USAGE_CLIENT_CLASSIC if classic else _USAGE_CLIENT_WORKSPACE).strip()
    if classic:
        raise ValueError("The gateway source joins the gateway's token log to the trace records, so it needs a Log Analytics "
                         "workspace (a workspace ID or workspace resource ID), not an Application Insights resource ID")
    return _USAGE_GATEWAY_WORKSPACE.strip()
