import { Card, Kpi, Notice, PageHead, SplitBar, StackedBars, Table, Tabs, Trend, formatMoney } from "@foundry-finops/ui";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CostControlsPanel } from "./CostControlsPanel";
import { Notes, RangePicker, Section } from "./common";
import { useLoad, usePortal } from "./context";

const GROUPS = [
  { id: "project", label: "Project" },
  { id: "agent", label: "Agent" },
  { id: "model", label: "Model" },
  { id: "deployment", label: "Deployment" },
  { id: "team", label: "Team tag" },
  { id: "cost_centre", label: "Cost centre" },
];

function Overview() {
  const { api } = usePortal();
  const [days, setDays] = useState(30);
  const [group, setGroup] = useState("project");
  const o = useLoad(() => api.costOverview(days), [days]);
  const table = useLoad(() => api.costTable(group, days), [group, days]);
  const series = useLoad(() => api.costSeries(group, days), [group, days]);
  return (
    <>
      <div className="ff-row" style={{ marginBottom: 12 }}>
        <RangePicker days={days} onChange={setDays} />
      </div>
      <Section load={o}>
        {(c) => (
          <>
            <div className="ff-grid kpis">
              <Kpi label={`Estimated cost · last ${c.days} days`} value={formatMoney(c.estimated_cost.value, c.currency)}
                trend={<Trend pct={c.estimated_cost.trend_pct} isNew={c.estimated_cost.previous === 0 && c.estimated_cost.value > 0} />} sub="vs the previous period" />
              <Kpi label="Month-end forecast" value={c.forecast.value != null ? formatMoney(c.forecast.value, c.currency) : "–"} sub={c.forecast.note ?? "projected total for this month"} />
              <Kpi label="Cost controls" value={`${c.cost_controls.attention} of ${c.cost_controls.total}`} sub="need attention" />
              <section className="ff-card">
                <div className="ff-muted" style={{ fontSize: 12, marginBottom: 8 }}>Spend breakdown</div>
                <SplitBar parts={[{ label: "Agent cost", value: c.spend_breakdown.agent_cost }, { label: "Direct model cost", value: c.spend_breakdown.direct_model_cost }]}
                  format={(v) => formatMoney(v, c.currency)} />
              </section>
            </div>
            <Notes items={c.notes} />
          </>
        )}
      </Section>
      <Card title="Cost over time" actions={
        <select className="ff-select" aria-label="Group by" value={group} onChange={(e) => setGroup(e.target.value)}>
          {GROUPS.map((g) => <option key={g.id} value={g.id}>Group by {g.label}</option>)}
        </select>
      }>
        <Section load={series}>
          {(s) => <StackedBars labels={s.labels} series={s.series} format={(v) => formatMoney(v)} axisFormat={(v) => formatMoney(v, "USD", 0)} ariaLabel={`Daily cost by ${group}`} />}
        </Section>
      </Card>
      <div style={{ height: 14 }} />
      <Card title={`Cost by ${GROUPS.find((g) => g.id === group)?.label.toLowerCase()}`}>
        <Section load={table}>
          {(t) => (
            <Table
              rows={t.rows}
              rowKey={(r) => r.name}
              initialSort={{ key: "cost", desc: true }}
              columns={[
                { key: "n", header: "Name", sortValue: (r) => r.name, render: (r) => r.name },
                { key: "cost", header: "Cost", numeric: true, sortValue: (r) => r.cost, render: (r) => formatMoney(r.cost, t.currency) },
                { key: "share", header: "Share", numeric: true, render: (r) => (r.share_pct == null ? "–" : `${r.share_pct}%`) },
                { key: "trend", header: "Cost trend", numeric: true, render: (r) => (r.previous_cost == null ? "–" : <Trend pct={r.trend_pct} isNew={r.new} />) },
              ]}
            />
          )}
        </Section>
      </Card>
    </>
  );
}

/** Manage > Cost: estimated cost, forecast, spend breakdown and cost controls. */
export function CostPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "controls" ? "controls" : "overview";
  return (
    <>
      <PageHead title="Cost" description="Cost from Azure Cost Management at the prices you pay, with the cost controls that guard it." />
      <Tabs tabs={[{ id: "overview", label: "Overview" }, { id: "controls", label: "Cost Control" }]} value={tab}
        onChange={(t) => setParams(t === "overview" ? {} : { tab: t })} />
      {tab === "overview" ? <Overview /> : <CostControlsPanel />}
      <Notice>
        Cost appears 8 to 24 hours after usage, and the current month's amounts are estimates until your invoice is final.
      </Notice>
    </>
  );
}
