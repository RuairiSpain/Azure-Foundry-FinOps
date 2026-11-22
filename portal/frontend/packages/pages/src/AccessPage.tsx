import { Badge, Card, Notice, PageHead, Table } from "@foundry-finops/ui";
import { Section } from "./common";
import { useLoad, usePortal } from "./context";

/** Access help: what each page reads from Azure, and which pages your portal access covers. */
export function AccessPage() {
  const { api, me } = usePortal();
  const pages = useLoad(() => api.accessPages(), []);
  return (
    <>
      <PageHead title="Access help" description="If a card shows an error, the permission it needs is listed here." />
      <Card title="You">
        <p>
          Signed in as <strong>{me.name}</strong> ({me.upn}). Portal role: <Badge tone="accent">{me.role}</Badge>.
          {me.account_level ? " Your access covers the whole Foundry resource." : ` Your access is limited to: ${(me.visible_projects ?? []).join(", ") || "nothing yet"}.`}
        </p>
        {me.mode === "entra" && <Notice>Azure shows each person only what their own Azure roles allow, and the portal can narrow that further but never widen it.</Notice>}
      </Card>
      <div style={{ height: 14 }} />
      <Card title="Permissions for each page">
        <Section load={pages}>
          {(list) => (
            <Table rows={list} rowKey={(p) => p.page}
              columns={[
                { key: "p", header: "Page", render: (p) => <strong>{p.page}</strong> },
                { key: "a", header: "Azure permission", render: (p) => p.azure_permissions.map((x) => <div key={x}><code>{x}</code></div>) },
                { key: "o", header: "Your portal access", render: (p) => <>{p.portal_access ? <Badge tone="ok">Allowed</Badge> : <Badge tone="warn">Limited</Badge>}{p.message && <div className="ff-muted">{p.message}</div>}</> },
              ]} />
          )}
        </Section>
      </Card>
    </>
  );
}
