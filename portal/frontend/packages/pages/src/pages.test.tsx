import type { ApiClient, Me } from "@foundry-finops/api";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AdminPage, CostControlForm, FleetPage, PortalProvider, defaultPages } from "./index";

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
