export function formatMoney(value: number | null | undefined, currency = "USD", digits?: number): string {
  if (value === null || value === undefined) return "–";
  const abs = Math.abs(value);
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: digits ?? (abs >= 1000 ? 0 : 2),
    minimumFractionDigits: digits,
  }).format(value);
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(value);
}

export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return "–";
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "–";
  return `${value.toFixed(digits)}%`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "–";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "–";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}
