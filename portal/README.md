# Foundry FinOps portal

A web portal for the people who watch Foundry cost: fleet overview, cost by project, agent, deployment and tag, cost
controls, budgets, cost centres, and notifications. It is built on the [`foundry-finops`](../foundry-finops) SDK.

* **Backend** (`backend/`): Python, FastAPI, SQLite. Reads Azure with the signed-in user's token, so Azure RBAC decides
  what each person can see.
* **Frontend** (`frontend/`): React 18, TypeScript, Vite. Packages: `api` (typed client and auth), `ui` (theme and charts),
  `pages` (the pages), `app` (shell and configuration).
* **Deployment** (`deploy/`): Bicep for App Service, Key Vault, Application Insights and Event Grid, plus scripts.

## Try it without Azure

```bash
cd portal/frontend && npm ci && npm run build
cd ../backend && pip install -e ../../foundry-finops -e . 
PORTAL_MODE=demo PORTAL_STATIC_DIR=../frontend/app/dist python -m foundry_finops_portal
```

Open http://localhost:8000. Demo mode uses fixed sample data and four sample users: Alice (administrator), Bob (owner
of the `fraud-risk` project), Carol (viewer of cost centre CC-SUP) and Dave (not added, sees nothing). Nothing is
sent to Azure.

## Deploy to Azure

See [docs/DEPLOY.md](docs/DEPLOY.md). Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Security model:
[docs/SECURITY.md](docs/SECURITY.md). Changing pages, branding and behaviour: [docs/CUSTOMISING.md](docs/CUSTOMISING.md).

## Status

Tested with unit and API tests and with demo mode in a browser. **Not yet tested against a real tenant.** Items to
confirm there are listed in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#unverified).
