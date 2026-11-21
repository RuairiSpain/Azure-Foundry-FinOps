import { ApiClient, ApiError, DemoAuth, fetchConfig, type AuthProvider, type Me, type PortalConfig } from "@foundry-finops/api";
import { PortalProvider, type PageDefinition } from "@foundry-finops/pages";
import { AppShell, ErrorCard, Loading, ThemeToggle, type NavSection } from "@foundry-finops/ui";
import { useEffect, useMemo, useState } from "react";
import { BrowserRouter, NavLink, Navigate, Route, Routes } from "react-router-dom";
import { MsalAuth } from "./msalAuth";
import appConfig from "./portal.config";
import { SignIn } from "./SignIn";

type Boot =
  | { state: "loading" }
  | { state: "error"; error: unknown }
  | { state: "ready"; config: PortalConfig; auth: AuthProvider };

function navFor(pages: PageDefinition[], me: Me): { nav: NavSection[]; routes: PageDefinition[] } {
  const visible = pages.filter((p) => !p.visible || p.visible(me));
  const sections = new Map<string, NavSection>();
  for (const p of visible) {
    if (!p.section) continue;
    const s = sections.get(p.section) ?? { title: p.section, items: [] };
    s.items.push({ id: p.id, label: p.title, to: p.path.replace(/\/:.*$/, "") });
    sections.set(p.section, s);
  }
  return { nav: [...sections.values()], routes: visible };
}

export default function App() {
  const [boot, setBoot] = useState<Boot>({ state: "loading" });
  const [epoch, bump] = useState(0); // changes when the person signs in or out, so the profile is loaded again
  const [me, setMe] = useState<Me | null>(null);
  const [notAdded, setNotAdded] = useState<string | null>(null);

  useEffect(() => {
    for (const [k, v] of Object.entries(appConfig.theme)) document.documentElement.style.setProperty(k, v);
    (async () => {
      try {
        const config = await fetchConfig(appConfig.apiBaseUrl);
        let auth: AuthProvider;
        if (config.mode === "demo") auth = new DemoAuth(config.demo_users ?? []);
        else {
          const msal = new MsalAuth(config.msal!);
          await msal.init();
          auth = msal;
        }
        auth.onSignedOut = () => { setMe(null); bump((n) => n + 1); };
        setBoot({ state: "ready", config, auth });
      } catch (error) {
        setBoot({ state: "error", error });
      }
    })();
  }, []);

  const api = useMemo(
    () => (boot.state === "ready" ? new ApiClient({ auth: boot.auth, baseUrl: appConfig.apiBaseUrl }) : null),
    [boot],
  );

  useEffect(() => {
    if (boot.state !== "ready" || !api || !boot.auth.isSignedIn()) return;
    api.me().then(setMe, (e: unknown) => {
      if (e instanceof ApiError && e.status === 403) {
        setNotAdded(e.message);
        void boot.auth.signOut();
      } else if (!(e instanceof ApiError && e.status === 401)) setBoot({ state: "error", error: e });
    });
  }, [boot, api, epoch]);

  if (boot.state === "loading") return <Loading label="Starting…" />;
  if (boot.state === "error") return <div style={{ padding: 24 }}><ErrorCard error={boot.error} onRetry={() => location.reload()} /></div>;
  const brand = appConfig.brand ?? boot.config.brand;
  document.title = brand;

  if (!me || !api) {
    if (!boot.auth.isSignedIn() || notAdded) {
      return <SignIn config={boot.config} auth={boot.auth} brand={brand} message={notAdded} onDone={() => { setNotAdded(null); bump((n) => n + 1); }} />;
    }
    return <Loading label="Signing in…" />;
  }

  const { nav, routes } = navFor(appConfig.pages, me);
  const account = boot.auth.account();
  return (
    <BrowserRouter>
      <PortalProvider api={api} me={me}>
        <AppShell
          brand={brand}
          nav={nav}
          renderLink={(i) => <NavLink to={i.to}>{i.label}</NavLink>}
          user={{ name: account?.name ?? me.name, detail: `${me.upn} · ${me.role}` }}
          onSignOut={() => void boot.auth.signOut()}
          toolbar={<>{me.mode === "demo" && <span className="ff-badge warn">Demo data</span>}{me.read_only && <span className="ff-badge">Read-only</span>}<ThemeToggle /></>}
        >
          <Routes>
            <Route path="/" element={<Navigate to={nav[0]?.items[0]?.to ?? "/access"} replace />} />
            {routes.map((p) => <Route key={p.id} path={p.path} element={<p.component />} />)}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AppShell>
      </PortalProvider>
    </BrowserRouter>
  );
}
