import { useId, useMemo } from "react";

export interface ChartSeries {
  name: string;
  values: number[];
}

const color = (i: number) => `var(--ff-c${(i % 10) + 1})`;

interface ChartProps {
  labels: string[];
  series: ChartSeries[];
  /** Format a value for tooltips and the text alternative. */
  format?: (v: number) => string;
  /** Format for the axis; defaults to `format`. Shorter is better here. */
  axisFormat?: (v: number) => string;
  height?: number;
  /** Most series to draw; the rest are summed into "Other". */
  maxSeries?: number;
  ariaLabel?: string;
}

function group(series: ChartSeries[], max: number): ChartSeries[] {
  if (series.length <= max) return series;
  const head = series.slice(0, max - 1);
  const rest = series.slice(max - 1);
  const other = rest[0]!.values.map((_, i) => rest.reduce((s, r) => s + (r.values[i] ?? 0), 0));
  return [...head, { name: "Other", values: other }];
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

const W = 720;
const PAD = { l: 52, r: 8, t: 8, b: 22 };

function Legend({ series }: { series: ChartSeries[] }) {
  return (
    <div className="ff-legend">
      {series.map((s, i) => (
        <span key={s.name}>
          <i style={{ background: color(i) }} />
          {s.name}
        </span>
      ))}
    </div>
  );
}

function Axis({ max, h, format }: { max: number; h: number; format: (v: number) => string }) {
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <>
      {ticks.map((t) => {
        const y = PAD.t + (h - PAD.t - PAD.b) * (1 - t);
        return (
          <g key={t}>
            <line className="grid" x1={PAD.l} x2={W - PAD.r} y1={y} y2={y} />
            <text x={PAD.l - 6} y={y + 4} textAnchor="end">
              {format(max * t)}
            </text>
          </g>
        );
      })}
    </>
  );
}

function XLabels({ labels, h, n }: { labels: string[]; h: number; n: number }) {
  const every = Math.max(1, Math.ceil(labels.length / 8));
  const step = (W - PAD.l - PAD.r) / Math.max(n, 1);
  return (
    <>
      {labels.map((l, i) =>
        i % every === 0 ? (
          <text key={l + i} x={PAD.l + step * (i + 0.5)} y={h - 6} textAnchor="middle">
            {l.slice(5, 10)}
          </text>
        ) : null,
      )}
    </>
  );
}

/** Stacked columns over time (daily cost per project and so on). */
export function StackedBars({ labels, series, format = String, axisFormat, height = 240, maxSeries = 8, ariaLabel }: ChartProps) {
  const id = useId();
  const data = useMemo(() => group(series, maxSeries), [series, maxSeries]);
  const totals = labels.map((_, i) => data.reduce((s, d) => s + (d.values[i] ?? 0), 0));
  const max = niceMax(Math.max(0, ...totals));
  if (labels.length === 0) return <div className="ff-empty">No data in this range.</div>;
  const plotH = height - PAD.t - PAD.b;
  const step = (W - PAD.l - PAD.r) / labels.length;
  const bw = Math.max(2, step * 0.7);
  return (
    <figure style={{ margin: 0 }}>
      <svg className="ff-chart" viewBox={`0 0 ${W} ${height}`} role="img" aria-label={ariaLabel ?? "Stacked column chart"} aria-describedby={id}>
        <Axis max={max} h={height} format={axisFormat ?? format} />
        {labels.map((l, i) => {
          let y = PAD.t + plotH;
          return (
            <g key={l}>
              {data.map((s, k) => {
                const v = s.values[i] ?? 0;
                const hh = (v / max) * plotH;
                y -= hh;
                return hh > 0 ? (
                  <rect key={s.name} x={PAD.l + step * i + (step - bw) / 2} y={y} width={bw} height={hh} fill={color(k)}>
                    <title>{`${l.slice(0, 10)} · ${s.name}: ${format(v)}`}</title>
                  </rect>
                ) : null;
              })}
            </g>
          );
        })}
        <XLabels labels={labels} h={height} n={labels.length} />
      </svg>
      <figcaption id={id} className="sr-only">
        {data.map((s) => `${s.name}: ${format(s.values.reduce((a, b) => a + b, 0))}`).join("; ")}
      </figcaption>
      <Legend series={data} />
    </figure>
  );
}

/** Lines over time (tokens per deployment). */
export function LineChart({ labels, series, format = String, axisFormat, height = 240, maxSeries = 8, ariaLabel }: ChartProps) {
  const data = useMemo(() => group(series, maxSeries), [series, maxSeries]);
  const max = niceMax(Math.max(0, ...data.flatMap((s) => s.values)));
  if (labels.length < 2) return <div className="ff-empty">Not enough data points in this range.</div>;
  const plotH = height - PAD.t - PAD.b;
  const step = (W - PAD.l - PAD.r) / (labels.length - 1);
  return (
    <figure style={{ margin: 0 }}>
      <svg className="ff-chart" viewBox={`0 0 ${W} ${height}`} role="img" aria-label={ariaLabel ?? "Line chart"}>
        <Axis max={max} h={height} format={axisFormat ?? format} />
        {data.map((s, k) => (
          <polyline
            key={s.name}
            fill="none"
            stroke={color(k)}
            strokeWidth={1.8}
            points={s.values.map((v, i) => `${PAD.l + step * i},${PAD.t + plotH - (v / max) * plotH}`).join(" ")}
          >
            <title>{s.name}</title>
          </polyline>
        ))}
        <XLabels labels={labels.map((l) => l)} h={height} n={labels.length - 1} />
      </svg>
      <Legend series={data} />
    </figure>
  );
}

/** One bar split into labelled parts (agent cost against direct model cost). */
export function SplitBar({ parts, format = String }: { parts: { label: string; value: number }[]; format?: (v: number) => string }) {
  const total = parts.reduce((s, p) => s + p.value, 0);
  return (
    <div>
      <div style={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", background: "var(--ff-surface-2)" }} role="img"
        aria-label={parts.map((p) => `${p.label} ${format(p.value)}`).join(", ")}>
        {parts.map((p, i) => (
          <div key={p.label} style={{ width: `${total ? (p.value / total) * 100 : 0}%`, background: color(i) }} title={`${p.label}: ${format(p.value)}`} />
        ))}
      </div>
      <div className="ff-legend" style={{ justifyContent: "space-between" }}>
        {parts.map((p, i) => (
          <span key={p.label}>
            <i style={{ background: color(i) }} />
            {p.label} <strong style={{ color: "var(--ff-text)" }}>{format(p.value)}</strong>
            {total ? ` · ${((p.value / total) * 100).toFixed(0)}%` : ""}
          </span>
        ))}
      </div>
    </div>
  );
}
