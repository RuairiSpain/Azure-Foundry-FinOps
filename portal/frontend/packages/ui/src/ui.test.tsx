import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Empty, ErrorCard, Modal, Progress, StackedBars, StateBadge, Table, Trend, formatMoney, formatPct } from "./index";

describe("formatting", () => {
  it("never invents a value", () => {
    expect(formatMoney(null)).toBe("–");
    expect(formatPct(undefined)).toBe("–");
    expect(formatMoney(1234.5, "USD")).toContain("1,235");
    expect(formatMoney(5, "USD", 0)).toContain("5");
  });
});

describe("Trend", () => {
  it("shows New when there was no previous cost, and a dash when unknown", () => {
    const { rerender } = render(<Trend pct={null} isNew />);
    expect(screen.getByText("New")).toBeInTheDocument();
    rerender(<Trend pct={null} />);
    expect(screen.getByText("–")).toBeInTheDocument();
  });
  it("colours cost increases as bad and decreases as good", () => {
    const { rerender, container } = render(<Trend pct={12.34} />);
    expect(container.querySelector(".ff-trend.up")).toHaveTextContent("12.3%");
    rerender(<Trend pct={-5} />);
    expect(container.querySelector(".ff-trend.down")).toBeInTheDocument();
    rerender(<Trend pct={5} upIsBad={false} />);
    expect(container.querySelector(".ff-trend.down")).toBeInTheDocument();
  });
});

describe("StateBadge and Progress", () => {
  it("words the states as the Foundry portal does and flags over budget", () => {
    render(<StateBadge state="Limit reached" overBudget />);
    expect(screen.getByText("Limit reached")).toHaveClass("bad");
    expect(screen.getByText("Over budget")).toBeInTheDocument();
  });
  it("says usage is unknown instead of showing zero", () => {
    render(<Progress value={null} />);
    expect(screen.getByText("Usage unknown")).toBeInTheDocument();
  });
  it("is accessible and changes tone at the thresholds", () => {
    const { container, rerender } = render(<Progress value={85} label="Quota used" />);
    expect(screen.getByRole("progressbar", { name: "Quota used" })).toHaveAttribute("aria-valuenow", "85");
    expect(container.querySelector(".ff-progress.warn")).toBeInTheDocument();
    rerender(<Progress value={120} />);
    expect(container.querySelector(".ff-progress.bad")).toBeInTheDocument();
  });
});

describe("Table", () => {
  const rows = [{ n: "b", v: 2 }, { n: "a", v: 3 }, { n: "c", v: null as number | null }];
  const columns = [
    { key: "n", header: "Name", sortValue: (r: (typeof rows)[number]) => r.n, render: (r: (typeof rows)[number]) => r.n },
    { key: "v", header: "Value", numeric: true, sortValue: (r: (typeof rows)[number]) => r.v, render: (r: (typeof rows)[number]) => String(r.v ?? "–") },
  ];
  const order = () => within(screen.getAllByRole("rowgroup")[1]!).getAllByRole("row").map((r) => r.textContent);

  it("sorts on a header click, nulls last, and toggles direction", async () => {
    render(<Table rows={rows} columns={columns} rowKey={(r) => r.n} />);
    await userEvent.click(screen.getByText("Value"));
    expect(order()).toEqual(["b2", "a3", "c–"]);
    await userEvent.click(screen.getByText(/Value/));
    expect(order()).toEqual(["a3", "b2", "c–"]);
  });
  it("calls onRowClick and shows the empty message", async () => {
    const onClick = vi.fn();
    const { rerender } = render(<Table rows={rows} columns={columns} rowKey={(r) => r.n} onRowClick={onClick} />);
    await userEvent.click(screen.getByRole("cell", { name: "a" }));
    expect(onClick).toHaveBeenCalledWith(rows[1]);
    rerender(<Table rows={[]} columns={columns} rowKey={(r) => r.n} empty="Nothing here" />);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
  });
});

describe("Modal, ErrorCard, Empty", () => {
  it("closes on Escape and on the close button", async () => {
    const onClose = vi.fn();
    render(<Modal title="T" onClose={onClose}>body</Modal>);
    expect(screen.getByRole("dialog", { name: "T" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByLabelText("Close"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
  it("shows an error with a retry that works", async () => {
    const retry = vi.fn();
    render(<ErrorCard error={new Error("boom")} onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByText("Try again"));
    expect(retry).toHaveBeenCalled();
    render(<Empty>nothing</Empty>);
  });
});

describe("StackedBars", () => {
  it("draws a column per day with a text alternative, and groups the tail into Other", () => {
    const labels = ["2026-11-01", "2026-11-02"];
    const series = Array.from({ length: 12 }, (_, i) => ({ name: `s${i}`, values: [i + 1, 1] }));
    const { container } = render(<StackedBars labels={labels} series={series} maxSeries={4} />);
    expect(screen.getByRole("img")).toBeInTheDocument();
    expect(screen.getByText("Other")).toBeInTheDocument();
    expect(container.querySelectorAll("rect").length).toBe(8);
  });
  it("says when there is nothing to draw", () => {
    render(<StackedBars labels={[]} series={[]} />);
    expect(screen.getByText("No data in this range.")).toBeInTheDocument();
  });
});
