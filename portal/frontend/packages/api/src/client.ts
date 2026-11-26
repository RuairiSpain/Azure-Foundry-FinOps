import type { AuthProvider } from "./auth";
import type * as T from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiClientOptions {
  auth: AuthProvider;
  /** Where the API lives. Default: the same origin (the portal serves its own frontend). */
  baseUrl?: string;
  fetchImpl?: typeof fetch;
}

const qs = (params: Record<string, string | number | undefined | null>): string => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

/**
 * Every call the pages make. Pages take an `ApiClient` (or anything shaped like it), so you can swap in your own
 * implementation, for example one that adds headers or talks to another backend.
 */
export class ApiClient {
  private auth: AuthProvider;
  private base: string;
  private f: typeof fetch;

  constructor(opts: ApiClientOptions) {
    this.auth = opts.auth;
    this.base = (opts.baseUrl ?? "").replace(/\/$/, "");
    this.f = opts.fetchImpl ?? ((...a) => fetch(...a));
  }

  async request<R>(method: string, path: string, body?: unknown): Promise<R> {
    const headers: Record<string, string> = { Accept: "application/json", ...(await this.auth.getAuthHeaders()) };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const res = await this.f(`${this.base}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    if (res.status === 401) this.auth.onSignedOut?.();
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const j = (await res.json()) as { detail?: unknown };
        if (typeof j.detail === "string") detail = j.detail;
        else if (Array.isArray(j.detail)) detail = j.detail.map((d: { msg?: string }) => d.msg ?? "").join("; ");
      } catch {
        /* keep the status text */
      }
      throw new ApiError(res.status, detail);
    }
    return (res.status === 204 ? undefined : await res.json()) as R;
  }

  private get<R>(path: string) {
    return this.request<R>("GET", path);
  }

  me = () => this.get<T.Me>("/api/me");
  projects = () => this.get<{ name: string; display_name: string; team: string }[]>("/api/projects");
  fleet = (days = 30) => this.get<T.Fleet>(`/api/fleet${qs({ days })}`);
  operations = (hours = 168) => this.get<T.Operations>(`/api/operations${qs({ hours })}`);
  costOverview = (days = 30) => this.get<T.CostOverview>(`/api/cost/overview${qs({ days })}`);
  costSeries = (groupBy: string, days = 30) => this.get<T.Series>(`/api/cost/series${qs({ group_by: groupBy, days })}`);
  costTable = (groupBy: string, days = 30) => this.get<T.CostTable>(`/api/cost/table${qs({ group_by: groupBy, days })}`);
  projectMonitor = (project: string, days = 30) => this.get<T.ProjectMonitor>(`/api/projects/${encodeURIComponent(project)}/monitor${qs({ days })}`);

  costControls = (project?: string) => this.get<T.CostControls>(`/api/cost-controls${qs({ project })}`);
  saveCostControl = (payload: Record<string, unknown>) => this.request<T.CostControl>("POST", "/api/cost-controls", payload);
  deleteCostControl = (name: string, project?: string | null) =>
    this.request<{ deleted: string }>("DELETE", `/api/cost-controls/${encodeURIComponent(name)}${qs({ project })}`);

  budgets = () => this.get<T.Budget[]>("/api/budgets");
  saveBudget = (payload: Record<string, unknown>) => this.request<T.Budget>("POST", "/api/budgets", payload);
  deleteBudget = (name: string) => this.request<{ deleted: string }>("DELETE", `/api/budgets/${encodeURIComponent(name)}`);
  tags = () => this.get<T.TagRow[]>("/api/tags");
  setTags = (scope: string, tags: Record<string, string>) => this.request<Record<string, string>>("PUT", "/api/tags", { scope, tags });

  costCentres = () => this.get<T.CostCentre[]>("/api/cost-centres");
  centreReport = (basis = "project", days = 30) => this.get<T.CentreReport>(`/api/cost-centres/report${qs({ basis, days })}`);
  saveCostCentre = (c: Partial<T.CostCentre> & { code: string; name: string }, id?: number) =>
    this.request<T.CostCentre>(id ? "PUT" : "POST", id ? `/api/cost-centres/${id}` : "/api/cost-centres", c);
  deleteCostCentre = (id: number) => this.request<{ deleted: boolean }>("DELETE", `/api/cost-centres/${id}`);
  addRule = (id: number, rule: { match_type: string; match_value: string; match_key?: string; share_percent: number }) =>
    this.request<T.Rule>("POST", `/api/cost-centres/${id}/rules`, rule);
  deleteRule = (ruleId: number) => this.request<{ deleted: boolean }>("DELETE", `/api/cost-centres/rules/${ruleId}`);

  usageSplit = (days = 30, unattributed?: T.UnattributedMode, pool?: number) =>
    this.get<T.UsageSplit>(`/api/cost-centres/usage-split${qs({ days, unattributed, pool })}`);
  usageReconciliation = (days = 30) => this.get<T.Reconciliation>(`/api/cost-centres/usage-split/reconciliation${qs({ days })}`);
  allocationRules = (days = 30, costCentreTag?: string, sourceTag?: string) =>
    this.get<T.AllocationRules>(`/api/cost-centres/usage-split/allocation-rules${qs({ days, cost_centre_tag: costCentreTag, source_tag: sourceTag })}`);
  agentAliases = () => this.get<T.AgentAlias[]>("/api/agent-aliases");
  setAgentAlias = (alias: string, agent: string) => this.request<T.AgentAlias>("PUT", "/api/agent-aliases", { alias, agent });
  deleteAgentAlias = (alias: string) => this.request<{ deleted: boolean }>("DELETE", `/api/agent-aliases${qs({ alias })}`);
  usagePools = () => this.get<T.UsagePool[]>("/api/usage-pools");
  saveUsagePool = (pool: Omit<T.UsagePool, "id">, id?: number) =>
    this.request<T.UsagePool>(id ? "PUT" : "POST", id ? `/api/usage-pools/${id}` : "/api/usage-pools", pool);
  deleteUsagePool = (id: number) => this.request<{ deleted: boolean }>("DELETE", `/api/usage-pools/${id}`);
  departmentMap = () => this.get<T.DepartmentMapping[]>("/api/cost-centres/department-map");
  setDepartmentMap = (department: string, costCentreId: number) =>
    this.request<{ department: string; cost_centre_id: number }>("PUT", "/api/cost-centres/department-map", { department, cost_centre_id: costCentreId });
  deleteDepartmentMap = (department: string) =>
    this.request<{ deleted: boolean }>("DELETE", `/api/cost-centres/department-map${qs({ department })}`);

  notificationRules = () => this.get<T.NotificationRule[]>("/api/notification-rules");
  saveNotificationRule = (rule: T.NotificationRuleInput, id?: number) =>
    this.request<T.NotificationRule>(id ? "PUT" : "POST", id ? `/api/notification-rules/${id}` : "/api/notification-rules", rule);
  deleteNotificationRule = (id: number) => this.request<{ deleted: boolean }>("DELETE", `/api/notification-rules/${id}`);
  evaluateNotifications = () => this.request<{ sent: number }>("POST", "/api/notification-rules/evaluate");
  alertEvents = () => this.get<T.AlertEvent[]>("/api/alert-events");

  users = () => this.get<T.PortalUser[]>("/api/admin/users");
  lookupUser = (identifier: string) => this.get<T.DirectoryUser>(`/api/admin/directory/lookup${qs({ identifier })}`);
  addUser = (identifier: string, role: T.Role, grants: Omit<T.Grant, "id">[]) =>
    this.request<T.PortalUser>("POST", "/api/admin/users", { identifier, role, grants });
  updateUser = (oid: string, patch: { role?: T.Role; enabled?: boolean }) => this.request<T.PortalUser>("PATCH", `/api/admin/users/${oid}`, patch);
  setGrants = (oid: string, grants: Omit<T.Grant, "id">[]) => this.request<T.Grant[]>("PUT", `/api/admin/users/${oid}/grants`, grants);
  removeUser = (oid: string) => this.request<{ deleted: boolean }>("DELETE", `/api/admin/users/${oid}`);
  audit = (limit = 200) => this.get<T.AuditEntry[]>(`/api/admin/audit${qs({ limit })}`);
  accessPages = () => this.get<T.AccessPage[]>("/api/access/pages");
}

/** The public configuration the sign-in page needs. It is the only call that needs no sign-in. */
export async function fetchConfig(baseUrl = "", f: typeof fetch = (...a) => fetch(...a)): Promise<T.PortalConfig> {
  const res = await f(`${baseUrl.replace(/\/$/, "")}/api/config`);
  if (!res.ok) throw new ApiError(res.status, "Could not load the portal configuration");
  return (await res.json()) as T.PortalConfig;
}
