import type { CostCentre } from "@foundry-finops/api";
import { Button, Card, Field, Modal, Notice, PageHead, Progress, Table, formatMoney } from "@foundry-finops/ui";
import { useState } from "react";
import { Notes, RangePicker, Section } from "./common";
import { useLoad, usePortal } from "./context";

const BASES = [
  { id: "project", label: "Project" },
  { id: "agent", label: "Agent" },
  { id: "deployment", label: "Deployment" },
  { id: "tag:team", label: "Tag: team" },
];

/** Cost centres: report your cost by who owns it, and manage the rules that decide that. */
export function CostCentresPage() {
  const { api, me } = usePortal();
  const [days, setDays] = useState(30);
  const [basis, setBasis] = useState("project");
  const report = useLoad(() => api.centreReport(basis, days), [basis, days]);
  const centres = useLoad(() => api.costCentres(), []);
  const [editing, setEditing] = useState<{ centre?: CostCentre } | null>(null);
  const [ruleFor, setRuleFor] = useState<CostCentre | null>(null);
  const [error, setError] = useState<string | null>(null);
  const admin = me.role === "admin" && !me.read_only;

  const reload = () => { report.reload(); centres.reload(); };
  const guard = async (fn: () => Promise<unknown>) => {
    setError(null);
    try { await fn(); reload(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  return (
    <>
      <PageHead
        title="Cost centres"
        description="Group projects, agents, deployments or tag values into cost centres, and see what each one costs against its budget."
        actions={
          <div className="ff-row">
            <select className="ff-select" aria-label="Allocation basis" value={basis} onChange={(e) => setBasis(e.target.value)}>
              {BASES.map((b) => <option key={b.id} value={b.id}>Allocate by {b.label}</option>)}
            </select>
            <RangePicker days={days} onChange={setDays} />
            {admin && <Button variant="primary" onClick={() => setEditing({})}>New cost centre</Button>}
          </div>
        }
      />
      {error && <Notice tone="bad">{error}</Notice>}
      <Section load={report}>
        {(r) => (
          <>
            <Card title={`Cost by cost centre · last ${r.days} days`}>
              <Table
                rows={r.centres}
                rowKey={(c) => c.id}
                initialSort={{ key: "cost", desc: true }}
                empty="No cost centre has a rule for this basis yet."
                columns={[
                  { key: "code", header: "Code", sortValue: (c) => c.code, render: (c) => <code>{c.code}</code> },
                  { key: "name", header: "Cost centre", sortValue: (c) => c.name, render: (c) => c.name },
                  { key: "cost", header: "Cost", numeric: true, sortValue: (c) => c.cost, render: (c) => formatMoney(c.cost, r.currency) },
                  { key: "budget", header: "Monthly budget", numeric: true, render: (c) => (c.monthly_budget == null ? "–" : formatMoney(c.monthly_budget, r.currency)) },
                  { key: "used", header: "Of budget", render: (c) => <Progress value={c.percent_of_budget} label={`${c.name} of budget`} /> },
                  { key: "from", header: "Made up of", render: (c) => c.values.map((v) => `${v.value}${v.share_pct < 100 ? ` (${v.share_pct}%)` : ""}`).join(", ") },
                ]}
              />
              {r.unallocated.length > 0 && (
                <Notice tone="warn">
                  <strong>Unallocated:</strong>{" "}
                  {r.unallocated.map((u) => `${u.value} ${formatMoney(u.cost, r.currency)}`).join(" · ")}. No cost centre has a rule for {r.unallocated.length === 1 ? "this" : "these"}.
                </Notice>
              )}
              <Notes items={[r.note]} />
            </Card>
          </>
        )}
      </Section>
      <div style={{ height: 14 }} />
      <Card title="Cost centres and their rules">
        <Section load={centres}>
          {(list) => (
            <Table
              rows={list}
              rowKey={(c) => c.id}
              empty="No cost centres yet."
              columns={[
                { key: "code", header: "Code", render: (c) => <code>{c.code}</code> },
                { key: "name", header: "Name", render: (c) => c.name },
                { key: "rules", header: "Rules", render: (c) => (
                  <>
                    {c.rules.map((rl) => (
                      <span key={rl.id} className="ff-badge" style={{ marginRight: 6 }}>
                        {rl.match_type}{rl.match_key ? `:${rl.match_key}` : ""} = {rl.match_value} · {rl.share_percent}%
                        {admin && <button aria-label={`Remove rule ${rl.match_value}`} style={{ border: 0, background: "none", cursor: "pointer", marginLeft: 4 }}
                          onClick={() => guard(() => api.deleteRule(rl.id))}>✕</button>}
                      </span>
                    ))}
                    {c.rules.length === 0 && <span className="ff-muted">None</span>}
                  </>
                ) },
                ...(admin ? [{ key: "act", header: "", render: (c: CostCentre) => (
                  <div className="ff-row">
                    <Button small onClick={() => setRuleFor(c)}>Add rule</Button>
                    <Button small onClick={() => setEditing({ centre: c })}>Edit</Button>
                    <Button small variant="danger" onClick={() => window.confirm(`Delete ${c.name}?`) && guard(() => api.deleteCostCentre(c.id))}>Delete</Button>
                  </div>
                ) }] : []),
              ]}
            />
          )}
        </Section>
      </Card>
      {editing && <CentreForm centre={editing.centre} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} />}
      {ruleFor && <RuleForm centre={ruleFor} onClose={() => setRuleFor(null)} onSaved={() => { setRuleFor(null); reload(); }} />}
    </>
  );
}

function CentreForm({ centre, onClose, onSaved }: { centre?: CostCentre; onClose: () => void; onSaved: () => void }) {
  const { api } = usePortal();
  const [code, setCode] = useState(centre?.code ?? "");
  const [name, setName] = useState(centre?.name ?? "");
  const [budget, setBudget] = useState(centre?.monthly_budget != null ? String(centre.monthly_budget) : "");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    try {
      await api.saveCostCentre({ code, name, monthly_budget: budget ? Number(budget) : null }, centre?.id);
      onSaved();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Modal title={centre ? `Edit ${centre.code}` : "New cost centre"} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={save}>Save</Button></>}>
      {error && <Notice tone="bad">{error}</Notice>}
      <Field label="Code">{(id) => <input id={id} className="ff-input" value={code} onChange={(e) => setCode(e.target.value)} />}</Field>
      <Field label="Name">{(id) => <input id={id} className="ff-input" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
      <Field label="Monthly budget (optional)" hint="Compared with the cost allocated to this cost centre.">{(id) => <input id={id} className="ff-input" inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} />}</Field>
      <Notice>A cost centre is a reporting label kept in this portal. It isn't written to Azure; to put a tag on Azure resources use Budgets &amp; tags.</Notice>
    </Modal>
  );
}

function RuleForm({ centre, onClose, onSaved }: { centre: CostCentre; onClose: () => void; onSaved: () => void }) {
  const { api } = usePortal();
  const [type, setType] = useState("project");
  const [key, setKey] = useState("team");
  const [value, setValue] = useState("");
  const [share, setShare] = useState("100");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    try {
      await api.addRule(centre.id, { match_type: type, match_value: value, match_key: type === "tag" ? key : "", share_percent: Number(share) });
      onSaved();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Modal title={`Add a rule to ${centre.code}`} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={save}>Add</Button></>}>
      {error && <Notice tone="bad">{error}</Notice>}
      <Field label="Match">
        {(id) => <select id={id} className="ff-select" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="project">A project</option><option value="agent">An agent</option><option value="deployment">A deployment</option><option value="tag">A tag value</option>
        </select>}
      </Field>
      {type === "tag" && <Field label="Tag name">{(id) => <input id={id} className="ff-input" value={key} onChange={(e) => setKey(e.target.value)} />}</Field>}
      <Field label="Value">{(id) => <input id={id} className="ff-input" value={value} onChange={(e) => setValue(e.target.value)} />}</Field>
      <Field label="Share of its cost (%)" hint="A value can be split across cost centres, up to 100% in total.">{(id) => <input id={id} className="ff-input" inputMode="decimal" value={share} onChange={(e) => setShare(e.target.value)} />}</Field>
    </Modal>
  );
}
