import type { Grant, PortalUser, Role, ScopeType } from "@foundry-finops/api";
import { Badge, Button, Card, Field, Modal, Notice, PageHead, Table, Tabs, formatDateTime } from "@foundry-finops/ui";
import { useState } from "react";
import { Section } from "./common";
import { useLoad, usePortal } from "./context";

type Tab = "users" | "audit";
type GrantDraft = Omit<Grant, "id">;
const SCOPES: { id: ScopeType; label: string }[] = [
  { id: "project", label: "A project" },
  { id: "cost_centre", label: "A cost centre" },
  { id: "account", label: "The Foundry resource" },
  { id: "resource_group", label: "The resource group" },
  { id: "subscription", label: "The subscription" },
  { id: "all", label: "Everything" },
];
const grantText = (g: Grant) => `${g.scope_type === "all" ? "Everything" : `${g.scope_type.replace("_", " ")} ${g.scope_value}`}${g.can_write ? " (can change)" : ""}`;

/** Admin: who may use the portal, what they can see and change, and what happened. */
export function AdminPage() {
  const { me } = usePortal();
  // Check the role before anything loads, so a non-administrator never calls the admin API.
  if (me.role !== "admin") return <><PageHead title="Admin" /><Notice tone="warn">Portal administrators only.</Notice></>;
  return <AdminContent />;
}

function AdminContent() {
  const { api, me } = usePortal();
  const [tab, setTab] = useState<Tab>("users");
  const users = useLoad(() => api.users(), []);
  const audit = useLoad(() => api.audit(200), []);
  const [form, setForm] = useState<{ user?: PortalUser } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try { await fn(); users.reload(); audit.reload(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <>
      <PageHead title="Admin" description="People must already exist in Microsoft Entra ID. What a person sees is the narrower of their portal access here and their Azure permissions."
        actions={tab === "users" && !me.read_only ? <Button variant="primary" onClick={() => setForm({})}>Add a person</Button> : undefined} />
      {error && <Notice tone="bad">{error}</Notice>}
      <Tabs<Tab> tabs={[{ id: "users", label: "People" }, { id: "audit", label: "Audit log" }]} value={tab} onChange={setTab} />
      {tab === "users" ? (
        <Card>
          <Section load={users}>
            {(list) => (
              <Table rows={list} rowKey={(u) => u.oid} empty="Nobody has been added."
                columns={[
                  { key: "n", header: "Person", sortValue: (u) => u.display_name, render: (u) => <><strong>{u.display_name || u.upn}</strong><div className="ff-muted">{u.upn}</div></> },
                  { key: "r", header: "Role", sortValue: (u) => u.role, render: (u) => <Badge tone={u.role === "admin" ? "accent" : "neutral"}>{u.role}</Badge> },
                  { key: "g", header: "Access", render: (u) => u.role === "admin" ? "Everything" : u.grants.length ? u.grants.map(grantText).join("; ") : <span className="ff-muted">None yet</span> },
                  { key: "e", header: "Status", render: (u) => <Badge tone={u.enabled ? "ok" : "bad"}>{u.enabled ? "Active" : "Disabled"}</Badge> },
                  { key: "a", header: "", render: (u) => (
                    <div className="ff-row">
                      <Button small onClick={() => setForm({ user: u })}>Edit</Button>
                      <Button small onClick={() => act(() => api.updateUser(u.oid, { enabled: !u.enabled }))}>{u.enabled ? "Disable" : "Enable"}</Button>
                      <Button small variant="danger" disabled={u.oid === me.oid} onClick={() => window.confirm(`Remove ${u.upn}?`) && act(() => api.removeUser(u.oid))}>Remove</Button>
                    </div>
                  ) },
                ]} />
            )}
          </Section>
        </Card>
      ) : (
        <Card>
          <Section load={audit}>
            {(list) => (
              <Table rows={list} rowKey={(a) => a.id} empty="Nothing logged yet."
                columns={[
                  { key: "t", header: "When", render: (a) => formatDateTime(a.at) },
                  { key: "w", header: "Who", render: (a) => a.actor_upn || a.actor_oid },
                  { key: "a", header: "Action", render: (a) => <code>{a.action}</code> },
                  { key: "x", header: "Target", render: (a) => (a.target.length > 50 ? `…${a.target.slice(-50)}` : a.target) },
                  { key: "o", header: "Outcome", render: (a) => <Badge tone={a.outcome === "ok" ? "ok" : a.outcome === "denied" ? "warn" : "bad"}>{a.outcome}</Badge> },
                  { key: "d", header: "Detail", render: (a) => <span className="ff-muted">{Object.keys(a.detail).length ? JSON.stringify(a.detail).slice(0, 90) : ""}</span> },
                ]} />
            )}
          </Section>
        </Card>
      )}
      {form && <UserForm user={form.user} onClose={() => setForm(null)} onSaved={() => { setForm(null); users.reload(); audit.reload(); }} />}
    </>
  );
}

function UserForm({ user, onClose, onSaved }: { user?: PortalUser; onClose: () => void; onSaved: () => void }) {
  const { api } = usePortal();
  const [identifier, setIdentifier] = useState("");
  const [found, setFound] = useState<{ upn: string; display_name: string } | null>(null);
  const [role, setRole] = useState<Role>(user?.role ?? "viewer");
  const [grants, setGrants] = useState<GrantDraft[]>(user?.grants.map(({ scope_type, scope_value, can_write }) => ({ scope_type, scope_value, can_write })) ?? []);
  const [error, setError] = useState<string | null>(null);

  const lookup = async () => {
    setError(null); setFound(null);
    try { setFound(await api.lookupUser(identifier.trim())); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  const save = async () => {
    try {
      if (user) {
        await api.updateUser(user.oid, { role });
        await api.setGrants(user.oid, grants);
      } else {
        await api.addUser(identifier.trim(), role, grants);
      }
      onSaved();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  const update = (i: number, patch: Partial<GrantDraft>) => setGrants((g) => g.map((x, k) => (k === i ? { ...x, ...patch } : x)));

  return (
    <Modal title={user ? `Edit ${user.upn}` : "Add a person"} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={save} disabled={!user && !found}>Save</Button></>}>
      {error && <Notice tone="bad">{error}</Notice>}
      {!user && (
        <Field label="Email (user principal name) or object ID" hint="Checked against Microsoft Entra ID before the person is added.">
          {(id) => (
            <div className="ff-row">
              <input id={id} className="ff-input" style={{ flex: 1 }} value={identifier} onChange={(e) => { setIdentifier(e.target.value); setFound(null); }} />
              <Button onClick={lookup}>Check</Button>
            </div>
          )}
        </Field>
      )}
      {found && <Notice><strong>{found.display_name}</strong> · {found.upn} exists in Entra ID.</Notice>}
      <Field label="Role" hint="Administrators manage people and see everything. Owners can change what their grants allow. Viewers only look.">
        {(id) => <select id={id} className="ff-select" value={role} onChange={(e) => setRole(e.target.value as Role)}>
          <option value="viewer">Viewer</option><option value="owner">Owner</option><option value="admin">Administrator</option>
        </select>}
      </Field>
      {role !== "admin" && (
        <>
          <h3 style={{ margin: "6px 0 8px" }}>Access</h3>
          {grants.map((g, i) => (
            <div key={i} className="ff-row" style={{ marginBottom: 8 }}>
              <select className="ff-select" aria-label="Scope type" value={g.scope_type} onChange={(e) => update(i, { scope_type: e.target.value as ScopeType })}>
                {SCOPES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
              {g.scope_type !== "all" && <input className="ff-input" aria-label="Scope value" placeholder="name or code" value={g.scope_value} onChange={(e) => update(i, { scope_value: e.target.value })} />}
              {role === "owner" && <label><input type="checkbox" checked={g.can_write} onChange={(e) => update(i, { can_write: e.target.checked })} /> can change</label>}
              <Button small variant="danger" onClick={() => setGrants((x) => x.filter((_, k) => k !== i))}>✕</Button>
            </div>
          ))}
          <Button small onClick={() => setGrants((g) => [...g, { scope_type: "project", scope_value: "", can_write: false }])}>Add access</Button>
        </>
      )}
    </Modal>
  );
}
