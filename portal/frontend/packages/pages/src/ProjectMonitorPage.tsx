import { Card, Kpi, Notice, PageHead, StackedBars, Table, Tabs, Trend, formatMoney } from "@foundry-finops/ui";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { CostControlsPanel } from "./CostControlsPanel";
import { Notes, RangePicker, Section } from "./common";
import { useLoad, usePortal } from "./context";

type Tab = "overview" | "cost" | "controls";

/** Build > Monitor for one project: its cost, its agents and its cost controls. */
export function ProjectMonitorPage() {
  const { api } = usePortal();
  const { project = "" } = useParams();
  const navigate = useNavigate();
  const [days, setDays] = useState(30);
  const [tab, setTab] = useState<Tab>("overview");
  const projects = useLoad(() => api.projects(), []);
  const mon = useLoad(() => (project ? api.projectMonitor(project, days) : Promise.reject(new Error("Choose a project"))), [project, days]);
  return (
    <>
      <PageHead
        title={project ? `Monitor · ${project}` : "Project monitor"}
        description="Billed cost, agents and cost controls for one project."
        actions={
          <div className="ff-row">
            <select className="ff-select" aria-label="Project" value={project} onChange={(e) => navigate(`/projects/${encodeURIComponent(e.target.value)}`)}>
              {!project && <option value="">Choose a project…</option>}
              {(projects.data ?? []).map((p) => <option key={p.name} value={p.name}>{p.display_name}</option>)}
            </select>
            <RangePicker days={days} onChange={setDays} />
          </div>
        }
      />
      <Tabs<Tab> tabs={[{ id: "overview", label: "Overview" }, { id: "cost", label: "Cost" }, { id: "controls", label: "Cost Control" }]} value={tab} onChange={setTab} />
      {!project ? <Notice>Choose a project to see its monitor.</Notice> : (
        <Section load={mon}>
          {(m) => tab === "controls" ? <CostControlsPanel project={project} load={{ data: m.controls, error: null, loading: false, reload: mon.reload }} /> : (
            <>
              <div className="ff-grid kpis">
                <Kpi label="Latest billed cost" value={formatMoney(m.latest_billed_cost.value, m.currency)}
                  trend={<Trend pct={m.latest_billed_cost.trend_pct} isNew={m.latest_billed_cost.previous === 0 && m.latest_billed_cost.value > 0} />} sub={`last ${m.days} days`} />
                <Kpi label="Project budget" value={m.controls.project_budget?.name ? formatMoney(m.controls.project_budget.amount) : "None"}
                  sub={m.controls.project_budget?.name ? m.controls.project_budget.name : "No project-level budget set"} />
                <Kpi label="Agents" value={m.agents.filter((a) => !a.agent.startsWith("(")).length} />
              </div>
              {tab === "overview" && (
                <Card title="Cost over time"><StackedBars labels={m.series.labels} series={m.series.series} format={(v) => formatMoney(v)} axisFormat={(v) => formatMoney(v, "USD", 0)} /></Card>
              )}
              <div style={{ height: 14 }} />
              <Card title="Cost by agent">
                <Table rows={m.agents} rowKey={(a) => a.agent} initialSort={{ key: "cost", desc: true }}
                  empty="No agent cost in this range. Cost appears 8 to 24 hours after usage."
                  columns={[
                    { key: "a", header: "Agent", sortValue: (a) => a.agent, render: (a) => a.agent },
                    { key: "cost", header: "Cost", numeric: true, sortValue: (a) => a.cost, render: (a) => formatMoney(a.cost, m.currency) },
                  ]} />
              </Card>
              <Notes items={m.notes} />
            </>
          )}
        </Section>
      )}
    </>
  );
}
