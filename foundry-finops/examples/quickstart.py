"""End-to-end tour of foundry-finops against your subscription (read-only)."""

import os

from azure.identity import DefaultAzureCredential

from foundry_finops import FinOpsClient, FoundryMetrics, cost_controls, resources

sub, rg, acct = (os.environ[k] for k in ("FINOPS_SUBSCRIPTION_ID", "FINOPS_RESOURCE_GROUP", "FINOPS_FOUNDRY_ACCOUNT"))

# FinOpsClient is azure.mgmt.cognitiveservices.CognitiveServicesManagementClient plus the FinOps groups.
with FinOpsClient(DefaultAzureCredential(), sub, resource_group=rg) as fo:
    account_id = fo.account_id(acct)
    print("Projects:", [p.name for p in fo.projects.list(rg, acct)])                        # a Microsoft group
    print("Deployments:", [(d.name, d.tokens_per_minute) for d in resources.list_deployments(fo, rg, acct)])
    for cc in cost_controls.list_specs(fo, rg, acct):
        print(f"Cost control {cc.name}: {cc.amount} {cc.category} per {cc.time_grain}, filters={cc.filters}")
    print(fo.cost.cost_by("project", scope=account_id, days=30, daily=False).to_csv())
    tokens = fo.metrics.query(account_id, FoundryMetrics.TOKENS, hours=24)
    print("Tokens (24h):", sum(s.total for s in tokens if s.metric == FoundryMetrics.TOTAL_TOKENS))
    for q in resources.list_quota(fo, os.environ.get("FINOPS_LOCATION", "eastus2")):
        if q.percent_used > 50:
            print(f"Quota {q.display_name}: {q.percent_used}% used")
