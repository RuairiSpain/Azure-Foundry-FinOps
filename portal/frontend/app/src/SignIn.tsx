import type { AuthProvider, PortalConfig } from "@foundry-finops/api";
import { Button, Card, Notice, ThemeToggle } from "@foundry-finops/ui";
import { useState } from "react";

export function SignIn({ config, auth, brand, onDone, message }: {
  config: PortalConfig;
  auth: AuthProvider;
  brand: string;
  onDone: () => void;
  message?: string | null;
}) {
  const [error, setError] = useState<string | null>(null);
  const go = async (oid?: string) => {
    try { await auth.signIn(oid); onDone(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  return (
    <div className="ff-signin">
      <div className="ff-row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <span className="ff-brand"><span className="ff-brand-mark" aria-hidden />{brand}</span>
        <ThemeToggle />
      </div>
      <Card title="Sign in">
        {message && <Notice tone="warn">{message}</Notice>}
        {error && <Notice tone="bad">{error}</Notice>}
        {config.mode === "demo" ? (
          <>
            <Notice>Demo mode: the data is illustrative and nobody is really signed in. Pick a person to see what their access looks like.</Notice>
            <div className="ff-people">
              {(config.demo_users ?? []).map((u) => (
                <button key={u.oid} onClick={() => go(u.oid)}><strong>{u.name}</strong><div className="ff-muted">{u.upn}</div></button>
              ))}
            </div>
          </>
        ) : (
          <>
            <p>Use your work account. What you see depends on your Azure permissions and on the access a portal administrator gave you.</p>
            <Button variant="primary" onClick={() => go()}>Sign in with Microsoft</Button>
          </>
        )}
      </Card>
    </div>
  );
}
