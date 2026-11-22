import type { DepartmentMapping, PoolSplit, SplitResult, UnattributedMode, UsagePool, UsageSplit } from "@foundry-finops/api";
import { Badge, Button, Card, Field, Modal, Notice, PageHead, Progress, Table, formatMoney, formatNumber, formatPct } from "@foundry-finops/ui";
import { useState } from "react";
import { Notes, RangePicker, Section } from "./common";
import { useLoad, usePortal } from "./context";

const MODES: { id: UnattributedMode; label: string }[] = [
  { id: "proportional", label: "Spread over the cost centres" },
  { id: "hold", label: "Show on its own row" },
];

/** Splits the cost of agents and models between cost centres by who used them. Administrators only. */
export function UsageSplitPage() {
  const { api, me } = usePortal();
  const [days, setDays] = useState(30);
  const [override, setOverride] = useState<UnattributedMode | "">("");
  const report = useLoad(() => api.usageSplit(days, override || undefined), [days, override]);
  const pools = useLoad(() => api.usagePools(), []);
  const mapping = useLoad(() => api.departmentMap(), []);
  const centres = useLoad(() => api.costCentres(), []);
  const [editing, setEditing] = useState<{ pool?: UsagePool } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const writable = !me.read_only;

  const reloadAll = () => { report.reload(); pools.reload(); mapping.reload(); };
  const guard = async (fn: () => Promise<unknown>) => {
    setError(null);
    try { await fn(); reloadAll(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <>
      <PageHead
        title="Usage split"
        description="Share the cost of agents and models between cost centres by who used them. Each person's department decides their cost centre."
        actions={
          <div className="ff-row">
            <select className="ff-select" aria-label="Usage with no cost centre" value={override} onChange={(e) => setOverride(e.target.value as UnattributedMode | "")}>
              <option value="">Unattributed usage: each pool's setting</option>
              {MODES.map((m) => <option key={m.id} value={m.id}>Unattributed usage: {m.label.toLowerCase()}</option>)}
            </select>
            <RangePicker days={days} onChange={setDays} />
          </div>
        }
      />
      {error && <Notice tone="bad">{error}</Notice>}
      <Section load={report}>{(r) => <Report r={r} />}</Section>

      <div style={{ height: 14 }} />
      <Card title="Pools" actions={writable ? <Button small variant="primary" onClick={() => setEditing({})}>New pool</Button> : undefined}>
        <Section load={pools}>
          {(list) => (
            <Table
              rows={list}
              rowKey={(p) => p.id}
              empty="No pools. Without a pool, the cost of the whole Foundry resource is split."
              columns={[
                { key: "name", header: "Pool", render: (p) => p.name },
                { key: "basis", header: "Made of", render: (p) => (p.match_type === "agent" ? "Agents" : "Deployments") },
                { key: "values", header: "Members", render: (p) => p.values.join(", ") },
                { key: "mode", header: "Unattributed usage", render: (p) => MODES.find((m) => m.id === p.unattributed)?.label ?? p.unattributed },
                ...(writable ? [{ key: "act", header: "", render: (p: UsagePool) => (
                  <div className="ff-row">
                    <Button small onClick={() => setEditing({ pool: p })}>Edit</Button>
                    <Button small variant="danger" onClick={() => window.confirm(`Delete the pool ${p.name}?`) && guard(() => api.deleteUsagePool(p.id))}>Delete</Button>
                  </div>
                ) }] : []),
              ]}
            />
          )}
        </Section>
        <Notes items={["All pools use the same basis (agents or deployments) and a member is in one pool only, so cost is never shared out twice."]} />
      </Card>

      <div style={{ height: 14 }} />
      <Card title="Departments">
        <Section load={mapping}>
          {(list) => (
            <>
              <Table
                rows={list}
                rowKey={(m) => m.department}
                empty="No department is mapped to a cost centre yet, so all usage is unattributed."
                columns={[
                  { key: "d", header: "Department (from the person's Entra profile)", render: (m) => m.department },
                  { key: "c", header: "Cost centre", render: (m) => <code>{m.cost_centre_code}</code> },
                  ...(writable ? [{ key: "act", header: "", render: (m: DepartmentMapping) => (
                    <Button small variant="danger" onClick={() => guard(() => api.deleteDepartmentMap(m.department))}>Remove</Button>
                  ) }] : []),
                ]}
              />
              {writable && <MapDepartment centres={centres.data ?? []} onAdd={(d, id) => guard(() => api.setDepartmentMap(d, id))} />}
            </>
          )}
        </Section>
      </Card>
      {editing && <PoolForm pool={editing.pool} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reloadAll(); }} />}
    </>
  );
}

function Report({ r }: { r: UsageSplit }) {
  const money = (v: number) => formatMoney(v, r.currency);
  // The usage of a department with no cost centre is in the unit it was weighted by: estimated cost or tokens.
  const usage = (v: number) => (r.measure === "cost" ? money(v) : `${formatNumber(v)} tokens`);
  return (
    <>
      {r.pools.length === 0 && r.rows ? (
        <Card title={`The whole Foundry resource · last ${r.days} days · ${money(r.total ?? 0)}`}>
          <SplitTable result={r as SplitResult} unattributedCode={r.unattributed_code} money={money} usage={usage} />
        </Card>
      ) : (
        <>
          <Card title={`Totals by cost centre · last ${r.days} days`}>
            <Table
              rows={r.totals ?? []}
              rowKey={(t) => t.code}
              empty="Nothing was allocated."
              columns={[
                { key: "c", header: "Cost centre", render: (t) => (t.code === r.unattributed_code ? <Badge tone="warn">Unattributed</Badge> : <><code>{t.code}</code> {t.name}</>) },
                { key: "a", header: "Allocated", numeric: true, render: (t) => money(t.allocated) },
              ]}
            />
            <p className="ff-muted">
              In pools: {money(r.pooled_cost ?? 0)}. Outside every pool: {money(r.unpooled_cost ?? 0)}, not split here (the cost centre rules apply to it).
            </p>
          </Card>
          {r.pools.map((p: PoolSplit) => (
            <div key={p.id} style={{ marginTop: 14 }}>
              <Card title={`${p.name} · ${money(p.total)}`}>
                {!p.matched_usage && (
                  <Notice tone="warn">
                    No usage record names any of this pool's {p.match_type === "agent" ? "agents" : "deployments"}, so all of its cost is unattributed. Pool members must match the billing name exactly.
                  </Notice>
                )}
                <SplitTable result={p} unattributedCode={r.unattributed_code} money={money} usage={usage} />
              </Card>
            </div>
          ))}
        </>
      )}
      <Notes items={[r.note]} />
    </>
  );
}

function SplitTable({ result, unattributedCode, money, usage }: { result: SplitResult; unattributedCode: string; money: (v: number) => string; usage: (v: number) => string }) {
  return (
    <>
      <Table
        rows={result.rows}
        rowKey={(x) => x.code}
        empty="Nothing to allocate in this period."
        columns={[
          { key: "c", header: "Cost centre", render: (x) => (x.code === unattributedCode ? <Badge tone="warn">Unattributed</Badge> : <><code>{x.code}</code> {x.name}</>) },
          { key: "a", header: "Allocated", numeric: true, render: (x) => money(x.allocated) },
          { key: "s", header: "Share", numeric: true, render: (x) => formatPct(x.share_percent) },
        ]}
      />
      <div style={{ marginTop: 10 }}>
        <div className="ff-muted">Usage with a known cost centre</div>
        {/* Coverage isn't a budget: never shown as a warning or an error colour. */}
        <Progress value={result.coverage_percent} warnAt={101} badAt={102} label="Usage with a known cost centre" />
      </div>
      {result.unmapped_departments.length > 0 && (
        <Notice tone="warn">
          <strong>No cost centre for:</strong>{" "}
          {result.unmapped_departments.map((u) => `${u.department} (${usage(u.usage)})`).join(" · ")}. Map these departments below to attribute their usage.
        </Notice>
      )}
    </>
  );
}

function MapDepartment({ centres, onAdd }: { centres: { id: number; code: string; name: string }[]; onAdd: (department: string, costCentreId: number) => void }) {
  const [department, setDepartment] = useState("");
  const [centre, setCentre] = useState("");
  return (
    <div className="ff-row" style={{ marginTop: 10 }}>
      <input className="ff-input" aria-label="Department" placeholder="Department, for example Finance" value={department} onChange={(e) => setDepartment(e.target.value)} />
      <select className="ff-select" aria-label="Cost centre for the department" value={centre} onChange={(e) => setCentre(e.target.value)}>
        <option value="">Cost centre…</option>
        {centres.map((c) => <option key={c.id} value={c.id}>{c.code} · {c.name}</option>)}
      </select>
      <Button variant="primary" disabled={!department.trim() || !centre} onClick={() => { onAdd(department.trim(), Number(centre)); setDepartment(""); }}>Map department</Button>
    </div>
  );
}

function PoolForm({ pool, onClose, onSaved }: { pool?: UsagePool; onClose: () => void; onSaved: () => void }) {
  const { api } = usePortal();
  const [name, setName] = useState(pool?.name ?? "");
  const [type, setType] = useState<UsagePool["match_type"]>(pool?.match_type ?? "agent");
  const [values, setValues] = useState(pool?.values.join("\n") ?? "");
  const [mode, setMode] = useState<UnattributedMode>(pool?.unattributed ?? "proportional");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    const list = values.split(/[\n,]/).map((v) => v.trim()).filter(Boolean);
    if (!name.trim()) return setError("Give the pool a name.");
    if (list.length === 0) return setError(`Add at least one ${type}.`);
    try {
      await api.saveUsagePool({ name: name.trim(), match_type: type, values: list, unattributed: mode }, pool?.id);
      onSaved();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Modal title={pool ? `Edit ${pool.name}` : "New pool"} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={save}>Save</Button></>}>
      {error && <Notice tone="bad">{error}</Notice>}
      <Field label="Name">{(id) => <input id={id} className="ff-input" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
      <Field label="Made of">
        {(id) => <select id={id} className="ff-select" value={type} onChange={(e) => setType(e.target.value as UsagePool["match_type"])}>
          <option value="agent">Agents</option><option value="deployment">Deployments (models)</option>
        </select>}
      </Field>
      <Field label="Members" hint="One per line, named exactly as in the billing data. Agents are matched by the agent.name tag.">
        {(id) => <textarea id={id} className="ff-input" rows={5} value={values} onChange={(e) => setValues(e.target.value)} />}
      </Field>
      <Field label="Usage with no known cost centre">
        {(id) => <select id={id} className="ff-select" value={mode} onChange={(e) => setMode(e.target.value as UnattributedMode)}>
          {MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select>}
      </Field>
    </Modal>
  );
}
