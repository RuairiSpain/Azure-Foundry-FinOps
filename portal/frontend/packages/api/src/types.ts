/** Shapes of the portal API's responses (see portal/backend). Fields the portal can't know are `null`, never invented. */

export interface PortalConfig {
  mode: "demo" | "entra";
  brand: string;
  read_only: boolean;
  demo_users?: { oid: string; name: string; upn: string }[];
  msal?: { client_id: string; tenant_id: string; authority: string; scope: string };
}

export type Role = "admin" | "owner" | "viewer";
export type ScopeType = "all" | "subscription" | "resource_group" | "account" | "project" | "cost_centre";

export interface Grant {
  id?: number;
  scope_type: ScopeType;
  scope_value: string;
  can_write: boolean;
}

export interface Me {
  oid: string;
  upn: string;
  name: string;
  role: Role;
  grants: Grant[];
  account_level: boolean;
  visible_projects: string[] | null;
  mode: "demo" | "entra";
  read_only: boolean;
  azure: { subscription: string; resource_group: string; account: string };
}

export interface SeriesItem {
  name: string;
  values: number[];
  total: number;
}
export interface Series {
  labels: string[];
  series: SeriesItem[];
  total: number;
}

export interface FleetProject {
  name: string;
  display_name: string;
  team: string;
  cost: number;
  previous_cost: number;
  trend_pct: number | null;
  new: boolean;
  agents: number;
  cost_controls: number;
  attention: number;
}
export interface PolicyViolation {
  resource_id: string;
  violations: number;
  policies: string[];
}
export interface Fleet {
  days: number;
  kpis: {
    projects: number;
    agents: number;
    deployments: number | null;
    cost: number;
    previous_cost: number;
    trend_pct: number | null;
    cost_controls_attention: number;
    policy_violations: number | null;
  };
  projects: FleetProject[];
  top_cost_increases: { name: string; increase: number; trend_pct: number | null }[];
  attention: { name: string; project: string | null; state: string; percent_used: number | null }[];
  policy: PolicyViolation[] | null;
  policy_note: string | null;
  currency: string;
  notes: string[];
}

export interface QuotaRow {
  name: string;
  display_name: string;
  current_value: number;
  limit: number;
  available: number;
  percent_used: number;
  unit: string;
}
export interface Operations {
  hours: number;
  labels: string[];
  series: SeriesItem[];
  kpis: { tokens: number; requests: number; deployments: number };
  deployments: {
    deployment: string;
    model: string | null;
    tokens: number;
    requests: number;
    tokens_per_request: number | null;
    tokens_per_minute_limit: number | null;
  }[];
  quota: QuotaRow[];
  notes: string[];
}

export interface Trend {
  value: number;
  previous: number;
  trend_pct: number | null;
}
export interface CostOverview {
  days: number;
  currency: string;
  estimated_cost: Trend;
  forecast: { value: number | null; note: string | null };
  spend_breakdown: { agent_cost: number; direct_model_cost: number };
  cost_controls: { total: number; attention: number };
  highest_spend: { name: string; cost: number }[];
  series: Series;
  notes: string[];
}
export interface CostRow {
  name: string;
  cost: number;
  share_pct: number | null;
  previous_cost: number | null;
  trend_pct: number | null;
  new: boolean;
}
export interface CostTable {
  group_by: string;
  days: number;
  rows: CostRow[];
  total: number;
  currency: string;
}

export interface Threshold {
  threshold: number;
  threshold_type?: string;
  event_grid_connection_id?: string | null;
}
export interface CostControl {
  name: string;
  project?: string | null;
  category: "Cost" | "Tokens";
  amount: number;
  time_grain: string;
  start_date: string | null;
  end_date: string | null;
  filters: Record<string, string[]>;
  sharing: string;
  alert?: Threshold | null;
  block?: Threshold | null;
  usage?: number | null;
  usage_note?: string | null;
  state: string;
  over_budget?: boolean;
  percent_used?: number | null;
  can_write?: boolean;
}
export interface CostControls {
  cost_controls: CostControl[];
  project_budget: { name: string | null; amount: number | null; count: number; per_agent_or_session: number } | null;
  note: string;
}
export interface ProjectMonitor {
  project: string;
  days: number;
  currency: string;
  latest_billed_cost: Trend;
  agents: { agent: string; cost: number }[];
  series: Series;
  controls: CostControls;
  notes: string[];
}

export interface Budget {
  name: string;
  amount: number;
  time_grain: string;
  start_date: string | null;
  end_date: string | null;
  current_spend: number | null;
  forecast_spend: number | null;
  currency: string | null;
  percent_used: number | null;
  notifications: { threshold: number; threshold_type: string; contact_emails: string[] }[];
}
export interface TagRow {
  scope: string;
  name: string;
  type: string;
  tags: Record<string, string>;
}

export interface Rule {
  id: number;
  cost_centre_id: number;
  match_type: "project" | "agent" | "deployment" | "tag";
  match_key: string;
  match_value: string;
  share_percent: number;
}
export interface CostCentre {
  id: number;
  code: string;
  name: string;
  owner_oid: string;
  description: string;
  monthly_budget: number | null;
  rules: Rule[];
}
export interface CentreReport {
  basis: string;
  days: number;
  currency: string;
  total: number;
  note: string;
  centres: {
    id: number;
    code: string;
    name: string;
    cost: number;
    monthly_budget: number | null;
    percent_of_budget: number | null;
    values: { value: string; share_pct: number; cost: number }[];
  }[];
  unallocated: { value: string; cost: number; unallocated_pct: number }[];
}

export interface NotificationRule {
  id: number;
  name: string;
  scope_type: ScopeType;
  scope_value: string;
  trigger: "percent_used" | "state" | "event";
  threshold: number | null;
  channel: "webhook" | "email" | "log";
  target: string;
  enabled: boolean;
  last_fired_at: string | null;
}
export type NotificationRuleInput = Omit<NotificationRule, "id" | "last_fired_at">;

export interface AlertEvent {
  id: number;
  received_at: string;
  cost_control: string;
  project: string;
  agent: string;
  threshold: string;
  unit: string;
  total_consumed: number | null;
  limit_value: number | null;
  notified: boolean;
}

export interface PortalUser {
  oid: string;
  upn: string;
  display_name: string;
  role: Role;
  enabled: boolean;
  added_by: string;
  added_at: string;
  grants: Grant[];
}
export interface DirectoryUser {
  oid: string;
  upn: string;
  display_name: string;
  enabled: boolean;
}
export interface AuditEntry {
  id: number;
  at: string;
  actor_oid: string;
  actor_upn: string;
  action: string;
  target: string;
  detail: Record<string, unknown>;
  outcome: "ok" | "denied" | "error";
}
export interface AccessPage {
  page: string;
  azure_permissions: string[];
  portal_access: boolean;
  message: string | null;
}

/** One cost centre's part of a usage split. `code` is the unattributed code for usage nobody can name. */
export interface SplitRow {
  code: string;
  name: string;
  allocated: number;
  share_percent: number;
}
export interface SplitResult {
  total: number;
  mode: "proportional" | "hold";
  coverage_percent: number;
  unattributed_percent: number;
  rows: SplitRow[];
  unmapped_departments: { department: string; usage: number }[];
}
export interface PoolSplit extends SplitResult {
  /** The unused part of a fixed cost when the records carry cost; null when they only have tokens. */
  idle_cost: number | null;
  idle_setting: IdleMode;
  idle_note?: string;
  id: number;
  name: string;
  match_type: "agent" | "deployment";
  values: string[];
  /** False when no usage record carried one of the pool's members, so all of its cost is unattributed. */
  matched_usage: boolean;
}
/** With no pools, the whole-resource fields are set; with pools, `pools`, `totals` and the pooled costs are. */
export interface UsageSplit extends Partial<SplitResult> {
  days: number;
  currency: string;
  measure: "cost" | "tokens";
  unattributed_code: string;
  note: string;
  pools: PoolSplit[];
  /** Agent names in the usage records that match no billing agent, with their usage. */
  unmatched_agents: { agent: string; usage: number }[];
  totals?: { code: string; name: string; allocated: number }[];
  pooled_cost?: number;
  unpooled_cost?: number;
}
export type UnattributedMode = "proportional" | "hold";
export type IdleMode = "spread" | "hold";
export interface UsagePool {
  id: number;
  name: string;
  match_type: "agent" | "deployment";
  unattributed: UnattributedMode;
  idle: IdleMode;
  values: string[];
}
export interface DepartmentMapping {
  department: string;
  cost_centre_id: number;
  cost_centre_code: string;
}

export type ReconcileStatus = "ok" | "under" | "over" | "none_recorded" | "not_in_monitor";
export interface Reconciliation {
  days: number;
  measure: string;
  tolerance_percent: number;
  azure_total: number;
  recorded_total: number;
  coverage_percent: number | null;
  unrecorded: number;
  not_in_monitor: string[];
  note: string;
  rows: { name: string; recorded: number; azure: number | null; difference: number | null; coverage_percent: number | null; status: ReconcileStatus }[];
}
export interface AgentAlias {
  alias: string;
  agent: string;
}
export interface AllocationRules {
  days: number;
  source_tag: string;
  target_tag: string;
  rules: { name: string; pool: string; excluded_percent: number; definition: Record<string, unknown> }[];
  skipped: { pool: string; reason: string }[];
  api: { method: string; api_version: string; path: string };
  notes: string[];
}
