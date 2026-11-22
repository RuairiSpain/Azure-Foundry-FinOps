import { useEffect, useId, useMemo, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { formatPct } from "./format";

export type Tone = "ok" | "warn" | "bad" | "accent" | "neutral";

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`ff-card ${className}`}>
      {(title || actions) && (
        <header>
          {title && <h2>{title}</h2>}
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Kpi({ label, value, sub, trend }: { label: string; value: ReactNode; sub?: ReactNode; trend?: ReactNode }) {
  return (
    <section className="ff-card ff-kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      <div className="sub">
        {trend} {sub}
      </div>
    </section>
  );
}

/** Change from the previous period. For cost, up is worse, so it is red. "New" means there was no cost before. */
export function Trend({ pct, isNew = false, upIsBad = true }: { pct: number | null | undefined; isNew?: boolean; upIsBad?: boolean }) {
  if (isNew) return <span className="ff-badge accent">New</span>;
  if (pct === null || pct === undefined) return <span className="ff-muted">–</span>;
  const dir = Math.abs(pct) < 0.05 ? "flat" : pct > 0 ? (upIsBad ? "up" : "down") : upIsBad ? "down" : "up";
  const arrow = pct > 0 ? "▲" : pct < 0 ? "▼" : "▬";
  return (
    <span className={`ff-trend ${dir}`} title="Change from the previous period of the same length">
      {arrow} {formatPct(Math.abs(pct))}
    </span>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`ff-badge ${tone === "neutral" ? "" : tone}`}>{children}</span>;
}

const STATE_TONE: Record<string, Tone> = {
  "On track": "ok",
  Active: "ok",
  "Near threshold": "warn",
  "Limit reached": "bad",
  Scheduled: "accent",
  Expired: "neutral",
};

/** A cost control's state as the Foundry portal words it. */
export function StateBadge({ state, overBudget = false }: { state: string; overBudget?: boolean }) {
  return (
    <>
      <Badge tone={STATE_TONE[state] ?? "neutral"}>{state}</Badge>
      {overBudget && (
        <span className="ff-muted" style={{ marginLeft: 6 }} title="The limit is reached and there is no Block action, so requests continue">
          Over budget
        </span>
      )}
    </>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "bad"; children: ReactNode }) {
  return (
    <div className={`ff-notice ${tone}`} role={tone === "bad" ? "alert" : "note"}>
      {children}
    </div>
  );
}

export function Progress({ value, warnAt = 80, badAt = 100, label }: { value: number | null | undefined; warnAt?: number; badAt?: number; label?: string }) {
  if (value === null || value === undefined) return <span className="ff-muted">Usage unknown</span>;
  const tone = value >= badAt ? "bad" : value >= warnAt ? "warn" : "";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div className={`ff-progress ${tone}`} role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100} aria-label={label ?? "Used"} style={{ flex: 1 }}>
        <span style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
      </div>
      <span style={{ minWidth: 44, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{formatPct(value)}</span>
    </div>
  );
}

export function Button({ variant = "default", small = false, className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "danger"; small?: boolean }) {
  return <button type="button" className={`ff-btn ${variant === "default" ? "" : variant} ${small ? "small" : ""} ${className}`} {...rest} />;
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="ff-field">
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string }[]; value: T; onChange: (id: T) => void }) {
  return (
    <div className="ff-tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={t.id === value} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Modal({ title, onClose, children, footer }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="ff-modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="ff-modal" role="dialog" aria-modal="true" aria-label={title}>
        <header>
          <h2>{title}</h2>
          <Button small onClick={onClose} aria-label="Close">
            ✕
          </Button>
        </header>
        {children}
        {footer && <footer>{footer}</footer>}
      </div>
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="ff-empty" role="status">
      {label}
    </div>
  );
}

/** One card failing doesn't take the page down: it shows its own message, as the Foundry portal's cards do. */
export function ErrorCard({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <Notice tone="bad">
      <strong>This couldn't be loaded.</strong> {message}{" "}
      {onRetry && (
        <Button small onClick={onRetry}>
          Try again
        </Button>
      )}
    </Notice>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="ff-empty">{children}</div>;
}

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  /** Value to sort by; omit to make the column unsortable. */
  sortValue?: (row: T) => number | string | null | undefined;
  numeric?: boolean;
}

export function Table<T>({ columns, rows, rowKey, onRowClick, empty = "Nothing to show.", initialSort }: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
  initialSort?: { key: string; desc?: boolean };
}) {
  const [sort, setSort] = useState(initialSort ?? null);
  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sort?.key);
    if (!col?.sortValue || !sort) return rows;
    const get = col.sortValue;
    return [...rows].sort((a, b) => {
      const x = get(a), y = get(b);
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : 1) * (sort.desc ? -1 : 1);
    });
  }, [rows, columns, sort]);
  if (rows.length === 0) return <Empty>{empty}</Empty>;
  return (
    <div className="ff-table-wrap">
      <table className="ff-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                className={`${c.sortValue ? "sortable" : ""} ${c.numeric ? "num" : ""}`}
                aria-sort={sort?.key === c.key ? (sort.desc ? "descending" : "ascending") : undefined}
                onClick={c.sortValue ? () => setSort((s) => ({ key: c.key, desc: s?.key === c.key ? !s.desc : false })) : undefined}
              >
                {c.header}
                {sort?.key === c.key ? (sort.desc ? " ▼" : " ▲") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={rowKey(row)} className={onRowClick ? "clickable" : ""} onClick={onRowClick ? () => onRowClick(row) : undefined}>
              {columns.map((c) => (
                <td key={c.key} className={c.numeric ? "num" : ""}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export interface NavSection {
  title: string;
  items: { id: string; label: string; to: string }[];
}

/** The page frame: top bar, side navigation and content. Router-agnostic: you provide how a link is drawn. */
export function AppShell({ brand, nav, renderLink, user, onSignOut, children, toolbar }: {
  brand: string;
  nav: NavSection[];
  renderLink: (item: { id: string; label: string; to: string }) => ReactNode;
  user?: { name: string; detail?: string } | null;
  onSignOut?: () => void;
  children: ReactNode;
  toolbar?: ReactNode;
}) {
  return (
    <div className="ff-shell">
      <div className="ff-top">
        <span className="ff-brand">
          <span className="ff-brand-mark" aria-hidden />
          {brand}
        </span>
        <span className="ff-spacer" />
        {toolbar}
        {user && (
          <span className="ff-user">
            <span title={user.detail}>{user.name}</span>
            {onSignOut && (
              <Button small onClick={onSignOut}>
                Sign out
              </Button>
            )}
          </span>
        )}
      </div>
      <nav className="ff-nav" aria-label="Portal">
        {nav.map((s) => (
          <div key={s.title}>
            <h4>{s.title}</h4>
            {s.items.map((i) => (
              <div key={i.id}>{renderLink(i)}</div>
            ))}
          </div>
        ))}
      </nav>
      <main className="ff-main">{children}</main>
    </div>
  );
}

export function PageHead({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="ff-page-head">
      <div style={{ flex: 1 }}>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions}
    </div>
  );
}

/** Switch between light, dark and the system setting. */
export function ThemeToggle() {
  const [mode, setMode] = useState<string>(() => {
    try { return localStorage.getItem("ff-theme") ?? "system"; } catch { return "system"; }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (mode === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", mode);
    try { localStorage.setItem("ff-theme", mode); } catch { /* storage unavailable */ }
  }, [mode]);
  return (
    <select className="ff-select" aria-label="Theme" value={mode} onChange={(e) => setMode(e.target.value)}>
      <option value="system">System theme</option>
      <option value="light">Light</option>
      <option value="dark">Dark</option>
    </select>
  );
}
