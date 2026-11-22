import { Card, Kpi, LineChart, PageHead, Progress, Table, formatCompact, formatNumber } from "@foundry-finops/ui";
import { useState } from "react";
import { Notes, Section } from "./common";
import { useLoad, usePortal } from "./context";

const WINDOWS = [
  { hours: 24, label: "Last 24 hours" },
  { hours: 168, label: "Last 7 days" },
  { hours: 720, label: "Last 30 days" },
];

/** Manage > Operations: what the Foundry resource consumed, within minutes. */
export function OperationsPage() {
  const { api } = usePortal();
  const [hours, setHours] = useState(168);
  const ops = useLoad(() => api.operations(hours), [hours]);
  return (
    <>
      <PageHead
        title="Operations"
        description="Tokens and requests by deployment, and how close each model is to its quota."
        actions={
          <select className="ff-select" aria-label="Time range" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            {WINDOWS.map((w) => <option key={w.hours} value={w.hours}>{w.label}</option>)}
          </select>
        }
      />
      <Section load={ops}>
        {(o) => (
          <>
            <div className="ff-grid kpis">
              <Kpi label="Tokens" value={formatCompact(o.kpis.tokens)} />
              <Kpi label="Requests" value={formatCompact(o.kpis.requests)} />
              <Kpi label="Model deployments" value={o.kpis.deployments} />
            </div>
            <Card title="Tokens over time, by deployment">
              <LineChart labels={o.labels} series={o.series} format={formatCompact} ariaLabel="Tokens over time by deployment" />
            </Card>
            <div className="ff-grid two" style={{ marginTop: 14 }}>
              <Card title="Deployments">
                <Table
                  rows={o.deployments}
                  rowKey={(d) => d.deployment}
                  initialSort={{ key: "tokens", desc: true }}
                  columns={[
                    { key: "d", header: "Deployment", sortValue: (d) => d.deployment, render: (d) => d.deployment },
                    { key: "tokens", header: "Tokens", numeric: true, sortValue: (d) => d.tokens, render: (d) => formatCompact(d.tokens) },
                    { key: "req", header: "Requests", numeric: true, sortValue: (d) => d.requests, render: (d) => formatCompact(d.requests) },
                    { key: "tpr", header: "Tokens / request", numeric: true, render: (d) => formatNumber(d.tokens_per_request) },
                    { key: "lim", header: "TPM limit", numeric: true, render: (d) => formatCompact(d.tokens_per_minute_limit) },
                  ]}
                />
              </Card>
              <Card title="Quota">
                <Table
                  rows={o.quota}
                  rowKey={(q) => q.name}
                  initialSort={{ key: "used", desc: true }}
                  columns={[
                    { key: "n", header: "Model and SKU", render: (q) => q.name.replace("OpenAI.", "") },
                    { key: "used", header: "Used", sortValue: (q) => q.percent_used, render: (q) => <Progress value={q.percent_used} label={`${q.name} quota used`} /> },
                    { key: "lim", header: "Limit", numeric: true, render: (q) => formatNumber(q.limit) },
                  ]}
                />
              </Card>
            </div>
            <Notes items={o.notes} />
          </>
        )}
      </Section>
    </>
  );
}
