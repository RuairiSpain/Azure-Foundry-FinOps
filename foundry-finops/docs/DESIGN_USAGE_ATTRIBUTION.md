# Design: splitting shared agent and model cost between cost centres by token usage

Status: first slice implemented (see section 8); the rest is proposal. Written 21 November 2026.

## 1. Problem

A cost centre in the portal can share a project, agent, deployment or tag value with other cost centres, but only by a
fixed `share_percent` that someone types in (`portal/backend/src/foundry_finops_portal/services.py`, `cost_centre_report`;
`db.py`, table `allocation_rules`). The percentages do not follow usage, so they drift as usage changes.

Azure Monitor reports tokens per deployment and model only. It does not say who made the call. To split by usage we need
the caller recorded on each model call, then a way to turn a caller into a department or tag, then an allocation that adds
up to the billed cost.

## 2. Proposal

1. Record the caller's identity on every model call.
2. Resolve each identity to a department and custom tags through Microsoft Graph, and keep a dated snapshot.
3. Map department and tag values to cost centres.
4. Split each shared pool's billed cost between cost centres in proportion to each one's share of usage cost.
5. Optionally publish the result as Azure cost allocation rules.

## 3. Findings

| Idea | Verdict | Why |
|---|---|---|
| Save the Entra ID of the signed-in user on each model call | Feasible | `UsageTracker` and `finops_context(user=...)` in `src/foundry_finops/tracing.py` already attach attributes per call and emit metrics and an `llm.usage` log record. |
| Fall back to the agent identity when no user is signed in | Needs change | Two cases are lost, see 4.2. |
| Resolve department and tags with Microsoft Graph | Feasible, with care | See 4.4. |
| Map the values to cost centres in "the new preview cost management and allocation SDK" | Not confirmed | See 4.6. The Azure feature documented in `docs/API_COVERAGE.md` does not split by usage. |

## 4. Design

### 4.1 Call record

One record per model call, with these fields:

| Field | Notes |
|---|---|
| `timestamp`, `deployment`, `model` | As today. |
| `caller_oid`, `caller_tid` | Entra object ID and tenant ID of the caller. Never name or email. |
| `caller_type` | `user`, `agent` or `service`. |
| `agent_id` | The agent's own identity, recorded even when a user is present. |
| `owner_cost_centre` | Optional. The cost centre to charge when there is no user (scheduled or autonomous runs). |
| `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens` | Priced differently, so kept apart. |
| `est_cost` | Tokens multiplied by the price in force on the call date. |
| `status` | Success, failed, cancelled, content-filtered. Counting rules in 4.5. |
| `trace_id` | For joining the child calls of one request. |

Record both the user and the agent instead of choosing one. A single field, "user if available, else agent", loses the
on-behalf-of case (both are involved) and the autonomous case (there is no user, and the cost should go to the agent's
owner, not to an "agent" bucket).

### 4.2 Capture point

Two options.

- **Client side** (`UsageTracker`). Works without new infrastructure. It only sees calls made through instrumented code,
  so direct REST calls, other SDKs, the Foundry playground and scripts are missing.
- **Gateway** (API Management in front of the models). Reads the identity from the validated JWT and emits the record for
  every caller, instrumented or not. More reliable, and the identity cannot be omitted by the client.

Recommendation: gateway where one can be deployed; client side as the fallback. In both cases take `oid` and `tid` from
the validated token in the service layer. An identity supplied by calling code can be omitted or spoofed.

To correct for either option, add a reconciliation report: traced tokens per deployment per day against the Azure Monitor
total. Show the difference as "unattributed" instead of hiding it.

### 4.3 Propagation through agents

One user request can fan out to several model and tool calls. Carry the identity in trace baggage so child calls inherit
the user. Without that, child calls are attributed to the agent.

### 4.4 Identity to department

A daily job reads each `caller_oid` seen that day and stores a snapshot row:

`(date, oid, department, tags, manager_oid)`

- Snapshot instead of resolving at report time. If someone changes department mid-month, resolving at report time moves
  all of their usage. A dated snapshot keeps historical splits stable and repeatable.
- Department is free text. Keep a normalisation table (`"Fin"`, `"Finance "` → `Finance`) and a mapping table from
  normalised value to cost centre.
- Custom tags come from directory extension attributes or a table the portal owns.
- Fallback buckets: guests, service principals, disabled or deleted accounts, and users with no department. Each goes to a
  named "unattributed" line, never silently dropped.
- Graph: batch the lookups and cache them. Grant the portal's identity the narrowest permission that reads user profiles,
  because it is sensitive.

### 4.5 Allocation

For each shared pool (a set of agents, deployments or tools) over a period:

1. Sum each caller's `est_cost` and roll it up to a cost centre through the snapshot and mapping.
2. Scale to the billed cost of the pool so the parts add up to the invoice. Cost is not proportional to tokens: input,
   cached input, output and reasoning tokens are priced differently, PTU deployments cost a fixed amount whatever the
   usage, and fine-tuned or hosted models add hosting charges.
3. Show two lines that are not usage: **idle capacity** (PTU cost beyond what the calls explain) and **unattributed**
   (calls with no resolvable identity or department). Each is either split in proportion to attributed usage or held
   back, as a per-pool setting.
4. Use the price in force on each call date, and reconcile each month against billed cost.

Counting rules to decide and document: retries (count once), cancelled and failed calls (count when the service reports
tokens), content-filtered calls (count when billed), streaming calls (request usage on the stream, or estimate and flag).

Sampling: Application Insights sampling drops records. Use unsampled counters or metrics for tokens and cost, not
sampled logs.

### 4.6 Publishing to Azure Cost Management

`docs/API_COVERAGE.md` records the current Azure Cost Allocation Rules API as EA and MCA billing scopes only, with one
source and one target per rule, splitting billed cost between resources, subscriptions or tag values. It works on resource
cost and tags, not on per-user token usage.

I have not seen the preview SDK referred to in the proposal and cannot confirm what it supports. Until that is checked:

- The usage-based split is computed by this project (portal or SDK).
- Publishing it to Cost Management is optional, as percentage rules between tag values regenerated each period.
- The portal stays the place the split is visible. A cost centre is a reporting label in the portal and is not written to
  Azure.

**Open item:** get the link or package name of the preview SDK and compare its limits with the above before depending on it.

### 4.7 Portal changes

- New allocation rule mode `usage` next to the percentage mode. A pool, a period and a mapping, with no typed percentage.
- New match types or a pool object, so a cost centre can hold agents, deployments and tools together. Today the match
  types are project, agent, deployment and tag only (`db.py` CHECK on `match_type`), and there is no model rule.
- The cost-centre trend chart (`_centre_series`) applies project rules only; extend it to the new mode.
- Report lines for idle capacity and unattributed, with the coverage figure from 4.2.

### 4.8 Privacy and security

- The object ID is personal data. Document the purpose, set a retention period for call records and snapshots, and limit
  who can read them.
- Show cost centre managers aggregated figures. Per-person usage is for administrators only, or off.
- Do not record prompts or completions (content recording stays off).

### 4.9 Attribution is optional per agent

Identity is opt-in. A call with no caller identity is recorded as `caller_type=unknown`; nothing fails.

- Its cost goes to the pool's "unattributed" line, or is split in proportion to attributed usage, as a per-pool setting.
- Existing percentage rules keep working. The usage mode applies only to pools that turn it on.
- Each agent gets a coverage figure (share of tokens with a known identity), so owners can see what to fix.

### 4.10 Automatic capture without code in the agent: what Microsoft documents

Checked against Microsoft Learn pages on 21 November 2026. Absence from a page is not proof a feature does not exist;
re-check before building.

| Mechanism | Needs developer code? | What it can fill | Finding |
|---|---|---|---|
| API Management in front of the models, `llm-emit-token-metric` with custom dimensions | No (the app points at the gateway) | Token counts, plus dimensions from the validated token | Policy takes `<dimension name=".." value="..">` where `value` can be a policy expression, so a validated JWT claim such as `oid` is possible. The page gives no claim example; treat that as unverified until tested. Limits: 5 custom dimensions per policy, 100 unique values per dimension, 1,000 active time series per namespace; beyond that data is silently discarded. Counts cached and reasoning tokens in preview only. Streaming needs `include_usage`, or counts are inaccurate if the stream is interrupted. |
| Global OpenTelemetry span processor | One setup call per app | Any attribute on every span | The Foundry client-side tracing page shows `provider.add_span_processor(...)` with `span.set_attribute("session.id", ...)` in `on_start`. The sample value is fixed. No per-request identity mechanism is documented. Marked experimental preview. |
| Trace baggage | One setting and propagation code | User identifiers passed to child calls | Python only. Off by default (`AZURE_TRACING_GEN_AI_TRACE_CONTEXT_PROPAGATION_INCLUDE_BAGGAGE`). The page warns it can expose PII and is sent to Azure OpenAI, where it may be logged. |
| Foundry diagnostic logs (`RequestResponse`) | No | Status, latency; possibly an identity blob | The common resource-log schema has an optional `identity` property. No Foundry page found that confirms it holds the Entra object ID. Test with a known identity before relying on it. |
| Foundry-added trace attributes | No | Agent identity | Hosted agents get project, agent name, version and ID on spans; `gen_ai.agent.id` is the agent key. External agents must set it themselves. Server-side traces run under the project's managed identity. |

Not found in any page read: a pre-call hook or trigger in Foundry that lets us inject fields without code, and a
standard end-user identity attribute (`user.id`, `enduser.id` or similar) on Foundry spans.

Consequences for the design:

- **Agent-side fields can be automatic.** Agent ID, project, deployment, model and token counts come from Foundry
  telemetry or the gateway with no agent code.
- **The end user's identity cannot come from the model layer.** Server-side calls run under the project's or agent's
  identity. The user's `oid` has to be taken where the user's token is validated (the app, API or gateway at the front
  door) and carried to the model call as a dimension, header or baggage.
- **Gateway metric limits matter.** Putting `oid` in an APIM metric dimension hits the 100-unique-values limit quickly.
  Send per-call records (gateway log or custom event) to Log Analytics for the per-person split, and keep metrics for
  coarse dimensions such as agent or department.
- **Recommended path:** gateway at the front door that validates the token, emits per-call records with `oid`, `tid`
  and the agent ID, and an optional span processor in apps that want trace-level detail. Agents that do neither fall into
  `unknown` (see 4.9).

Open items from this check: test an APIM `validate-jwt` claim as a dimension and as a logged field; inspect a
`RequestResponse` row for a known identity; confirm which APIM API schema type covers Foundry model deployments.

Sources: [llm-emit-token-metric policy](https://learn.microsoft.com/en-us/azure/api-management/llm-emit-token-metric-policy),
[Add client-side tracing to Foundry agents](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-client-side),
[Configure tracing for AI agent frameworks](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-framework),
[Enable diagnostic logging for Foundry](https://learn.microsoft.com/en-us/azure/foundry/how-to/diagnostic-logging).

## 5. Pitfalls and edge cases

| Case | Handling |
|---|---|
| Caller omits or spoofs identity | Take it from the validated token at the gateway. |
| Call not made through the tracker | Reconciliation report; prefer the gateway. |
| On-behalf-of call | Record user and agent separately. |
| Scheduled or autonomous run | Charge `owner_cost_centre`; otherwise "unattributed". |
| Fan-out to child calls | Trace baggage. |
| Department changes mid-month | Dated snapshot. |
| Guest, service principal, deleted user, no department | Named fallback bucket. |
| PTU or hosting cost with no per-call driver | Idle capacity line and a setting. |
| Price change during the month | Price by call date; monthly reconciliation. |
| Sampling drops records | Unsampled counters. |
| Streaming without `usage` | Request usage on the stream; flag estimates. |
| Free-text department | Normalisation and mapping tables. |

## 6. Delivery order

1. Settle the capture point (gateway or client) and write down the coverage target. Everything else depends on it.
2. Extend the call record and `UsageTracker`; add the reconciliation report.
3. Graph snapshot job and the department-to-cost-centre mapping.
4. Usage allocation mode in the portal, with idle and unattributed lines.
5. Optional publishing to Cost Management, after the open item in 4.6.

## 7. Decisions needed

- Gateway, client side, or both?
- Treatment of idle PTU and unattributed cost: split or held back?
- Retention period for call records and snapshots.
- Who may see per-person usage?
- Link to the preview Cost Management SDK.

## 8. Implementation status (21 November 2026)

Built, with tests:

| Part | Where | Notes |
|---|---|---|
| Optional caller identity on usage records | `foundry_finops.tracing` | `caller_oid`, `caller_tid`, `agent_id` through `finops_context`; `caller_type` derived (`user`, `agent`, `unknown`) and written to the `llm.usage` log record only, not to metric dimensions. `identity_from_claims` reads a validated token. No identity is `unknown`, never an error. |
| Usage-based split | `foundry_finops.analysis.allocate_by_usage` | Cost or tokens as the measure. Unattributed usage is spread (`proportional`) or held on its own row (`hold`). Parts add up to the total. Reports a coverage percentage. |
| Gateway policy | `examples/apim/` | **Prototype, not deployed.** XML parses and keeps its rules (test). Expressions, trace output and the log join are untested; the open checks are listed in its README. |
| Portal schema 2 | `portal/backend` `db.py` | `department_map` and a dated `department_snapshot`. A version 1 database is upgraded in place. |
| Portal split report | `GET /api/cost-centres/usage-split` | Administrators only. Department comes from Graph (`department` on the user) and is kept as a dated snapshot; people with no department are asked again on a later day. Department to cost centre mapping through `/api/cost-centres/department-map`. Returns unmapped departments and the coverage figure. |

| Live reader | `queries.usage_by_caller`, `LiveBackend.usage_by_caller` | Reads per-caller usage from Log Analytics (`PORTAL_LOGS_TARGET`). Two sources: `client` (the SDK's `llm.usage` records; estimated cost; its fields are the ones the tracker writes, covered by a test) and `gateway` (policy trace records joined to the gateway LLM log by request ID; tokens only). **The gateway query is untested against a real instance**: table and column names come from Microsoft's documentation and must be checked. With no target configured the endpoint answers 501 and names the setting. Tests use a fake `kql`; nothing has run against Azure. |
| Test deployment | `examples/apim/deploy/` | Bicep template (workspace, Application Insights, API Management with a managed identity, the policy on one chat-completions API, diagnostic settings) plus `deploy.sh` and `smoke-test.sh`. The template compiles (checked in CI) and the scripts parse; **neither has been run against a real subscription**. The smoke test sends a call with no token, one with a bad token and one with a real token, then runs the portal's gateway query. |
| Measure | `services.usage_split` | Estimated cost when the records carry it; tokens otherwise, and the report says which. Tokens ignore price differences between models. |

Not built yet:

- **Bicep settings.** `PORTAL_LOGS_TARGET` and `PORTAL_USAGE_SOURCE` are set by hand (see `portal/docs/DEPLOY.md`).
- **Pools.** The split uses the whole Foundry resource's cost. Pools of chosen agents, deployments and tools are not
  built.
- **Idle capacity.** PTU cost with no per-call driver is not shown on its own line.
- **Portal pages.** There is no screen for the report or the department mapping; the API is there.
- **Publishing to Azure Cost Management** (4.6), and the preview SDK check.
- **Reconciliation** of traced tokens against Azure Monitor totals.
- **Retention** of call records and snapshots, and a rule for who may read per-person data beyond "administrators only".
