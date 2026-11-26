import type { ApiClient, Me } from "@foundry-finops/api";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AdminPage, CostControlForm, FleetPage, PortalProvider, UsageSplitPage, defaultPages } from "./index";

const me = (over: Partial<Me> = {}): Me => ({
  oid: "o1", upn: "a@x", name: "A", role: "admin", grants: [], account_level: true, visible_projects: null, mode: "demo",
  read_only: false, azure: { subscription: "s", resource_group: "rg", account: "acct" }, ...over,
});

const fleet = {
  days: 30, currency: "USD", notes: ["n1"],
  kpis: { projects: 2, agents: 3, deployments: null, cost: 120.5, previous_cost: 100, trend_pct: 20.5, cost_controls_attention: 1, policy_violations: null },
  projects: [
    { name: "p1", display_name: "p1", team: "", cost: 100, previous_cost: 0, trend_pct: null, new: true, agents: 2, cost_controls: 1, attention: 1 },
    { name: "p2", display_name: "p2", team: "", cost: 20.5, previous_cost: 100, trend_pct: -79.5, new: false, agents: 1, cost_controls: 0, attention: 0 },
  ],
  top_cost_increases: [], attention: [{ name: "cap", project: "p1", state: "Near threshold", percent_used: 91 }],
  policy: null, policy_note: "Policy violations unavailable: you may lack Microsoft.PolicyInsights/*/read.",
};

const wrap = (api: Partial<ApiClient>, ui: React.ReactElement, meValue = me(), path = "/") => render(
  <PortalProvider api={api as ApiClient} me={meValue}>
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="*" element={ui} /></Routes>
    </MemoryRouter>
  </PortalProvider>,
);

describe("FleetPage", () => {
  it("shows what Azure gave and a dash for what it didn't", async () => {
    wrap({ fleet: vi.fn().mockResolvedValue(fleet) }, <FleetPage />);
    expect(await screen.findByText("$120.50")).toBeInTheDocument();       // total cost
    expect(screen.getByText("New")).toBeInTheDocument();               // p1 had no cost before
    expect(screen.getByText(/cap \(Near threshold, 91%\)/)).toBeInTheDocument();
    expect(screen.getByText(/lack Microsoft.PolicyInsights/)).toBeInTheDocument();   // the policy card fails by itself
    expect(screen.getAllByText("–").length).toBeGreaterThan(0);        // deployments and violations are unknown, not zero
  });

  it("shows its own error with a retry and reloads when the range changes", async () => {
    const f = vi.fn().mockRejectedValueOnce(new Error("Azure: 403")).mockResolvedValue(fleet);
    wrap({ fleet: f }, <FleetPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Azure: 403");
    await userEvent.click(screen.getByText("Try again"));
    expect(await screen.findByText("$120.50")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Date range"), "90");
    await waitFor(() => expect(f).toHaveBeenLastCalledWith(90));
  });
});

describe("CostControlForm", () => {
  const setup = () => {
    const api = { saveCostControl: vi.fn().mockResolvedValue({}) };
    const onSaved = vi.fn();
    wrap(api, <CostControlForm projects={["p1", "p2"]} onClose={() => undefined} onSaved={onSaved} />);
    return { api, onSaved };
  };

  it("applies the service's rules before calling the API", async () => {
    const { api } = setup();
    await userEvent.click(screen.getByText("Save"));
    expect(screen.getByRole("alert")).toHaveTextContent("1 to 63 letters");
    await userEvent.type(screen.getByLabelText("Cost control name"), "cap-1");
    await userEvent.click(screen.getByText("Save"));
    expect(screen.getByRole("alert")).toHaveTextContent("above zero");
    await userEvent.type(screen.getByLabelText("Amount (USD)"), "500");
    await userEvent.clear(screen.getByLabelText("End date (UTC)"));
    await userEvent.click(screen.getByText("Save"));
    expect(screen.getByRole("alert")).toHaveTextContent("Both dates are required");
    expect(api.saveCostControl).not.toHaveBeenCalled();
  });

  it("refuses an alert above the block and an agent filter without a project", async () => {
    setup();
    await userEvent.type(screen.getByLabelText("Cost control name"), "cap");
    await userEvent.type(screen.getByLabelText("Amount (USD)"), "5");
    await userEvent.clear(screen.getByLabelText("Send alert at (% of budget)"));
    await userEvent.type(screen.getByLabelText("Send alert at (% of budget)"), "95");
    await userEvent.clear(screen.getByLabelText("Block usage at (% of budget)"));
    await userEvent.type(screen.getByLabelText("Block usage at (% of budget)"), "90");
    await userEvent.click(screen.getByText("Save"));
    expect(screen.getByRole("alert")).toHaveTextContent("at or below the block");
  });

  it("sends the payload the backend expects", async () => {
    const { api, onSaved } = setup();
    await userEvent.type(screen.getByLabelText("Cost control name"), "fraud-cap");
    await userEvent.selectOptions(screen.getByLabelText("Owner"), "p1");
    await userEvent.type(screen.getByLabelText("Amount (USD)"), "250");
    await userEvent.type(screen.getByLabelText("Agents (optional)"), "a1, a2");
    await userEvent.selectOptions(screen.getByLabelText("How should the budget be shared?"), "agent");
    await userEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const body = api.saveCostControl.mock.calls[0]![0];
    expect(body).toMatchObject({
      name: "fraud-cap", project: "p1", category: "Cost", amount: 250, time_grain: "Monthly", sharing: "agent",
      filters: { project: ["p1"], agent: ["a1", "a2"] }, alert: { threshold: 80 }, block: { threshold: 100 },
    });
  });

  it("shows the backend's refusal in the form", async () => {
    const api = { saveCostControl: vi.fn().mockRejectedValue(new Error("Your portal access doesn't include changing this.")) };
    wrap(api, <CostControlForm projects={[]} onClose={() => undefined} onSaved={() => undefined} />);
    await userEvent.type(screen.getByLabelText("Cost control name"), "x");
    await userEvent.type(screen.getByLabelText("Amount (USD)"), "5");
    await userEvent.click(screen.getByText("Save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("doesn't include changing this");
  });
});

describe("AdminPage", () => {
  it("is for administrators only", () => {
    wrap({}, <AdminPage />, me({ role: "owner" }));
    expect(screen.getByText("Portal administrators only.")).toBeInTheDocument();
  });

  it("checks Entra before adding a person", async () => {
    const api = {
      users: vi.fn().mockResolvedValue([]), audit: vi.fn().mockResolvedValue([]),
      lookupUser: vi.fn().mockResolvedValueOnce({ oid: "x", upn: "new@x.com", display_name: "New Person", enabled: true }),
      addUser: vi.fn().mockResolvedValue({}),
    };
    wrap(api, <AdminPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a person" }));
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();                        // nobody can be added before Entra confirms they exist
    await userEvent.type(screen.getByLabelText(/Email \(user principal name\)/), "new@x.com");
    await userEvent.click(screen.getByText("Check"));
    expect(await screen.findByText(/exists in Entra ID/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("Add access"));
    await userEvent.type(screen.getByLabelText("Scope value"), "fraud-risk");
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(api.addUser).toHaveBeenCalledWith("new@x.com", "viewer", [{ scope_type: "project", scope_value: "fraud-risk", can_write: false }]);
  });

  it("reports why a lookup failed", async () => {
    const api = { users: vi.fn().mockResolvedValue([]), audit: vi.fn().mockResolvedValue([]), lookupUser: vi.fn().mockRejectedValue(new Error("Entra ID has no such user")) };
    wrap(api, <AdminPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a person" }));
    await userEvent.type(screen.getByLabelText(/Email \(user principal name\)/), "ghost@x.com");
    await userEvent.click(screen.getByText("Check"));
    expect(await screen.findByText("Entra ID has no such user")).toBeInTheDocument();
  });
});

describe("defaultPages", () => {
  it("hides whole-resource and admin pages from people who may not use them", () => {
    const limited = me({ role: "viewer", account_level: false });
    const visible = defaultPages.filter((p) => !p.visible || p.visible(limited)).map((p) => p.id);
    expect(visible).not.toContain("operations");
    expect(visible).not.toContain("budgets");
    expect(visible).not.toContain("admin");
    expect(visible).toContain("fleet");
    const admin = defaultPages.filter((p) => !p.visible || p.visible(me())).map((p) => p.id);
    expect(admin).toEqual(expect.arrayContaining(["operations", "budgets", "admin"]));
  });

  it("keeps every route unique so a customer's added page can't shadow one", () => {
    const paths = defaultPages.map((p) => p.path);
    expect(new Set(paths).size).toBe(paths.length);
    expect(within(document.body).queryAllByText("x")).toEqual([]);
  });
});

describe("UsageSplitPage", () => {
  const rows = [
    { code: "CC-RISK", name: "Risk and compliance", allocated: 75, share_percent: 75 },
    { code: "(unattributed)", name: "Usage with no cost centre", allocated: 25, share_percent: 25 },
  ];
  const whole = {
    days: 30, currency: "USD", measure: "cost" as const, unattributed_code: "(unattributed)", note: "An allocation, not billed cost.",
    pools: [], unmatched_agents: [], total: 100, mode: "hold" as const, coverage_percent: 75, unattributed_percent: 25, rows,
    unmapped_departments: [{ department: "Finance", usage: 40 }],
  };
  const pooled = {
    days: 30, currency: "USD", measure: "tokens" as const, unattributed_code: "(unattributed)", note: "Pool members must match.",
    pools: [
      { id: 1, name: "Support agents", match_type: "agent" as const, values: ["care-triage"], total: 60, mode: "proportional" as const,
        coverage_percent: 0, unattributed_percent: 100, rows: [{ code: "(unattributed)", name: "x", allocated: 60, share_percent: 100 }],
        unmapped_departments: [], matched_usage: false, idle: "spread" as const, idle_cost: 12, idle_setting: "spread" as const },
    ],
    unmatched_agents: [{ agent: "0a1b-client-id", usage: 55 }],
    totals: [{ code: "(unattributed)", name: "x", allocated: 60 }], pooled_cost: 60, unpooled_cost: 40,
  };
  const centres = [{ id: 7, code: "CC-FIN", name: "Finance", owner_oid: "", description: "", monthly_budget: null, rules: [] }];
  type Mocks = Record<string, ReturnType<typeof vi.fn>>;
  const recon = {
    days: 30, measure: "tokens", tolerance_percent: 5, azure_total: 2000, recorded_total: 1500, coverage_percent: 75, unrecorded: 500,
    not_in_monitor: ["gpt-typo"], note: "Matched by name, ignoring case.",
    rows: [
      { name: "gpt-5.4", recorded: 1000, azure: 1000, difference: 0, coverage_percent: 100, status: "ok" },
      { name: "o4-mini", recorded: 500, azure: 1000, difference: 500, coverage_percent: 50, status: "under" },
      { name: "gpt-typo", recorded: 70, azure: null, difference: null, coverage_percent: null, status: "not_in_monitor" },
    ],
  };
  const api = (over: Mocks = {}): Mocks => ({
    usageReconciliation: vi.fn().mockResolvedValue(recon), agentAliases: vi.fn().mockResolvedValue([{ alias: "abc", agent: "care-triage" }]),
    usageSplit: vi.fn().mockResolvedValue(whole), usagePools: vi.fn().mockResolvedValue([]),
    departmentMap: vi.fn().mockResolvedValue([{ department: "Risk", cost_centre_id: 1, cost_centre_code: "CC-RISK" }]),
    costCentres: vi.fn().mockResolvedValue(centres), ...over,
  });

  it("shows the whole-resource split, how much usage was attributed and which departments have no cost centre", async () => {
    wrap(api(), <UsageSplitPage />);
    expect(await screen.findByText("$75.00")).toBeInTheDocument();
    expect(screen.getByText("Unattributed")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Usage with a known cost centre" })).toHaveAttribute("aria-valuenow", "75");
    expect(screen.getByText(/Finance \(\$40.00\)/)).toBeInTheDocument();      // a department with no cost centre, and its usage
    expect(screen.getAllByText("25.0%").length).toBeGreaterThan(0);      // shares are rounded for reading
  });

  it("shows pools, the cost outside them, and a pool that matched no usage", async () => {
    wrap(api({ usageSplit: vi.fn().mockResolvedValue(pooled) }), <UsageSplitPage />);
    expect(await screen.findByText(/Support agents · \$60.00/)).toBeInTheDocument();
    expect(screen.getByText(/No usage record names any of this pool's agents/)).toBeInTheDocument();
    expect(screen.getByText(/Outside every pool: \$40.00/)).toBeInTheDocument();
  });

  it("says what is wrong when the usage isn't connected, instead of showing numbers", async () => {
    const f = vi.fn().mockRejectedValueOnce(new Error("Per-caller usage isn't configured. Set PORTAL_LOGS_TARGET")).mockResolvedValue(whole);
    wrap(api({ usageSplit: f }), <UsageSplitPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("PORTAL_LOGS_TARGET");
    expect(screen.getByText("Departments")).toBeInTheDocument();       // the rest of the page still works
    await userEvent.click(screen.getByText("Try again"));
    expect(await screen.findByText("$75.00")).toBeInTheDocument();
  });

  it("asks again when the unattributed setting or the range changes", async () => {
    const a = api();
    wrap(a, <UsageSplitPage />);
    await screen.findByText("$75.00");
    await userEvent.selectOptions(screen.getByLabelText("Usage with no cost centre"), "hold");
    await waitFor(() => expect(a.usageSplit).toHaveBeenLastCalledWith(30, "hold"));
    await userEvent.selectOptions(screen.getByLabelText("Date range"), "7");
    await waitFor(() => expect(a.usageSplit).toHaveBeenLastCalledWith(7, "hold"));
  });

  it("maps a department to a cost centre and can remove a mapping", async () => {
    const a = api({ setDepartmentMap: vi.fn().mockResolvedValue({}), deleteDepartmentMap: vi.fn().mockResolvedValue({ deleted: true }) });
    wrap(a, <UsageSplitPage />);
    await screen.findByText("$75.00");
    const button = screen.getByText("Map department");
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Department"), " Finance ");
    await userEvent.selectOptions(screen.getByLabelText("Cost centre for the department"), "7");
    await userEvent.click(button);
    await waitFor(() => expect(a.setDepartmentMap).toHaveBeenCalledWith("Finance", 7));
    await userEvent.click(within(screen.getByRole("cell", { name: "Risk" }).closest("tr")!).getByText("Remove"));
    await waitFor(() => expect(a.deleteDepartmentMap).toHaveBeenCalledWith("Risk"));
  });

  it("checks a pool before saving it and saves its members as a list", async () => {
    const a = api({ saveUsagePool: vi.fn().mockResolvedValue({}) });
    wrap(a, <UsageSplitPage />);
    await screen.findByText("$75.00");
    await userEvent.click(screen.getByText("New pool"));
    await userEvent.click(screen.getByText("Save"));
    expect(screen.getByRole("alert")).toHaveTextContent("Give the pool a name");
    await userEvent.type(screen.getByLabelText("Name"), "Support");
    await userEvent.click(screen.getByText("Save"));
    expect(screen.getByRole("alert")).toHaveTextContent("at least one agent");
    await userEvent.type(screen.getByLabelText("Members"), "care-triage, kb-answers{enter}refund-assistant");
    await userEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(a.saveUsagePool).toHaveBeenCalledWith(
      { name: "Support", match_type: "agent", values: ["care-triage", "kb-answers", "refund-assistant"], unattributed: "proportional", idle: "spread" }, undefined));
  });

  it("shows the service's reason when a pool is refused", async () => {
    const a = api({ saveUsagePool: vi.fn().mockRejectedValue(new Error("'x' is already in pool 'Other'; a value can be in one pool only")) });
    wrap(a, <UsageSplitPage />);
    await screen.findByText("$75.00");
    await userEvent.click(screen.getByText("New pool"));
    await userEvent.type(screen.getByLabelText("Name"), "P");
    await userEvent.type(screen.getByLabelText("Members"), "x");
    await userEvent.click(screen.getByText("Save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("already in pool");
  });

  it("offers no changes in read-only mode, and the menu entry is for administrators", async () => {
    wrap(api(), <UsageSplitPage />, me({ read_only: true }));
    await screen.findByText("$75.00");
    expect(screen.queryByText("New pool")).not.toBeInTheDocument();
    expect(screen.queryByText("Map department")).not.toBeInTheDocument();
    expect(screen.queryByText("Remove")).not.toBeInTheDocument();
    const page = defaultPages.find((p) => p.id === "usage-split")!;
    expect(page.visible!(me({ role: "admin" }))).toBe(true);
    expect(page.visible!(me({ role: "viewer" }))).toBe(false);
    expect(page.visible!(me({ role: "owner" }))).toBe(false);
  });
});

describe("UsageSplitPage: idle capacity, reconciliation, agent names and rules", () => {
  type Mocks = Record<string, ReturnType<typeof vi.fn>>;
  const base = (over: Mocks = {}): Mocks => ({
    usageSplit: vi.fn().mockResolvedValue({
      days: 30, currency: "USD", measure: "cost", unattributed_code: "(unattributed)", note: "n", unmatched_agents: [],
      pools: [{
        id: 1, name: "PTU pool", match_type: "deployment", values: ["gpt-5.4"], total: 100, mode: "proportional", coverage_percent: 100,
        unattributed_percent: 0, unmapped_departments: [], matched_usage: true, idle: "hold", idle_setting: "hold", idle_cost: 60,
        rows: [{ code: "(idle capacity)", name: "x", allocated: 60, share_percent: 60 }, { code: "CC-1", name: "One", allocated: 40, share_percent: 40 }],
      }],
      totals: [{ code: "CC-1", name: "One", allocated: 40 }], pooled_cost: 100, unpooled_cost: 0,
    }),
    usagePools: vi.fn().mockResolvedValue([]), departmentMap: vi.fn().mockResolvedValue([]), costCentres: vi.fn().mockResolvedValue([]),
    usageReconciliation: vi.fn().mockResolvedValue({
      days: 30, measure: "tokens", tolerance_percent: 5, azure_total: 2000, recorded_total: 1500, coverage_percent: 75, unrecorded: 500,
      not_in_monitor: ["gpt-typo"], note: "Matched by name.",
      rows: [
        { name: "gpt-5.4", recorded: 1000, azure: 1000, difference: 0, coverage_percent: 100, status: "ok" },
        { name: "o4-mini", recorded: 500, azure: 1000, difference: 500, coverage_percent: 50, status: "under" },
        { name: "gpt-typo", recorded: 70, azure: null, difference: null, coverage_percent: null, status: "not_in_monitor" },
      ],
    }),
    agentAliases: vi.fn().mockResolvedValue([]), ...over,
  });

  it("shows a pool's unused fixed cost on its own row", async () => {
    wrap(base(), <UsageSplitPage />);
    expect(await screen.findByText("Idle capacity")).toBeInTheDocument();
    expect(screen.getByText("$60.00")).toBeInTheDocument();
  });

  it("says when unused fixed cost was spread instead, and why a hold wasn't possible", async () => {
    const r = (await base().usageSplit!()) as { pools: Record<string, unknown>[] };
    r.pools[0] = { ...r.pools[0], rows: [{ code: "CC-1", name: "One", allocated: 100, share_percent: 100 }], idle: "spread", idle_cost: 25, idle_note: "Idle capacity needs estimated cost." };
    wrap(base({ usageSplit: vi.fn().mockResolvedValue(r) }), <UsageSplitPage />);
    expect(await screen.findByText(/\$25.00 is fixed cost that the recorded calls did not use/)).toBeInTheDocument();
    expect(screen.getByText(/Idle capacity needs estimated cost/)).toBeInTheDocument();
  });

  it("shows how much of Azure's token count the records cover, per deployment", async () => {
    wrap(base(), <UsageSplitPage />);
    expect(await screen.findByText(/75.0%/)).toBeInTheDocument();
    expect(screen.getByText(/500 tokens are not accounted for/)).toBeInTheDocument();
    expect(screen.getByText("Matches")).toBeInTheDocument();
    expect(screen.getByText("Records hold less")).toBeInTheDocument();
    expect(screen.getByText("Not in Azure Monitor")).toBeInTheDocument();
  });

  it("shows the reconciliation's own error without hiding the split", async () => {
    wrap(base({ usageReconciliation: vi.fn().mockRejectedValue(new Error("Azure Monitor: 403")) }), <UsageSplitPage />);
    expect(await screen.findByText("PTU pool · $100.00")).toBeInTheDocument();
    expect(await screen.findByText(/Azure Monitor: 403/)).toBeInTheDocument();
  });

  it("offers to map an agent name that billing doesn't know, with the name filled in", async () => {
    const r = (await base().usageSplit!()) as Record<string, unknown>;
    const a = base({ usageSplit: vi.fn().mockResolvedValue({ ...r, unmatched_agents: [{ agent: "0a1b-client", usage: 12 }] }), setAgentAlias: vi.fn().mockResolvedValue({}) });
    wrap(a, <UsageSplitPage />);
    await userEvent.click(await screen.findByText("Map to a billing agent"));
    expect(screen.getByLabelText("Named in the usage records")).toHaveValue("0a1b-client");
    await userEvent.click(screen.getByText("Save"));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter both names");
    await userEvent.type(screen.getByLabelText("Billing agent name"), "care-triage");
    await userEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(a.setAgentAlias).toHaveBeenCalledWith("0a1b-client", "care-triage"));
  });

  it("lists agent names already mapped and removes one", async () => {
    const a = base({ agentAliases: vi.fn().mockResolvedValue([{ alias: "abc", agent: "care-triage" }]), deleteAgentAlias: vi.fn().mockResolvedValue({ deleted: true }) });
    wrap(a, <UsageSplitPage />);
    expect(await screen.findByText("abc")).toBeInTheDocument();
    const row = screen.getByText("abc").closest("tr")!;
    await userEvent.click(within(row).getByText("Remove"));
    await waitFor(() => expect(a.deleteAgentAlias).toHaveBeenCalledWith("abc"));
  });

  it("builds allocation rules for review, says nothing was sent, and shows what was skipped", async () => {
    const rules = {
      days: 30, source_tag: "agent.name", target_tag: "cc",
      rules: [{ name: "finops-Support", pool: "Support", excluded_percent: 40, definition: { properties: { status: "NotActive" } } }],
      skipped: [{ pool: "Models", reason: "Azure allocation rules can't use a deployment as a source" }],
      api: { method: "PUT", api_version: "2024-08-01", path: "/x" }, notes: ["For review only: nothing here has been sent to Azure."],
    };
    const a = base({ allocationRules: vi.fn().mockResolvedValue(rules) });
    wrap(a, <UsageSplitPage />);
    await screen.findByText("PTU pool · $100.00");
    await userEvent.clear(screen.getByLabelText("Cost centre tag name"));
    await userEvent.type(screen.getByLabelText("Cost centre tag name"), "cc");
    await userEvent.click(screen.getByText("Build rule definitions"));
    await waitFor(() => expect(a.allocationRules).toHaveBeenCalledWith(30, "cc"));
    expect(await screen.findByText(/40.0% left out/)).toBeInTheDocument();
    expect(screen.getByText("finops-Support")).toBeInTheDocument();
    expect(screen.getByText(/nothing here has been sent to Azure/)).toBeInTheDocument();
    expect(screen.getByText(/can't use a deployment as a source/)).toBeInTheDocument();
    expect(screen.getByText("Download JSON")).toBeInTheDocument();
  });

  it("saves a pool's idle setting", async () => {
    const a = base({ saveUsagePool: vi.fn().mockResolvedValue({}) });
    wrap(a, <UsageSplitPage />);
    await screen.findByText("PTU pool · $100.00");
    await userEvent.click(screen.getByText("New pool"));
    await userEvent.type(screen.getByLabelText("Name"), "P");
    await userEvent.type(screen.getByLabelText("Members"), "x");
    await userEvent.selectOptions(screen.getByLabelText("Fixed cost that no call used"), "hold");
    await userEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(a.saveUsagePool).toHaveBeenCalledWith(
      { name: "P", match_type: "agent", values: ["x"], unattributed: "proportional", idle: "hold" }, undefined));
  });
});
