import { Badge, Card, Empty, Kpi, Notice, PageHead, Table, Trend, formatMoney, formatNumber } from "@foundry-finops/ui";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Notes, RangePicker, Section } from "./common";
import { useLoad, usePortal } from "./context";

/** Manage > Fleet: your projects, what they cost, and what needs attention. */
export function FleetPage() {
  const { api } = usePortal();
  const [days, setDays] = useState(30);
  const fleet = useLoad(() => api.fleet(days), [days]);
  return (
    <>
      <PageHead
        title="Fleet"
        description="The projects in your scope with their cost, agents and cost controls."
        actions={<RangePicker days={days} onChange={setDays} />}
      />
      <Section load={fleet}>
        {(f) => (
          <>
            <div className="ff-grid kpis">
              <Kpi label="Projects" value={f.kpis.projects} />
              <Kpi label="Agents" value={f.kpis.agents} sub="from the agent.name billing tag" />
              <Kpi label="Model deployments" value={f.kpis.deployments ?? "–"} sub={f.kpis.deployments === null ? "whole-resource view" : undefined} />
              <Kpi label={`Estimated cost · last ${f.days} days`} value={formatMoney(f.kpis.cost, f.currency)}
                trend={<Trend pct={f.kpis.trend_pct} />} sub="vs the previous period" />
              <Kpi label="Cost controls needing attention" value={f.kpis.cost_controls_attention} />
              <Kpi label="Policy violations" value={f.kpis.policy_violations ?? "–"} />
            </div>
            {f.kpis.cost_controls_attention > 0 && (
              <Notice tone="warn">
                <strong>Needs attention.</strong>{" "}
                {f.attention.map((a) => `${a.name} (${a.state}${a.percent_used != null ? `, ${a.percent_used}%` : ""})`).join(" · ")}{" "}
                <Link to="/cost?tab=controls">Open cost controls</Link>
              </Notice>
            )}
            <div className="ff-grid two">
              <Card title="Projects">
                <Table
                  rows={f.projects}
                  rowKey={(p) => p.name}
                  initialSort={{ key: "cost", desc: true }}
                  empty="No projects in your scope."
                  columns={[
                    { key: "name", header: "Project", sortValue: (p) => p.name, render: (p) => <Link to={`/projects/${encodeURIComponent(p.name)}`}>{p.display_name}</Link> },
                    { key: "cost", header: "Cost", numeric: true, sortValue: (p) => p.cost, render: (p) => formatMoney(p.cost, f.currency) },
                    { key: "trend", header: "Cost trend", numeric: true, sortValue: (p) => p.trend_pct, render: (p) => <Trend pct={p.trend_pct} isNew={p.new} /> },
                    { key: "agents", header: "Agents", numeric: true, sortValue: (p) => p.agents, render: (p) => formatNumber(p.agents) },
                    { key: "cc", header: "Cost controls", numeric: true, render: (p) => (
                      <>
                        {p.cost_controls}
                        {p.attention > 0 && <> <Badge tone="warn">{p.attention} need attention</Badge></>}
                      </>
                    ) },
                  ]}
                />
              </Card>
              <div style={{ display: "grid", gap: 14, alignContent: "start" }}>
                <Card title="Top cost increases">
                  {f.top_cost_increases.length === 0 ? (
                    <Empty>No project cost more than in the previous period.</Empty>
                  ) : (
                    <Table
                      rows={f.top_cost_increases}
                      rowKey={(r) => r.name}
                      columns={[
                        { key: "n", header: "Project", render: (r) => r.name },
                        { key: "i", header: "Increase", numeric: true, render: (r) => formatMoney(r.increase, f.currency) },
                        { key: "t", header: "Change", numeric: true, render: (r) => <Trend pct={r.trend_pct} isNew={r.trend_pct === null} /> },
                      ]}
                    />
                  )}
                </Card>
                <Card title="Policy violations">
                  {f.policy === null ? (
                    <Notice tone="warn">{f.policy_note ?? "Policy data isn't available."}</Notice>
                  ) : f.policy.length === 0 ? (
                    <Empty>No violations in your scope.</Empty>
                  ) : (
                    <Table
                      rows={f.policy}
                      rowKey={(p) => p.resource_id}
                      columns={[
                        { key: "r", header: "Resource", render: (p) => <code>{p.resource_id.split("/").slice(-2).join("/")}</code> },
                        { key: "v", header: "Violations", numeric: true, render: (p) => p.violations },
                        { key: "p", header: "Policies", render: (p) => p.policies.join(", ") },
                      ]}
                    />
                  )}
                </Card>
              </div>
            </div>
            <Notes items={f.notes} />
          </>
        )}
      </Section>
    </>
  );
}
