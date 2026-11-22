import type { CostControl } from "@foundry-finops/api";
import { Button, Field, Modal, Notice } from "@foundry-finops/ui";
import { useState } from "react";
import { usePortal } from "./context";

const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
const day = (iso: string | null | undefined) => (iso ? iso.slice(0, 10) : "");

/** Create or edit a cost control. The rules are the service's: one alert and one Block action, both dates required. */
export function CostControlForm({ existing, projects, fixedProject, onClose, onSaved }: {
  existing?: CostControl;
  projects: string[];
  /** When the form is opened from a project's page, the cost control belongs to that project. */
  fixedProject?: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { api } = usePortal();
  const today = new Date().toISOString().slice(0, 10);
  const nextYear = new Date(Date.now() + 365 * 86400000).toISOString().slice(0, 10);
  const [name, setName] = useState(existing?.name ?? "");
  const [owner, setOwner] = useState(existing?.project ?? fixedProject ?? "");
  const [category, setCategory] = useState<"Cost" | "Tokens">(existing?.category ?? "Cost");
  const [amount, setAmount] = useState(String(existing?.amount ?? ""));
  const [grain, setGrain] = useState(existing?.time_grain ?? "Monthly");
  const [start, setStart] = useState(day(existing?.start_date) || today);
  const [end, setEnd] = useState(day(existing?.end_date) || nextYear);
  const [project, setProject] = useState(existing?.filters?.project?.[0] ?? (fixedProject ?? ""));
  const [deployments, setDeployments] = useState((existing?.filters?.deployment ?? []).join(", "));
  const [agents, setAgents] = useState((existing?.filters?.agent ?? []).join(", "));
  const [sharing, setSharing] = useState(existing?.sharing ?? "shared");
  const [alertPct, setAlertPct] = useState(existing?.alert ? String(existing.alert.threshold) : "80");
  const [blockPct, setBlockPct] = useState(existing?.block ? String(existing.block.threshold) : "100");
  const [conn, setConn] = useState(existing?.alert?.event_grid_connection_id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const problem = (): string | null => {
    if (!/^[A-Za-z0-9_-]{1,63}$/.test(name)) return "The name is 1 to 63 letters, numbers, hyphens or underscores.";
    const a = Number(amount);
    if (!(a > 0)) return "The amount must be above zero.";
    if (category === "Tokens" && !Number.isInteger(a)) return "A token limit is a whole number.";
    if (!start || !end) return "Both dates are required: a cost control has a start and an end date.";
    if (end <= start) return "The end date must be after the start date. Counting stops at the start of the end date.";
    if (list(agents).length && !(owner || project)) return "An agent filter needs a project.";
    if (alertPct && blockPct && Number(alertPct) > Number(blockPct)) return "The alert must be at or below the block threshold.";
    return null;
  };

  const save = async () => {
    const p = problem();
    if (p) return setError(p);
    setBusy(true);
    setError(null);
    const filters: Record<string, string[]> = {};
    const projectFilter = owner ? [owner] : project ? [project] : [];
    if (projectFilter.length && (list(agents).length || !owner)) filters.project = projectFilter;
    if (owner && list(agents).length) filters.project = [owner];
    if (list(deployments).length) filters.deployment = list(deployments);
    if (list(agents).length) filters.agent = list(agents);
    try {
      await api.saveCostControl({
        name, project: owner || null, category, amount: Number(amount), time_grain: grain,
        start_date: start, end_date: end, filters, sharing,
        alert: alertPct ? { threshold: Number(alertPct), event_grid_connection_id: conn || null } : null,
        block: blockPct ? { threshold: Number(blockPct) } : null,
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={existing ? `Edit ${existing.name}` : "Create a cost control"}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</Button>
        </>
      }
    >
      {error && <Notice tone="bad">{error}</Notice>}
      <Field label="Cost control name" hint="Identifies the cost control in its URL. It can't be changed later.">
        {(id) => <input id={id} className="ff-input" value={name} disabled={!!existing} onChange={(e) => setName(e.target.value)} />}
      </Field>
      <Field label="Owner" hint="A project's cost control covers only that project. Resource-level cost controls can span projects.">
        {(id) => (
          <select id={id} className="ff-select" value={owner} disabled={!!existing || !!fixedProject} onChange={(e) => setOwner(e.target.value)}>
            <option value="">Foundry resource</option>
            {projects.map((p) => <option key={p} value={p}>Project: {p}</option>)}
          </select>
        )}
      </Field>
      <div className="ff-row">
        <Field label="Budget type" hint="Can't be changed after creation.">
          {(id) => (
            <select id={id} className="ff-select" value={category} disabled={!!existing} onChange={(e) => setCategory(e.target.value as "Cost" | "Tokens")}>
              <option value="Cost">Spending (USD)</option>
              <option value="Tokens">Tokens</option>
            </select>
          )}
        </Field>
        <Field label={category === "Cost" ? "Amount (USD)" : "Token limit"}>
          {(id) => <input id={id} className="ff-input" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />}
        </Field>
        <Field label="Resets">
          {(id) => (
            <select id={id} className="ff-select" value={grain} onChange={(e) => setGrain(e.target.value)}>
              <option>Daily</option><option>Weekly</option><option>Monthly</option>
            </select>
          )}
        </Field>
      </div>
      <div className="ff-row">
        <Field label="Start date (UTC)">{(id) => <input id={id} className="ff-input" type="date" value={start} onChange={(e) => setStart(e.target.value)} />}</Field>
        <Field label="End date (UTC)" hint="Counting stops at the start of this day.">{(id) => <input id={id} className="ff-input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />}</Field>
      </div>
      <h3 style={{ margin: "6px 0 8px" }}>What does it apply to?</h3>
      {!owner && (
        <Field label="Project (optional)">
          {(id) => (
            <select id={id} className="ff-select" value={project} onChange={(e) => setProject(e.target.value)}>
              <option value="">All projects</option>
              {projects.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          )}
        </Field>
      )}
      <Field label="Deployments (optional)" hint="Comma-separated. Only GPT and OpenAI o-series deployments are supported in the preview.">
        {(id) => <input id={id} className="ff-input" value={deployments} onChange={(e) => setDeployments(e.target.value)} />}
      </Field>
      <Field label="Agents (optional)" hint="Comma-separated. Needs one project.">
        {(id) => <input id={id} className="ff-input" value={agents} onChange={(e) => setAgents(e.target.value)} />}
      </Field>
      <Field label="How should the budget be shared?">
        {(id) => (
          <select id={id} className="ff-select" value={sharing} onChange={(e) => setSharing(e.target.value)}>
            <option value="shared">One budget for everything</option>
            <option value="agent">A budget per agent</option>
            <option value="sessionId">A budget per session</option>
            {!owner && <option value="project">A budget per project</option>}
          </select>
        )}
      </Field>
      <h3 style={{ margin: "6px 0 8px" }}>Threshold actions</h3>
      <div className="ff-row">
        <Field label="Send alert at (% of budget)" hint="Leave empty for no alert.">{(id) => <input id={id} className="ff-input" inputMode="decimal" value={alertPct} onChange={(e) => setAlertPct(e.target.value)} />}</Field>
        <Field label="Block usage at (% of budget)" hint="Leave empty to only track.">{(id) => <input id={id} className="ff-input" inputMode="decimal" value={blockPct} onChange={(e) => setBlockPct(e.target.value)} />}</Field>
      </div>
      {alertPct && (
        <Field label="Event Grid connection (optional)" hint="Empty reuses the connection Foundry created when an alert was first saved in the Foundry portal.">
          {(id) => <input id={id} className="ff-input" value={conn} onChange={(e) => setConn(e.target.value)} />}
        </Field>
      )}
      <Notice>A cost control has at most one alert and one Block action, and takes effect within about 15 minutes.</Notice>
    </Modal>
  );
}
