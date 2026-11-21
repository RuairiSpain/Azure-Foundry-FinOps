# Design: splitting shared agent and model cost between cost centres by token usage

Status: proposal, not implemented. Written 21 November 2026.

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
