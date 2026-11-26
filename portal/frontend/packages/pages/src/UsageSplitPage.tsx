import type { AgentAlias, AllocationRules, DepartmentMapping, IdleMode, PoolSplit, Reconciliation, SplitResult, UnattributedMode, UsagePool, UsageSplit } from "@foundry-finops/api";
import { Badge, Button, Card, Field, Modal, Notice, PageHead, Progress, Table, formatMoney, formatNumber, formatPct } from "@foundry-finops/ui";
import { useState } from "react";
import { Notes, RangePicker, Section } from "./common";
import { useLoad, usePortal } from "./context";

const IDLE_CODE = "(idle capacity)";
const IDLE_MODES: { id: IdleMode; label: string }[] = [
  { id: "spread", label: "Spread over the cost centres" },
  { id: "hold", label: "Show on its own row (for provisioned PTU deployments)" },
];
const STATUS: Record<Reconciliation["rows"][number]["status"], { tone: "ok" | "warn" | "bad" | "neutral"; label: string }> = {
  ok: { tone: "ok", label: "Matches" },
  under: { tone: "warn", label: "Records hold less" },
  over: { tone: "warn", label: "Records hold more" },
  none_recorded: { tone: "bad", label: "Nothing recorded" },
  not_in_monitor: { tone: "neutral", label: "Not in Azure Monitor" },
};

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
  const recon = useLoad(() => api.usageReconciliation(days), [days]);
  const aliases = useLoad(() => api.agentAliases(), []);
  const [editing, setEditing] = useState<{ pool?: UsagePool } | null>(null);
  const [aliasFor, setAliasFor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const writable = !me.read_only;

  const reloadAll = () => { report.reload(); pools.reload(); mapping.reload(); aliases.reload(); };
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
      <Section load={report}>{(r) => <Report r={r} onMap={writable ? setAliasFor : undefined} />}</Section>

      <div style={{ height: 14 }} />
      <Card title="Does the usage data cover everything Azure counted?">
        <Section load={recon}>{(r) => <ReconciliationView r={r} />}</Section>
      </Card>

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
                { key: "idle", header: "Unused fixed cost", render: (p) => (p.idle === "hold" ? "On its own row" : "Spread") },
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

      <div style={{ height: 14 }} />
      <Card title="Agent names">
        <Section load={aliases}>
          {(list) => (
            <Table
              rows={list}
              rowKey={(a) => a.alias}
              empty="No agent names mapped. Usage records often name an agent by its client ID, while billing uses the agent.name tag."
              columns={[
                { key: "a", header: "Named in usage records", render: (a) => <code>{a.alias}</code> },
                { key: "b", header: "Billing agent name", render: (a) => a.agent },
                ...(writable ? [{ key: "act", header: "", render: (a: AgentAlias) => (
                  <Button small variant="danger" onClick={() => guard(() => api.deleteAgentAlias(a.alias))}>Remove</Button>
                ) }] : []),
              ]}
            />
          )}
        </Section>
        {writable && <div style={{ marginTop: 10 }}><Button onClick={() => setAliasFor("")}>Map an agent name</Button></div>}
      </Card>

      <div style={{ height: 14 }} />
      <AllocationRulesCard days={days} />
      {aliasFor !== null && <AliasForm initial={aliasFor} onClose={() => setAliasFor(null)} onSaved={() => { setAliasFor(null); reloadAll(); }} />}
      {editing && <PoolForm pool={editing.pool} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reloadAll(); }} />}
    </>
  );
}

function Report({ r, onMap }: { r: UsageSplit; onMap?: (agent: string) => void }) {
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
                <SplitTable result={p} unattributedCode={r.unattributed_code} money={money} usage={usage} idle={p.idle_cost} note={p.idle_note} />
              </Card>
            </div>
          ))}
        </>
      )}
      {r.unmatched_agents.length > 0 && (
        <Notice tone="warn">
          <strong>Agent names in the usage records that no billing agent has:</strong>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {r.unmatched_agents.map((u) => (
              <li key={u.agent}>
                <code>{u.agent}</code> ({usage(u.usage)}){" "}
                {onMap && <Button small onClick={() => onMap(u.agent)}>Map to a billing agent</Button>}
              </li>
            ))}
          </ul>
        </Notice>
      )}
      <Notes items={[r.note]} />
    </>
  );
}

function SplitTable({ result, unattributedCode, money, usage, idle, note }: { result: SplitResult; unattributedCode: string; money: (v: number) => string; usage: (v: number) => string; idle?: number | null; note?: string }) {
  return (
    <>
      <Table
        rows={result.rows}
        rowKey={(x) => x.code}
        empty="Nothing to allocate in this period."
        columns={[
          { key: "c", header: "Cost centre", render: (x) => (x.code === unattributedCode ? <Badge tone="warn">Unattributed</Badge> : x.code === IDLE_CODE ? <Badge>Idle capacity</Badge> : <><code>{x.code}</code> {x.name}</>) },
          { key: "a", header: "Allocated", numeric: true, render: (x) => money(x.allocated) },
          { key: "s", header: "Share", numeric: true, render: (x) => formatPct(x.share_percent) },
        ]}
      />
      {idle !== undefined && idle !== null && idle > 0 && !result.rows.some((x) => x.code === IDLE_CODE) && (
        <p className="ff-muted">
          Of this, {money(idle)} is fixed cost that the recorded calls did not use. It is spread over the cost centres; set the pool to show it on its own row.
        </p>
      )}
      {note && <Notice tone="warn">{note}</Notice>}
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
  const [idle, setIdle] = useState<IdleMode>(pool?.idle ?? "spread");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    const list = values.split(/[\n,]/).map((v) => v.trim()).filter(Boolean);
    if (!name.trim()) return setError("Give the pool a name.");
    if (list.length === 0) return setError(`Add at least one ${type}.`);
    try {
      await api.saveUsagePool({ name: name.trim(), match_type: type, values: list, unattributed: mode, idle }, pool?.id);
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
      <Field label="Fixed cost that no call used" hint="A provisioned (PTU) deployment costs the same whatever is used. Needs usage records with an estimated cost, not tokens only.">
        {(id) => <select id={id} className="ff-select" value={idle} onChange={(e) => setIdle(e.target.value as IdleMode)}>
          {IDLE_MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select>}
      </Field>
    </Modal>
  );
}

function ReconciliationView({ r }: { r: Reconciliation }) {
  return (
    <>
      <p>
        {r.coverage_percent === null ? "Azure Monitor reported no tokens for this period." : (
          <>Your usage records hold <strong>{formatPct(r.coverage_percent)}</strong> of the {formatNumber(r.azure_total)} tokens Azure Monitor counted on the deployments both sides know about. {formatNumber(r.unrecorded)} tokens are not accounted for by any record, so the split can't see who used them.</>
        )}
      </p>
      <Table
        rows={r.rows}
        rowKey={(x) => x.name}
        empty="No deployments to compare."
        columns={[
          { key: "n", header: "Deployment", render: (x) => x.name },
          { key: "r", header: "In your records", numeric: true, render: (x) => formatNumber(x.recorded) },
          { key: "a", header: "Azure Monitor", numeric: true, render: (x) => (x.azure === null ? "–" : formatNumber(x.azure)) },
          { key: "c", header: "Covered", numeric: true, render: (x) => (x.coverage_percent === null ? "–" : formatPct(x.coverage_percent)) },
          { key: "s", header: "Status", render: (x) => <Badge tone={STATUS[x.status].tone}>{STATUS[x.status].label}</Badge> },
        ]}
      />
      <Notes items={[r.note]} />
    </>
  );
}

function AliasForm({ initial, onClose, onSaved }: { initial: string; onClose: () => void; onSaved: () => void }) {
  const { api } = usePortal();
  const [alias, setAlias] = useState(initial);
  const [agent, setAgent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    if (!alias.trim() || !agent.trim()) return setError("Enter both names.");
    try { await api.setAgentAlias(alias.trim(), agent.trim()); onSaved(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Modal title="Map an agent name" onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={save}>Save</Button></>}>
      {error && <Notice tone="bad">{error}</Notice>}
      <Field label="Named in the usage records" hint="Usually the calling application's client ID.">{(id) => <input id={id} className="ff-input" value={alias} onChange={(e) => setAlias(e.target.value)} />}</Field>
      <Field label="Billing agent name" hint="The value of the agent.name tag in billing.">{(id) => <input id={id} className="ff-input" value={agent} onChange={(e) => setAgent(e.target.value)} />}</Field>
    </Modal>
  );
}

/** Builds Azure cost allocation rule definitions from the current split, for review. Nothing is sent to Azure. */
function AllocationRulesCard({ days }: { days: number }) {
  const { api } = usePortal();
  const [tag, setTag] = useState("cost-centre");
  const [out, setOut] = useState<AllocationRules | null>(null);
  const [error, setError] = useState<string | null>(null);
  const build = async () => {
    setError(null);
    try { setOut(await api.allocationRules(days, tag.trim() || undefined)); } catch (e) { setOut(null); setError(e instanceof Error ? e.message : String(e)); }
  };
  const download = () => {
    if (!out) return;
    const blob = new Blob([JSON.stringify(out.rules.map((r) => ({ name: r.name, ...r.definition })), null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "cost-allocation-rules.json";
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <Card title="Azure Cost Management allocation rules (for review)">
      <p className="ff-muted">Build rule definitions from this period's split. Nothing is sent to Azure: you review them and send them yourself.</p>
      <div className="ff-row">
        <input className="ff-input" aria-label="Cost centre tag name" value={tag} onChange={(e) => setTag(e.target.value)} />
        <Button onClick={build}>Build rule definitions</Button>
        {out && out.rules.length > 0 && <Button onClick={download}>Download JSON</Button>}
      </div>
      {error && <Notice tone="bad">{error}</Notice>}
      {out && (
        <>
          {out.rules.map((r) => (
            <details key={r.name} style={{ marginTop: 10 }}>
              <summary><code>{r.name}</code> · {r.pool}{r.excluded_percent > 0 ? ` · ${formatPct(r.excluded_percent)} left out` : ""}</summary>
              <pre style={{ overflow: "auto" }}>{JSON.stringify(r.definition, null, 2)}</pre>
            </details>
          ))}
          {out.rules.length === 0 && <Notice>No rule could be built.</Notice>}
          {out.skipped.map((k) => <Notice key={k.pool} tone="warn"><strong>{k.pool}:</strong> {k.reason}</Notice>)}
          <Notes items={out.notes} />
        </>
      )}
    </Card>
  );
}
