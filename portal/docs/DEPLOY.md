# Deploy to App Service

You need: an Azure subscription with a Foundry resource, rights to create resources and role assignments on the Foundry
resource group, rights to create an Entra app registration and grant admin consent, Azure CLI, Python 3.12 and Node 20.

## 1. Create the app registration

The redirect URI is the portal URL, which exists only after step 2. Create the registration with a placeholder, then
add the real URL.

```bash
./deploy/create-app-registration.sh https://localhost
```

It prints `portalClientId` and a client secret. The script requests delegated Azure Service Management
`user_impersonation` and Graph `User.Read.All` (application), and tries to grant admin consent.

## 2. Deploy the infrastructure

Copy `deploy/main.bicepparam`, fill in the values, then:

```bash
az group create -n rg-foundry-portal -l eastus2
az deployment group create -g rg-foundry-portal -f deploy/main.bicep -p deploy/main.bicepparam \
  -p portalClientSecret='<secret>'
```

Note the output `portalUrl` and add it as a single-page-application redirect URI on the app registration.

## 3. Deploy the code

```bash
./deploy/package.sh
az webapp deploy -g rg-foundry-portal -n <webAppName> --src-path deploy/portal.zip --type zip
```

Open the portal URL and sign in as one of `bootstrapAdmins`. Add the other people in the Admin page.

## 4. Send cost control alerts to the portal

Deploy again with `createEventSubscription=true`. Event Grid checks the endpoint when the subscription is created, so
the code from step 3 must already be running. Point your Foundry cost controls' notification at the Event Grid topic
named in the `eventGridTopicName` output.

## Optional: split cost by who used it

The usage split (`GET /api/cost-centres/usage-split`) reads per-caller usage from Log Analytics. Set these App Service
application settings by hand; the Bicep template does not set them yet.

| Setting | Value |
|---|---|
| `PORTAL_LOGS_TARGET` | The Log Analytics workspace ID (or workspace resource ID) that holds the gateway's records. |
| `PORTAL_USAGE_SOURCE` | `gateway` (default; the API Management policy in `foundry-finops/examples/apim`, **experimental**: weighted by tokens) or `client` (`llm.usage` records from the SDK; weighted by estimated cost). |

The portal's identity needs the Log Analytics Reader role on that workspace, and `User.Read.All` (already needed for the
directory check) so it can read each caller's department. Without `PORTAL_LOGS_TARGET` the endpoint answers 501 and says
so. The `gateway` source needs one workspace that holds both the Application Insights traces and the gateway's LLM log.

## Parameters you will change

| Parameter | Use |
|---|---|
| `appServiceSkuName` | `P0v3` or larger for production, `B1` for a trial. Keep one instance. |
| `writerRoleDefinitionIds` | Roles the managed identity needs for portal writes. Empty makes the portal effectively read-only. |
| `readOnly` | Block every change. |
| `brandName`, `tags`, `namePrefix` | Naming and branding. |
| `apimService` | Include API Management in the cost views. |

## Operating notes

* Back up `/home/data/portal.db` (for example a nightly copy to a storage account).
* Do not scale out; SQLite is a single-writer database.
* Health check: `/api/health`.
