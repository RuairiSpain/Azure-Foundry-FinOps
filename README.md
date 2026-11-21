# Azure-Foundry-FinOps

Foundry FinOps, token limits, chargeback, budgeting, usage, costs per project/agent/tag. Tracing agent call has token usage. Calls to APIM to get token usage. Connects to Azure retails prices for cost calculation.

| Project | Purpose | Start here |
|---|---|---|
| `foundry-finops/` | Publishable Python package (`pip install foundry-finops`) wrapping the REST APIs for Foundry cost controls, Cost Management, Azure Monitor, Log Analytics, quota, tags, alerts and API Management AI-gateway policies, plus OpenTelemetry usage tracking | `foundry-finops/README.md` |
| `foundry-finops/examples/dashboard/` | Example web dashboard in the style of the Azure portal and Log Analytics, with CRUD pages, charts and downloads | `python foundry-finops/examples/dashboard/server.py` |
| `foundry-finops-cowork/` | Separate project: MCP connector (Entra sign-in + on-behalf-of) and a Cowork plugin with report and governance skills | `foundry-finops-cowork/docs/SETUP.md` |

**Start with the [usage guide](foundry-finops/docs/USAGE.md)**: install the package, import it, and run every query type from
Python and from the command line. The [download setup guide](foundry-finops/docs/SETUP.md) covers the bulk JSON download.

Everything runs in demo mode without Azure (illustrative data). Tests use only the standard library plus `requests`,
`PyJWT` and `cryptography`:

```bash
cd foundry-finops && PYTHONPATH=src python -m unittest discover -s tests -t .
cd ../foundry-finops-cowork && PYTHONPATH=.:../foundry-finops/src python -m unittest discover -s tests -t .
```
