/** How the portal knows who you are. Replace this to plug in your own sign-in. */

export interface AccountInfo {
  name: string;
  upn: string;
}

export interface AuthProvider {
  readonly kind: string;
  /** Headers to send with an API request (an access token, or the demo user). Rejects if nobody is signed in. */
  getAuthHeaders(): Promise<Record<string, string>>;
  isSignedIn(): boolean;
  account(): AccountInfo | null;
  signIn(hint?: string): Promise<void>;
  signOut(): Promise<void>;
  /** Called by the API client on a 401, so the app can show the sign-in page. */
  onSignedOut?: () => void;
}

const KEY = "foundry-finops-demo-user";

/** Demo mode: pick one of the demo people. Never use this against a real deployment. */
export class DemoAuth implements AuthProvider {
  readonly kind = "demo";
  onSignedOut?: () => void;
  private users: { oid: string; name: string; upn: string }[];

  constructor(users: { oid: string; name: string; upn: string }[] = [], private storage: Storage | null = safeStorage()) {
    this.users = users;
  }

  private oid(): string | null {
    return this.storage?.getItem(KEY) ?? null;
  }

  async getAuthHeaders(): Promise<Record<string, string>> {
    const oid = this.oid();
    if (!oid) throw new Error("Pick a demo user to sign in");
    return { "X-Demo-User": oid };
  }
  isSignedIn(): boolean {
    return this.oid() !== null;
  }
  account(): AccountInfo | null {
    const u = this.users.find((x) => x.oid === this.oid());
    return u ? { name: u.name, upn: u.upn } : null;
  }
  async signIn(oid?: string): Promise<void> {
    if (!oid) throw new Error("Choose a demo user");
    this.storage?.setItem(KEY, oid);
  }
  async signOut(): Promise<void> {
    this.storage?.removeItem(KEY);
    this.onSignedOut?.();
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}
