# Azure cost and usage APIs: what the SDK covers and what it doesn't

A review of the Azure REST APIs for cost and usage (including the preview ones) against `foundry-finops`, with a
prioritised list of features to add.

* **Source.** The official OpenAPI definitions in [Azure/azure-rest-api-specs](https://github.com/Azure/azure-rest-api-specs)
  (the `main` branch, read on 2026-11-21) and the Azure Monitor supported-metrics page for
  `Microsoft.CognitiveServices/accounts`. Nothing here was tested against a live Azure service.
* **Not reviewed in depth:** Microsoft.Billing (invoices, billing periods), Azure Advisor cost recommendations,
  Azure Monitor diagnostic settings and the metrics batch API.

## 1. API versions: what the SDK uses and what is current

| API | SDK default (`ApiVersions`) | Newest stable | Newest preview | Note |
|---|---|---|---|---|
| Cost Management | `2025-03-01` | `2026-06-01` | `2024-10-01-preview` | 2026-06-01 only adds markup rules (MCA partners). Safe to move. |
| Cost Management tag inheritance (`settings`) | `2022-10-05-preview` | `2026-06-01` | | Settings is stable now; stop using the preview version. |
| Foundry cost controls | `2026-11-15-preview` | none | `2026-11-15-preview` | Current. Only exists as a preview. |
| Cognitive Services (accounts, deployments, quota) | `2025-06-01` | `2026-11-01` | `2026-11-15-preview` | New deployment fields since (see 3.2). |
| API Management | `2024-05-01` | `2024-05-01` | `2025-09-01-preview` | Current stable. |
| Azure Monitor metrics, alerts, tags, resources | unchanged | not reviewed | | |

## 2. Cost Management (`Microsoft.CostManagement`, 2026-06-01)

Of its 75 operations in 21 groups, the SDK covers 6 groups fully or partly.

| Group | Operations | SDK today | Gap |
|---|---|---|---|
| Query | 2 (+1 external cloud) | `fo.cost.query`, `cost_by` | Only `and` filters (the API also takes `or`/`not`); no check for the 2 group-by limit; no constructors for the `WeekToDate`, `TheLastMonth`, `BillingMonthToDate`, `TheLastBillingMonth`, `TheCurrentMonth` time frames; no `configuration.columns`; no multi-cloud queries |
| Forecast | 2 | `fo.cost.forecast` | none |
| Dimensions | 2 | `fo.cost.dimensions` | No `$filter`, `$expand`, `$top` |
| Budgets | 4 | list, get, create or update, delete | Budget-only periods were mishandled (fixed, see 5) |
| Exports | 6 | list, create or update, run, history, delete | No **get**; no Parquet compression, `partitionData`, `dataOverwriteBehavior`, description, `dataVersion`, column selection, managed identity; export type **FocusCost** (FOCUS format) only works by passing a string; `Monthly` granularity |
| Settings | 4 | tag inheritance only | No list or get |
| **Generate Cost Details Report** | 2 | none | The replacement for all Usage Details APIs: an asynchronous job that produces **charge-level rows with tags** as files in blob storage. See 3.1. |
| Generate Detailed Cost Report | 3 | none | Legacy enterprise variant of the above |
| **Alerts** | 4 | none | List and dismiss Cost Management alerts: budget, forecast budget, credit, quota, invoice. See 3.3 |
| **Scheduled actions** | 12 | none | Email a saved view on a schedule, and **anomaly alerts** (`kind: InsightAlert`, not available in Azure Government) |
| **Views** | 8 | none | Saved cost-analysis views (the thing scheduled actions point at) |
| Price Sheet | 3 | none | Download your **negotiated** price sheet (EA and MCA billing scopes) |
| Benefit Recommendations, Benefit Utilization Summaries, Generate Reservation Details Report | 1 + 4 + 8 | none | Savings plan and reservation data, relevant to PTU reservations |
| Cost Allocation Rules | 5 | none | Allocate shared costs between resources, EA and MCA billing scope only; source and target lists hold one element each |
| Markup Rules | 4 | none | New in 2026-06-01, MCA billing profile for partners |

`Microsoft.Consumption` (2026-06-01) is mostly superseded by the above. Microsoft's own description of the Cost Details
API is "the replacement for all previously released Usage Details APIs". Still useful there: `Tags` (all tag keys for a
scope), `PriceSheet`, `Balances`, `Credits`, `Lots`, `Events`, `Charges`, `AggregatedCost` (management groups),
`Reservation*` and `Marketplaces`.

## 3. Foundry and Azure OpenAI (`Microsoft.CognitiveServices`, 2026-11-01 and 2026-11-15-preview)

### 3.1 Cost controls (preview): the SDK matches the spec, with caveats

The preview adds `costControls` under both accounts and projects, exactly the paths the SDK uses. The model
(category, amount, time grain, time period, filters, grouping, one notification per control, one Block action) matches.
Points from the spec that the SDK now enforces or that you should know:

* **At most one alert and one Block action per cost control.** More returns HTTP 400.
* Grouping holds at most one item (`project`, `agent` or `sessionId`; `project` only for account-owned controls).
* An `agent` filter needs a `project` filter with exactly one project, even on project-owned controls.
* Token amounts and absolute token thresholds must be whole numbers.
* Names in filters are short names, 1 to 64 characters, without path or query separators.
* Reading, deleting and enforcing **identity selections** needs the `Microsoft.CognitiveServices/CostControlsByIdentity`
  feature registered, otherwise HTTP 403 `FeatureNotRegistered`.
* The spec has **no usage endpoint** for cost controls, which is why `LiveService` estimates usage.
* Missing in the SDK: the read-only `etag` and `If-Match` on update and delete (optimistic concurrency).

### 3.2 Deployments, capacity and quota

| Feature | API | SDK today | Gap |
|---|---|---|---|
| Deployment fields `serviceTier` (`Default` or `Priority`, changes price), `deploymentState` (`Running` or `Paused`), `spilloverDeploymentName`, `routing` (model router), `speculativeDecoding` | deployments | not modelled | Add to `Deployment`; `serviceTier` and spillover matter for cost |
| Change capacity | `PATCH deployments/{name}` (`sku.capacity`) | **fixed**: it used a full PUT that reset those fields | |
| **Commitment plans** and account associations | `commitmentPlans` (subscription, resource group, account) | none | Commitment tier, period, quota, auto renew, current and next period |
| **Commitment tiers** | `locations/{loc}/commitmentTiers` | none | |
| **Model capacities** | `modelCapacities`, `locations/{loc}/modelCapacities` | none | Available capacity per model, SKU and region |
| **Calculate model capacity** | `POST calculateModelCapacity` | none | PTU sizing from a workload |
| **Quota tiers** | `quotaTiers` | none | Subscription quota tier, upgrade eligibility, tier upgrade policy |
| SKUs | account and deployment `skus` | none | Available SKUs for an account or deployment |
| Managed compute capacities and usages (preview) | `managedComputeCapacities`, `managedComputeUsages` | none | Accelerator capacity per type |
| Region and account usages, models | `usages`, `models` | `fo.quota.usages`, `models`, `account_usages` | none |

### 3.3 Azure Monitor metrics for Foundry (the SDK knows 9 of about 120 metric names)

| Metric | What it gives | Status in the SDK |
|---|---|---|
| **`FoundryModelEstimatedCost` (Preview)** | **Estimated USD cost per project, deployment, model and region, per minute** | Not in `FoundryMetrics`. This is the near-real-time answer to "what is project X spending right now?". The documentation calls it an estimate, not an invoice. Not exportable. |
| `ProvisionedUtilization`, `AzureOpenAIProvisionedManagedUtilizationV2` | PTU utilisation percent (at or above 100 percent means HTTP 429) | missing |
| `ActiveTokens` | Tokens minus cached tokens, split by service tier (request and response) | missing |
| `AzureOpenAIContextTokensCacheMatchRate` | Prompt cache hit rate | missing (the SDK computes it from tracing only) |
| `ModelRouterInputTokens`, `...OutputTokens`, `...CachedTokens`, `...Requests`, `...FallbackCount` | Model router usage | missing |
| `AudioPromptTokens`, `AudioCompletionTokens`, `VoiceLive*Tokens` | Audio and voice tokens | missing |
| Anthropic cache metrics (`cacheReadInputTokens`, `ephemeral1hInputTokens`, `ephemeral5mInputTokens`) | Cache read and write tokens | missing |
| `ModelRequests`, `InputTokens`, `OutputTokens`, `TotalTokens` | Generic model metrics | present as `MODEL_*` |
| `ProcessedPromptTokens`, `GeneratedTokens`, `TokenTransaction`, `AzureOpenAIRequests`, `AzureOpenAITTLTInMS` | Core OpenAI metrics | present |
| `QuotaLimit`, `Ratelimit`, `RAI*`, `BlockedCalls`, error and latency metrics | Limits, safety, reliability | missing |

## 4. API Management reports and quotas (2024-05-01)

| API | Operations | SDK today | Gap |
|---|---|---|---|
| Reports | `reports/byApi`, `byProduct`, `bySubscription`, `byUser`, `byOperation`, `byGeo`, `byTime`, `byRequest` | none | Calls, bandwidth and latency per team (subscription or product) for chargeback. These are call counts, not tokens. |
| Quotas | `quotas/{counterKey}` and `periods/{periodKey}` (read and update) | none | Read and reset quota counters |

## 5. Defects found while reviewing, now fixed

1. **Multiple cost-control alerts.** An earlier change added several alert thresholds per cost control. The spec allows
   exactly one alert and one Block action and answers HTTP 400 otherwise. Reverted and now validated.
2. **Changing a deployment's capacity reset other settings.** `set_capacity` sent a full PUT with a short allow-list of
   properties, so a `Priority` service tier, a spillover deployment or a paused state would have been reset to defaults.
   It now sends a PATCH of `sku.capacity` only.
3. **Budget periods were rewritten.** A budget with `Last7Days`, `Last30Days` or `BillingMonth/Quarter/Annual` was read
   as `Monthly`, so re-saving it silently changed it. Unknown periods are now kept as they are, and the budget-only
   periods are refused for cost controls.
4. Absolute thresholds on Token budgets must be whole numbers: now validated.

Checked and fine: `granularity: "None"` in cost queries (it appears in the official examples), the cost-control and
account paths, and the tag-inheritance path.

## 6. Recommended additions, in order

**High value, modest effort**

1. **Near-real-time project cost** from the `FoundryModelEstimatedCost` metric: add it to `FoundryMetrics`, a
   `fo.metrics.estimated_cost(account, by="project")` helper, a `download` file and a CLI report. Use it for the
   cost-control usage estimate instead of billed cost with its 8 to 24 hour lag.
2. **Cost details report** (`GenerateCostDetailsReport`): `fo.cost.generate_cost_details(scope, start, end)` that starts
   the job, polls it, and returns or downloads the blob files. This is the only API that returns charge-level rows with
   all tags, needed for per-project, per-agent and per-team chargeback beyond what Query aggregates.
3. **Move to current API versions** (Cost Management `2026-06-01`, Cognitive Services `2026-11-01`, tag inheritance to
   the stable version), add the new `Deployment` fields (`serviceTier`, `deploymentState`, `spilloverDeploymentName`),
   and `etag` and `If-Match` on cost controls.
4. **PTU and capacity planning:** PTU utilisation and `ActiveTokens` metrics, `commitmentPlans`, `commitmentTiers`,
   `modelCapacities`, `calculateModelCapacity` and `quotaTiers`.

**Useful**

5. Cost Management **alerts** (list and dismiss), **scheduled actions** including anomaly alerts, and saved **views**.
6. Full **export** configuration: get, Parquet, FOCUS, compression, partitioning, overwrite behaviour, managed identity.
7. **Price sheet** download for your negotiated prices, to feed `PriceSheet` instead of retail prices.
8. APIM **reports** and **quotas**, for per-team call volume.
9. Query ergonomics: `or` and `not` filters, the 2 group-by check, the extra time frames, dimension `$filter` and `$top`.

**Niche**

10. Cost allocation rules, markup rules, benefit and reservation utilisation, billing invoices, balances and credits,
    management-group aggregated cost, multi-cloud queries.

## 7. Notes on method and limits

* The cost-control preview has no public narrative documentation yet in the search results; the OpenAPI definition is the
  only source, so behaviour beyond what the spec says is unknown.
* "Preview" here means the version name contains `-preview`. A few stable APIs (for example Cost Management alerts) have
  behaviour that depends on your billing type (EA, MCA, CSP, pay-as-you-go); the specs do not describe that fully.
* The estimated-cost metric is marked Preview and as an estimate by Microsoft. Billed cost from Cost Management remains
  the system of record.
