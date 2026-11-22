import type { Budget, TagRow } from "@foundry-finops/api";
import { Button, Card, Field, Modal, Notice, PageHead, Progress, Table, Tabs, formatDate, formatMoney } from "@foundry-finops/ui";
import { useState } from "react";
import { Section } from "./common";
import { useLoad, usePortal } from "./context";

type Tab = "budgets" | "tags";

/** Cost Management budgets (they alert on the bill, they can't block) and the tags on your Azure resources. */
export function BudgetsTagsPage() {
  const { api, me } = usePortal();
  const [tab, setTab] = useState<Tab>("budgets");
  const budgets = useLoad(() => api.budgets(), []);
  const tags = useLoad(() => api.tags(), []);
  const [editTags, setEditTags] = useState<TagRow | null>(null);
  const [budgetForm, setBudgetForm] = useState<{ existing?: Budget } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canWrite = me.role !== "viewer" && !me.read_only;

  return (
    <>
      <PageHead title="Budgets & tags" description="Azure Cost Management budgets alert on cost; tags let Cost Management group it. Both apply to the whole resource group." />
      {error && <Notice tone="bad">{error}</Notice>}
      <Tabs<Tab> tabs={[{ id: "budgets", label: "Budgets" }, { id: "tags", label: "Tags" }]} value={tab} onChange={setTab} />
      {tab === "budgets" ? (
        <Card title="Cost Management budgets" actions={canWrite ? <Button variant="primary" onClick={() => setBudgetForm({})}>Create</Button> : undefined}>
          <Section load={budgets}>
            {(list) => (
              <Table rows={list} rowKey={(b) => b.name} empty="No budgets."
                columns={[
                  { key: "n", header: "Name", render: (b) => <strong>{b.name}</strong> },
                  { key: "a", header: "Amount", numeric: true, render: (b) => `${formatMoney(b.amount, b.currency ?? "USD")} / ${b.time_grain.toLowerCase()}` },
                  { key: "s", header: "Spend so far", numeric: true, render: (b) => formatMoney(b.current_spend, b.currency ?? "USD") },
                  { key: "f", header: "Forecast", numeric: true, render: (b) => formatMoney(b.forecast_spend, b.currency ?? "USD") },
                  { key: "u", header: "Used", render: (b) => <Progress value={b.percent_used} label={`${b.name} used`} /> },
                  { key: "p", header: "Period", render: (b) => `${formatDate(b.start_date)} – ${formatDate(b.end_date)}` },
                  ...(canWrite ? [{ key: "x", header: "", render: (b: Budget) => (
                    <div className="ff-row">
                      <Button small onClick={() => setBudgetForm({ existing: b })}>Edit</Button>
                      <Button small variant="danger" onClick={async () => {
                        if (!window.confirm(`Delete ${b.name}?`)) return;
                        try { await api.deleteBudget(b.name); budgets.reload(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
                      }}>Delete</Button>
                    </div>
                  ) }] : []),
                ]} />
            )}
          </Section>
        </Card>
      ) : (
        <Card title="Tags on the resource group, Foundry resource and projects">
          <Section load={tags}>
            {(list) => (
              <Table rows={list} rowKey={(t) => t.scope} empty="No tagged resources."
                columns={[
                  { key: "n", header: "Name", render: (t) => t.name },
                  { key: "t", header: "Type", render: (t) => t.type },
                  { key: "g", header: "Tags", render: (t) => Object.entries(t.tags).map(([k, v]) => <span key={k} className="ff-badge" style={{ marginRight: 6 }}>{k}={v}</span>) },
                  ...(canWrite ? [{ key: "e", header: "", render: (t: TagRow) => <Button small onClick={() => setEditTags(t)}>Edit</Button> }] : []),
                ]} />
            )}
          </Section>
        </Card>
      )}
      {editTags && <TagsForm row={editTags} onClose={() => setEditTags(null)} onSaved={() => { setEditTags(null); tags.reload(); }} />}
      {budgetForm && <BudgetForm existing={budgetForm.existing} onClose={() => setBudgetForm(null)} onSaved={() => { setBudgetForm(null); budgets.reload(); }} />}
    </>
  );
}

function TagsForm({ row, onClose, onSaved }: { row: TagRow; onClose: () => void; onSaved: () => void }) {
  const { api } = usePortal();
  const [text, setText] = useState(Object.entries(row.tags).map(([k, v]) => `${k}=${v}`).join("\n"));
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    const tags: Record<string, string> = {};
    for (const line of text.split("\n").map((l) => l.trim()).filter(Boolean)) {
      const i = line.indexOf("=");
      if (i < 1) return setError(`"${line}" isn't key=value`);
      tags[line.slice(0, i).trim()] = line.slice(i + 1).trim();
    }
    try { await api.setTags(row.scope, tags); onSaved(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Modal title={`Tags on ${row.name}`} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={save}>Save</Button></>}>
      {error && <Notice tone="bad">{error}</Notice>}
      <Notice tone="warn">Saving replaces all tags on this resource. Cost Management uses tags such as <code>project</code> and <code>team</code> to group cost.</Notice>
      <Field label="Tags (one key=value per line)">{(id) => <textarea id={id} className="ff-input" rows={8} value={text} onChange={(e) => setText(e.target.value)} />}</Field>
    </Modal>
  );
}

function BudgetForm({ existing, onClose, onSaved }: { existing?: Budget; onClose: () => void; onSaved: () => void }) {
  const { api } = usePortal();
  const first = new Date(); first.setUTCDate(1);
  const [name, setName] = useState(existing?.name ?? "");
  const [amount, setAmount] = useState(existing ? String(existing.amount) : "");
  const [start, setStart] = useState((existing?.start_date ?? first.toISOString()).slice(0, 10));
  const [end, setEnd] = useState((existing?.end_date ?? "").slice(0, 10));
  const [emails, setEmails] = useState(existing?.notifications[0]?.contact_emails.join(", ") ?? "");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    try {
      const contacts = emails.split(",").map((e) => e.trim()).filter(Boolean);
      await api.saveBudget({
        name, amount: Number(amount), time_grain: "Monthly", start_date: start, end_date: end || null,
        notifications: contacts.length ? [{ threshold: 80, threshold_type: "Actual", contact_emails: contacts }, { threshold: 100, threshold_type: "Forecasted", contact_emails: contacts }] : [],
      });
      onSaved();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Modal title={existing ? `Edit ${existing.name}` : "Create a budget"} onClose={onClose} footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={save}>Save</Button></>}>
      {error && <Notice tone="bad">{error}</Notice>}
      <Field label="Name">{(id) => <input id={id} className="ff-input" value={name} disabled={!!existing} onChange={(e) => setName(e.target.value)} />}</Field>
      <Field label="Monthly amount">{(id) => <input id={id} className="ff-input" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />}</Field>
      <div className="ff-row">
        <Field label="Start (first of a month)">{(id) => <input id={id} className="ff-input" type="date" value={start} onChange={(e) => setStart(e.target.value)} />}</Field>
        <Field label="End (optional)">{(id) => <input id={id} className="ff-input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />}</Field>
      </div>
      <Field label="Email alerts to (optional)" hint="Alerts at 80% of actual cost and when the forecast reaches 100%.">{(id) => <input id={id} className="ff-input" value={emails} onChange={(e) => setEmails(e.target.value)} />}</Field>
      <Notice>A Cost Management budget alerts; it can't block requests. Use a cost control to block.</Notice>
    </Modal>
  );
}
