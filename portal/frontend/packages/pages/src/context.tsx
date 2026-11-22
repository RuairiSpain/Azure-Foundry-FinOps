import type { ApiClient, Me } from "@foundry-finops/api";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export interface PortalContextValue {
  /** Anything shaped like ApiClient works, so you can wrap or replace it. */
  api: ApiClient;
  me: Me;
}

const Ctx = createContext<PortalContextValue | null>(null);

export function PortalProvider({ api, me, children }: PortalContextValue & { children: ReactNode }) {
  return <Ctx.Provider value={{ api, me }}>{children}</Ctx.Provider>;
}

export function usePortal(): PortalContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("Wrap the page in <PortalProvider api={...} me={...}>");
  return v;
}

export interface Loaded<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

/** Load something when the page opens or `deps` change. The latest request wins, so a slow old answer can't overwrite a new one. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]): Loaded<T> {
  const [state, setState] = useState<{ data: T | null; error: unknown; loading: boolean }>({ data: null, error: null, loading: true });
  const seq = useRef(0);
  const [tick, setTick] = useState(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, deps);
  useEffect(() => {
    const mine = ++seq.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    run().then(
      (data) => mine === seq.current && setState({ data, error: null, loading: false }),
      (error: unknown) => mine === seq.current && setState((s) => ({ data: s.data, error, loading: false })),
    );
  }, [run, tick]);
  return { ...state, reload: () => setTick((t) => t + 1) };
}

/** Show a loaded value, its loading state or its own error. */
export function isReady<T>(l: Loaded<T>): l is Loaded<T> & { data: T } {
  return l.data !== null;
}
