import type { NotificationRule, NotificationRuleInput } from "@foundry-finops/api";
import { Badge, Button, Card, Field, Modal, Notice, PageHead, Table, formatDateTime } from "@foundry-finops/ui";
import { useState } from "react";
import { Notes, Section } from "./common";
import { useLoad, usePortal } from "./context";

const TRIGGERS = { event: "A cost control alert arrives", percent_used: "A cost control passes a percentage", state: "A cost control is near or at its limit" };

/** Notifications: who hears when a cost control alerts, and the alerts received so far. */
export function NotificationsPage() {
  const { api, me } = usePortal();
  const rules = useLoad(() => api.notificationRules(), []);
  const events = useLoad(() => api.alertEvents(), []);
  const [form, setForm] = useState<{ rule?: NotificationRule } | null>(null);
  const [message, setMessage] = useState<{ tone: "info" | "bad"; text: string } | null>(null);
  const canEdit = me.role !== "viewer" && !me.read_only;

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setMessage(null);
    try { await fn(); if (ok) setMessage({ tone: "info", text: ok }); rules.reload(); events.reload(); }
    catch (e) { setMessage({ tone: "bad", text: e instanceof Error ? e.message : String(e) }); }
  };

  return (
    <>
      <PageHead
        title="Notifications"
        description="Rules that send a message when a cost control alerts or passes a threshold. Alerts reach the portal through Event Grid."
        actions={
          <div className="ff-row">
            {me.role === "admin" && <Button onClick={() => run(async () => { const r = await api.evaluateNotifications(); setMessage({ tone: "info", text: `Sent ${r.sent} notification(s).` }); })}>Check thresholds now</Button>}
            {canEdit && <Button variant="primary" onClick={() => setForm({})}>New rule</Button>}
          </div>
        }
      />
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      <Card title="Rules">
        <Section load={rules}>
          {(list) => (
            <Table rows={list} rowKey={(r) => r.id} empty="No notification rules in your scope."
              columns={[
                { key: "n", header: "Name", render: (r) => <strong>{r.name}</strong> },
                { key: "s", header: "Scope", render: (r) => (r.scope_type === "all" ? "Everything" : `${r.scope_type.replace("_", " ")}: ${r.scope_value}`) },
                { key: "t", header: "When", render: (r) => (r.trigger === "percent_used" ? `Usage reaches ${r.threshold}%` : TRIGGERS[r.trigger]) },
                { key: "c", header: "Send to", render: (r) => <>{r.channel}{r.target ? ` · ${r.target.length > 40 ? r.target.slice(0, 40) + "…" : r.target}` : ""}</> },
                { key: "e", header: "State", render: (r) => <Badge tone={r.enabled ? "ok" : "neutral"}>{r.enabled ? "On" : "Off"}</Badge> },
                { key: "f", header: "Last sent", render: (r) => formatDateTime(r.last_fired_at) },
                ...(canEdit ? [{ key: "a", header: "", render: (r: NotificationRule) => (
                  <div className="ff-row">
                    <Button small onClick={() => setForm({ rule: r })}>Edit</Button>
                    <Button small variant="danger" onClick={() => window.confirm(`Delete ${r.name}?`) && run(() => api.deleteNotificationRule(r.id))}>Delete</Button>
                  </div>
                ) }] : []),
              ]} />
          )}
        </Section>
      </Card>
      <div style={{ height: 14 }} />
      <Card title="Alerts received">
        <Section load={events}>
          {(list) => (
            <Table rows={list} rowKey={(e) => e.id} empty="No alert has arrived yet. Point an Event Grid subscription at /api/events/cost-control-alert."
              columns={[
                { key: "w", header: "Received", render: (e) => formatDateTime(e.received_at) },
                { key: "p", header: "Project", render: (e) => e.project || "–" },
                { key: "a", header: "Agent", render: (e) => e.agent || "–" },
                { key: "t", header: "Threshold", render: (e) => e.threshold },
                { key: "u", header: "Used", numeric: true, render: (e) => (e.total_consumed != null && e.limit_value != null ? `${e.total_consumed} of ${e.limit_value} ${e.unit}` : "–") },
                { key: "n", header: "Notified", render: (e) => <Badge tone={e.notified ? "ok" : "neutral"}>{e.notified ? "Yes" : "No rule matched"}</Badge> },
              ]} />
          )}
        </Section>
        <Notes items={["Foundry sends one event per request while usage is over a threshold. The portal keeps the first for each threshold and period."]} />
      </Card>
      {form && <RuleForm rule={form.rule} onClose={() => setForm(null)} onSaved={() => { setForm(null); rules.reload(); }} />}
    </>
  );
}

function RuleForm({ rule, onClose, onSaved }: { rule?: NotificationRule; onClose: () => void; onSaved: () => void }) {
  const { api, me } = usePortal();
  const projects = useLoad(() => api.projects(), []);
  const [v, setV] = useState<NotificationRuleInput>(rule ?? { name: "", scope_type: me.role === "admin" ? "all" : "project", scope_value: "", trigger: "event", threshold: 80, channel: "log", target: "", enabled: true });
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof NotificationRuleInput>(k: K, val: NotificationRuleInput[K]) => setV((x) => ({ ...x, [k]: val }));
  const save = async () => {
    try { await api.saveNotificationRule(v, rule?.id); onSaved(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <Modal title={rule ? `Edit ${rule.name}` : "New notification rule"} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" onClick={save}>Save</Button></>}>
      {error && <Notice tone="bad">{error}</Notice>}
      <Field label="Name">{(id) => <input id={id} className="ff-input" value={v.name} onChange={(e) => set("name", e.target.value)} />}</Field>
      <div className="ff-row">
        <Field label="Applies to">{(id) => (
          <select id={id} className="ff-select" value={v.scope_type} onChange={(e) => set("scope_type", e.target.value as NotificationRuleInput["scope_type"])}>
            {me.role === "admin" && <option value="all">Everything</option>}
            <option value="project">A project</option>
            <option value="cost_centre">A cost centre</option>
          </select>
        )}</Field>
        {v.scope_type !== "all" && (
          <Field label={v.scope_type === "project" ? "Project" : "Cost centre code"}>{(id) => v.scope_type === "project" ? (
            <select id={id} className="ff-select" value={v.scope_value} onChange={(e) => set("scope_value", e.target.value)}>
              <option value="">Choose…</option>
              {(projects.data ?? []).map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
            </select>
          ) : <input id={id} className="ff-input" value={v.scope_value} onChange={(e) => set("scope_value", e.target.value)} />}</Field>
        )}
      </div>
      <Field label="When">{(id) => (
        <select id={id} className="ff-select" value={v.trigger} onChange={(e) => set("trigger", e.target.value as NotificationRuleInput["trigger"])}>
          {Object.entries(TRIGGERS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>
      )}</Field>
      {v.trigger === "percent_used" && <Field label="Percentage">{(id) => <input id={id} className="ff-input" inputMode="decimal" value={v.threshold ?? ""} onChange={(e) => set("threshold", e.target.value ? Number(e.target.value) : null)} />}</Field>}
      <div className="ff-row">
        <Field label="Send by">{(id) => (
          <select id={id} className="ff-select" value={v.channel} onChange={(e) => set("channel", e.target.value as NotificationRuleInput["channel"])}>
            <option value="log">Portal log only</option><option value="webhook">Webhook (Teams, Slack, …)</option><option value="email">Email</option>
          </select>
        )}</Field>
        {v.channel !== "log" && (
          <Field label={v.channel === "email" ? "Email address" : "Webhook URL (https)"}>{(id) => <input id={id} className="ff-input" value={v.target} onChange={(e) => set("target", e.target.value)} />}</Field>
        )}
      </div>
      <Field label="Enabled">{(id) => <input id={id} type="checkbox" checked={v.enabled} onChange={(e) => set("enabled", e.target.checked)} />}</Field>
    </Modal>
  );
}
