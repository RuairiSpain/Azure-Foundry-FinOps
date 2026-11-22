import type { CostControl, CostControls } from "@foundry-finops/api";
import { Button, Card, Modal, Notice, Progress, StateBadge, Table, formatDate, formatMoney, formatNumber } from "@foundry-finops/ui";
import { useState } from "react";
import { CostControlForm } from "./CostControlForm";
import { Section } from "./common";
import { useLoad, usePortal, type Loaded } from "./context";

const applies = (c: CostControl): string => {
  const parts = [c.project ? `Project ${c.project}` : "Foundry resource"];
  for (const [k, v] of Object.entries(c.filters ?? {})) if (k !== "project" || !c.project) parts.push(`${k}: ${v.join(", ")}`);
  return parts.join(" · ");
};
const budget = (c: CostControl) =>
  `${c.category === "Cost" ? formatMoney(c.amount) : `${formatNumber(c.amount)} tokens`} / ${c.time_grain.toLowerCase()}${c.sharing !== "shared" ? ` · per ${c.sharing === "sessionId" ? "session" : c.sharing}` : ""}`;

/** The cost controls in scope, with their state and an estimate of how close each is to its limit. */
export function CostControlsPanel({ project, load: external }: { project?: string; load?: Loaded<CostControls> }) {
  const { api, me } = usePortal();
  const own = useLoad(() => api.costControls(project), [project]);
  const load = external ?? own;
  const projects = useLoad(() => api.projects(), []);
  const [form, setForm] = useState<{ existing?: CostControl } | null>(null);
  const [detail, setDetail] = useState<CostControl | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canCreate = me.role !== "viewer" && !me.read_only;

  const remove = async (c: CostControl) => {
    if (!window.confirm(`Delete ${c.name}? Its alerts and blocks stop.`)) return;
    try {
      await api.deleteCostControl(c.name, c.project);
      setDetail(null);
      load.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Card
      title="Cost controls"
      actions={canCreate ? <Button variant="primary" onClick={() => setForm({})}>Create</Button> : undefined}
    >
      {error && <Notice tone="bad">{error}</Notice>}
      <Section load={load}>
        {(d) => (
          <>
            {d.project_budget && (
              <Notice>
                <strong>Project budget:</strong>{" "}
                {d.project_budget.name
                  ? `${d.project_budget.name}, ${formatMoney(d.project_budget.amount)}${d.project_budget.count > 1 ? ` (lowest of ${d.project_budget.count} cost controls)` : ""}`
                  : "No project-level budget set"}
                {d.project_budget.per_agent_or_session > 0 && ` · ${d.project_budget.per_agent_or_session} per agent or session`}
              </Notice>
            )}
            <Table
              rows={d.cost_controls}
              rowKey={(c) => `${c.project ?? ""}/${c.name}`}
              onRowClick={setDetail}
              empty="No cost controls in your scope."
              columns={[
                { key: "n", header: "Policy name", sortValue: (c) => c.name, render: (c) => <strong>{c.name}</strong> },
                { key: "a", header: "Applies to", render: applies },
                { key: "b", header: "Budget", render: budget },
                { key: "d", header: "Start – end", render: (c) => `${formatDate(c.start_date)} – ${formatDate(c.end_date)}` },
                { key: "u", header: "Closest to limit (estimated)", sortValue: (c) => c.percent_used, render: (c) => <Progress value={c.percent_used} label={`${c.name} used`} /> },
                { key: "s", header: "State", sortValue: (c) => c.state, render: (c) => <StateBadge state={c.state} overBudget={c.over_budget} /> },
              ]}
            />
            <ul className="ff-notes"><li>{d.note}</li></ul>
          </>
        )}
      </Section>
      {detail && (
        <Modal
          title={detail.name}
          onClose={() => setDetail(null)}
          footer={
            detail.can_write && !me.read_only ? (
              <>
                <Button variant="danger" onClick={() => remove(detail)}>Delete</Button>
                <Button variant="primary" onClick={() => { setForm({ existing: detail }); setDetail(null); }}>Edit</Button>
              </>
            ) : undefined
          }
        >
          <table className="ff-table"><tbody>
            <tr><th>Applies to</th><td>{applies(detail)}</td></tr>
            <tr><th>Budget</th><td>{budget(detail)}</td></tr>
            <tr><th>Period</th><td>{formatDate(detail.start_date)} – {formatDate(detail.end_date)} (UTC)</td></tr>
            <tr><th>Alert</th><td>{detail.alert ? `at ${detail.alert.threshold}%` : "None"}</td></tr>
            <tr><th>Block</th><td>{detail.block ? `at ${detail.block.threshold}%` : "None: requests continue"}</td></tr>
            <tr><th>Usage this period</th><td>{detail.usage != null ? `${formatNumber(detail.usage)} (${detail.percent_used ?? "–"}%)` : "Usage unknown"}</td></tr>
            <tr><th>State</th><td><StateBadge state={detail.state} overBudget={detail.over_budget} /></td></tr>
          </tbody></table>
          {detail.usage_note && <p className="ff-muted">{detail.usage_note}</p>}
          {!detail.can_write && <Notice>You can view this cost control. Changing it needs write access to its scope.</Notice>}
        </Modal>
      )}
      {form && (
        <CostControlForm
          existing={form.existing}
          projects={(projects.data ?? []).map((p) => p.name)}
          fixedProject={project}
          onClose={() => setForm(null)}
          onSaved={() => { setForm(null); load.reload(); }}
        />
      )}
    </Card>
  );
}
