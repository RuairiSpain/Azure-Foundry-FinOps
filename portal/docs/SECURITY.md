# Security

| Topic | What the portal does |
|---|---|
| Sign-in | Entra ID, authorization code with PKCE. The backend validates RS256 signatures, issuer, audience and scope. |
| Authorization | Azure RBAC (through on-behalf-of reads) plus portal grants that only narrow access. |
| Writes | Need a portal write grant and the user's own Azure permission. Performed by the managed identity. Audited. |
| Secrets | Client secret and webhook secret are in Key Vault, read by Key Vault references. Nothing secret is in the template output. |
| Transport | HTTPS only, TLS 1.2, FTPS disabled. |
| Alert intake | Shared secret compared in constant time; events are de-duplicated. |
| Webhook targets | https only; names that resolve to private, loopback, link-local or reserved addresses are refused. |
| Read-only mode | `PORTAL_READ_ONLY=true` blocks every write. |
| Demo mode | Uses fixed sample users. Never enable it on a public host; the Bicep template always sets `entra`. |

## Roles for the managed identity

Give the identity the least it needs. The template assigns Reader, Cost Management Reader and Monitoring Reader on the
Foundry resource group. Add writer roles (`writerRoleDefinitionIds`) only if people should change cost controls,
budgets or tags through the portal.

## Rotating secrets

Create a new client secret (`az ad app credential reset`), update the Key Vault secret `portal-client-secret`, and
restart the app. Rotate the webhook secret the same way and redeploy the Event Grid subscription.

## Known residual risks

DNS rebinding for webhook targets; SQLite file on `/home` needs backups; the write design needs a security review.
