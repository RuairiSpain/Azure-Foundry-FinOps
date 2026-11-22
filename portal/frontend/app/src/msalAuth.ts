import { InteractionRequiredAuthError, PublicClientApplication, type AccountInfo as MsalAccount } from "@azure/msal-browser";
import type { AccountInfo, AuthProvider, PortalConfig } from "@foundry-finops/api";

/** Sign-in with Microsoft Entra ID (authorization code flow with PKCE). The API checks the token it receives. */
export class MsalAuth implements AuthProvider {
  readonly kind = "msal";
  onSignedOut?: () => void;
  private app: PublicClientApplication;
  private scope: string;
  private current: MsalAccount | null = null;

  constructor(cfg: NonNullable<PortalConfig["msal"]>) {
    this.scope = cfg.scope;
    this.app = new PublicClientApplication({
      auth: { clientId: cfg.client_id, authority: cfg.authority, redirectUri: window.location.origin },
      cache: { cacheLocation: "sessionStorage" },
    });
  }

  /** Call once at start-up: finishes a sign-in redirect and picks up an existing session. */
  async init(): Promise<void> {
    await this.app.initialize();
    const result = await this.app.handleRedirectPromise();
    this.current = result?.account ?? this.app.getAllAccounts()[0] ?? null;
    if (this.current) this.app.setActiveAccount(this.current);
  }

  async getAuthHeaders(): Promise<Record<string, string>> {
    if (!this.current) throw new Error("Sign in required");
    try {
      const r = await this.app.acquireTokenSilent({ scopes: [this.scope], account: this.current });
      return { Authorization: `Bearer ${r.accessToken}` };
    } catch (e) {
      if (e instanceof InteractionRequiredAuthError) {
        await this.app.acquireTokenRedirect({ scopes: [this.scope], account: this.current });
      }
      throw e;
    }
  }
  isSignedIn(): boolean {
    return this.current !== null;
  }
  account(): AccountInfo | null {
    return this.current ? { name: this.current.name ?? this.current.username, upn: this.current.username } : null;
  }
  async signIn(): Promise<void> {
    await this.app.loginRedirect({ scopes: [this.scope] });
  }
  async signOut(): Promise<void> {
    const account = this.current;
    this.current = null;
    this.onSignedOut?.();
    await this.app.logoutRedirect({ account: account ?? undefined });
  }
}
