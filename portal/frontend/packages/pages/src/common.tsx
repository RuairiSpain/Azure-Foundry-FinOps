import { ErrorCard, Loading, formatMoney } from "@foundry-finops/ui";
import type { ReactNode } from "react";
import type { Loaded } from "./context";

/** Renders a section from a `useLoad` result: spinner, its own error with a retry, or the content. */
export function Section<T>({ load, children }: { load: Loaded<T>; children: (data: T) => ReactNode }) {
  if (load.data !== null) return <>{children(load.data)}</>;
  if (load.error) return <ErrorCard error={load.error} onRetry={load.reload} />;
  return <Loading />;
}

export const RANGES = [
  { days: 7, label: "Last 7 days" },
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
];

export function RangePicker({ days, onChange }: { days: number; onChange: (d: number) => void }) {
  return (
    <select className="ff-select" aria-label="Date range" value={days} onChange={(e) => onChange(Number(e.target.value))}>
      {RANGES.map((r) => (
        <option key={r.days} value={r.days}>
          {r.label}
        </option>
      ))}
    </select>
  );
}

export const money = (currency: string) => (v: number) => formatMoney(v, currency);

export function Notes({ items }: { items: string[] }) {
  return items.length ? (
    <ul className="ff-notes">
      {items.map((n) => (
        <li key={n}>{n}</li>
      ))}
    </ul>
  ) : null;
}
