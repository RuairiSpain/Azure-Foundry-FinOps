import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError, DemoAuth, fetchConfig } from "./index";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const store = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) } as unknown as Storage;
};

describe("ApiClient", () => {
  it("sends the auth headers and builds the query", async () => {
    const auth = new DemoAuth([], store());
    await auth.signIn("oid-1");
    const f = vi.fn().mockResolvedValue(json({ ok: true }));
    const api = new ApiClient({ auth, fetchImpl: f as unknown as typeof fetch, baseUrl: "http://x/" });
    await api.costTable("agent", 7);
    const [url, init] = f.mock.calls[0]!;
    expect(url).toBe("http://x/api/cost/table?group_by=agent&days=7");
    expect(init.headers["X-Demo-User"]).toBe("oid-1");
    expect(init.method).toBe("GET");
  });

  it("maps an error response to ApiError with the service's message", async () => {
    const auth = new DemoAuth([], store());
    await auth.signIn("o");
    const f = vi.fn().mockResolvedValue(json({ detail: "Your portal access doesn't include changing this." }, 403));
    const api = new ApiClient({ auth, fetchImpl: f as unknown as typeof fetch });
    await expect(api.saveCostControl({ name: "x" })).rejects.toMatchObject({ status: 403, message: "Your portal access doesn't include changing this." });
    await expect(api.saveCostControl({ name: "x" })).rejects.toBeInstanceOf(ApiError);
  });

  it("joins validation messages and tells the app on a 401", async () => {
    const auth = new DemoAuth([], store());
    await auth.signIn("o");
    auth.onSignedOut = vi.fn();
    const f = vi.fn().mockResolvedValueOnce(json({ detail: [{ msg: "bad a" }, { msg: "bad b" }] }, 422)).mockResolvedValueOnce(json({ detail: "Sign in" }, 401));
    const api = new ApiClient({ auth, fetchImpl: f as unknown as typeof fetch });
    await expect(api.me()).rejects.toThrow("bad a; bad b");
    await expect(api.me()).rejects.toThrow("Sign in");
    expect(auth.onSignedOut).toHaveBeenCalledOnce();
  });

  it("refuses to call without a signed-in person, and encodes names in paths", async () => {
    const api = new ApiClient({ auth: new DemoAuth([], store()), fetchImpl: vi.fn() as unknown as typeof fetch });
    await expect(api.me()).rejects.toThrow("Pick a demo user");
    const auth = new DemoAuth([], store());
    await auth.signIn("o");
    const f = vi.fn().mockResolvedValue(json({ deleted: "a b" }));
    await new ApiClient({ auth, fetchImpl: f as unknown as typeof fetch }).deleteCostControl("a b/c", "p 1");
    expect(f.mock.calls[0]![0]).toBe("/api/cost-controls/a%20b%2Fc?project=p+1");
  });

  it("loads the public configuration without signing in", async () => {
    const f = vi.fn().mockResolvedValue(json({ mode: "demo", brand: "B", read_only: false }));
    expect((await fetchConfig("", f as unknown as typeof fetch)).brand).toBe("B");
    await expect(fetchConfig("", vi.fn().mockResolvedValue(json({}, 500)) as unknown as typeof fetch)).rejects.toBeInstanceOf(ApiError);
  });
});

describe("DemoAuth", () => {
  it("remembers who signed in and forgets on sign out", async () => {
    const s = store();
    const auth = new DemoAuth([{ oid: "o1", name: "Alice", upn: "a@x" }], s);
    expect(auth.isSignedIn()).toBe(false);
    await auth.signIn("o1");
    expect(auth.account()).toEqual({ name: "Alice", upn: "a@x" });
    await auth.signOut();
    expect(auth.isSignedIn()).toBe(false);
    await expect(auth.signIn()).rejects.toThrow();
  });
});

describe("usage split calls", () => {
  it("builds the queries and bodies", async () => {
    const auth = new DemoAuth([], store());
    await auth.signIn("o");
    const f = vi.fn().mockImplementation(() => Promise.resolve(json({})));
    const api = new ApiClient({ auth, fetchImpl: f as unknown as typeof fetch });
    await api.usageSplit(7);
    await api.usageSplit(30, "hold", 3);
    await api.saveUsagePool({ name: "p", match_type: "agent", values: ["a"], unattributed: "hold" });
    await api.saveUsagePool({ name: "p", match_type: "agent", values: ["a"], unattributed: "hold" }, 4);
    await api.setDepartmentMap("Risk & Fraud", 2);
    await api.deleteDepartmentMap("Risk & Fraud");
    const calls = f.mock.calls.map(([url, init]) => `${init.method} ${url}`);
    expect(calls).toEqual([
      "GET /api/cost-centres/usage-split?days=7",
      "GET /api/cost-centres/usage-split?days=30&unattributed=hold&pool=3",
      "POST /api/usage-pools",
      "PUT /api/usage-pools/4",
      "PUT /api/cost-centres/department-map",
      "DELETE /api/cost-centres/department-map?department=Risk+%26+Fraud",
    ]);
    expect(JSON.parse(f.mock.calls[4]![1].body)).toEqual({ department: "Risk & Fraud", cost_centre_id: 2 });
  });
});
