# Setting up `foundry-finops download`

`foundry-finops download` runs every read query the SDK offers for one Foundry resource (optionally one project) over a
date range and saves each result as a JSON file:

```
FinOps/
  2026-11-21/                 <- the day you ran it
    cost_by_project.json      <- one file per query, named after the query
    cost_by_agent.json
    budgets.json
    ...
    _manifest.json            <- what succeeded, was skipped or failed
```

It only reads from Azure and writes local files. Nothing in Azure is changed.

## 1. Prerequisites

* Python 3.10 or newer.
* An Azure account that can sign in with `az login` (or a service principal / managed identity, see step 3).
* The roles in step 4 on the subscription that holds the Foundry resource.
* The public Azure cloud. The command line has no sovereign-cloud option (the Python SDK does).

## 2. Install

The package is not on PyPI yet, so install it from a clone of this repository:

```bash
git clone https://github.com/RuairiSpain/Azure-Foundry-FinOps.git
cd Azure-Foundry-FinOps
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install ./foundry-finops
foundry-finops download --list                         # prints the query names; proves the install works
```

## 3. Sign in

The command uses `DefaultAzureCredential`, which tries these in order, so use whichever fits:

| Where you run it | How to sign in |
|---|---|
| Your laptop | `az login` (and `az account set --subscription <id>` if you have several) |
| CI or a server with a service principal | Set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` and `AZURE_CLIENT_SECRET` |
| An Azure VM, container or Function | Assign a managed identity and grant it the roles in step 4 |

If sign-in fails the command stops straight away with a hint rather than failing every query.

## 4. Roles

The signed-in identity needs read access. The shortest set is **Reader + Cost Management Reader + Monitoring Reader**
on the subscription, plus **Log Analytics Reader** for the KQL files. The per-file table is in the
[README](../README.md#roles-needed-by-foundry-finops-download).

A query without its role fails on its own (HTTP 403 in `_manifest.json`) and the others still download, so you can
start with a partial grant.

Cost data also depends on your billing arrangement: Enterprise Agreements need the enrollment's "view charges"
settings, Microsoft Customer Agreements need the billing profile to allow it, and CSP subscriptions need the partner to
enable cost visibility.

## 5. Find the values you need

| Value | Where to find it | Flag | Environment variable |
|---|---|---|---|
| Subscription ID | `az account show --query id -o tsv` | `--subscription` | `FINOPS_SUBSCRIPTION_ID` |
| Resource group (required) | Portal, or `az cognitiveservices account list -o table` | `-g` / `--resource-group` | `FINOPS_RESOURCE_GROUP` |
| Foundry resource name (required) | Same command; the `AIServices` account | `--account` | `FINOPS_FOUNDRY_ACCOUNT` |
| Project name (optional) | Foundry portal, or `foundry-finops ... download --only foundry_projects` | `--project` | n/a |
| Region for quota and prices | The Foundry resource's region (default `eastus2`) | `--location` | `FINOPS_LOCATION` |
| Logs target (optional) | A Log Analytics **workspace ID** (a GUID), or an Application Insights **resource ID** | `--logs-target` | `FINOPS_LOGS_TARGET` |
| API Management name (optional) | The APIM instance used as your AI gateway | `--apim-service` | `FINOPS_APIM_SERVICE` |
| Team tag name | The tag that holds your team name (default `team`) | n/a | `FINOPS_TEAM_TAG` |

Setting the environment variables once keeps the command short:

```bash
export FINOPS_SUBSCRIPTION_ID=<subscription-id>
export FINOPS_RESOURCE_GROUP=rg-ai
export FINOPS_FOUNDRY_ACCOUNT=contoso-foundry
```

```powershell
$env:FINOPS_SUBSCRIPTION_ID = "<subscription-id>"
$env:FINOPS_RESOURCE_GROUP = "rg-ai"
$env:FINOPS_FOUNDRY_ACCOUNT = "contoso-foundry"
```

## 6. Run it

Everything for one project, January to March 2026:

```bash
foundry-finops download --project fraud-risk --start 2026-01-01 --end 2026-03-31
```

The whole Foundry resource (all projects), with the KQL and gateway files included:

```bash
foundry-finops download --start 2026-01-01 --end 2026-03-31 \
  --logs-target <workspace-id-or-app-insights-resource-id> --apim-service contoso-ai-gateway
```

Only some queries, written somewhere else:

```bash
foundry-finops download --start 2026-03-01 --end 2026-03-31 --only cost_by_agent,budgets --output-dir ~/reports
```

| Option | Meaning |
|---|---|
| `--start`, `--end` | First and last day, inclusive, `YYYY-MM-DD`. Required. |
| `--project` | Filter cost data to one Foundry project. Omit for all projects. |
| `--only a,b` | Run only these queries. Names come from `--list`. |
| `--roles "A,B"` | The identity's Azure roles, comma-separated, or `auto` to read them from Azure. Only the queries those roles allow are run (see below). Default: try everything. |
| `--list` | Print the query names and exit. With `--roles`, each line starts with `run` or `skip`, so it is a dry run (`--roles auto` signs in to Azure to read the roles). |
| `--output-dir` | Where the `FinOps` folder is created. Default: the current directory. |

The global options `--subscription` and `-g` go **before** `download`.

**Limiting the download to what your roles allow.** Without `--roles` every query is tried, and a query you aren't
allowed to run simply fails with a 403. If you know the roles, pass them and the rest are skipped without calling Azure:

```bash
foundry-finops download --start 2026-01-01 --end 2026-03-31 --roles "Reader,Cost Management Reader,Monitoring Reader"
foundry-finops download --list --roles "Monitoring Reader"        # shows which queries would run (dry run)
```

* Role names are the Azure built-in names, matched without regard to case. Quote the list because names contain spaces.
* Skipped queries appear in `_manifest.json` as `skipped` with the roles that would allow them, and the roles you gave
  are recorded under `parameters`.
* **Owner, Contributor and Reader** allow every query (their `*/read` permission covers them). The narrower roles allow
  their own area: **Cost Management Reader/Contributor** for the `cost_*` files and `budgets`; **Monitoring
  Reader/Contributor** for `token_usage_*`, `metric_definitions` and `alerts`; **Log Analytics Reader/Contributor**
  (and Monitoring roles) for `kql_*`; **Cognitive Services Contributor** or the Azure AI account/owner roles for the
  Foundry files; the **API Management Service** reader/contributor/operator roles for `apim_gateway`.
  `retail_price_sheet` needs no role and always runs.
* The command assumes the roles are held at a scope that covers the resource (the subscription for `quota_*`,
  `foundry_event_grid_topics` and the tag inventory). It does not look up the real assignments, so it can't know if
  a role is held only on a resource group. Anything still refused by Azure is recorded as `failed` with the 403.
* A role name the tool doesn't know, including a custom role, is reported as a warning and ignored. For custom roles
  use `--roles auto`, which works from permissions instead of names.

**Reading the roles from Azure: `--roles auto`.** Instead of typing roles, let the command look them up:

```bash
foundry-finops download --start 2026-01-01 --end 2026-03-31 --roles auto
foundry-finops download --list --roles auto          # dry run: which queries would run
```

It reads the signed-in identity's role assignments (including roles inherited through groups) and the permissions of
each role, then runs a query only if those permissions allow the actions it needs **at the scope it runs at**. A Monitoring
Reader assignment on the resource group allows the token metrics for the Foundry resource inside it, but a Reader
assignment on a different resource group allows nothing. This is more precise than `--roles "..."`: it handles custom
roles, `notActions`, and scope. The roles found are printed and saved in `_manifest.json` under `discovered_roles`.

* The identity needs `Microsoft.Authorization/roleAssignments/read` and `roleDefinitions/read`. **Reader** has both. If
  they are missing the command stops with a message and you can pass `--roles "..."` yourself.
* The identity is taken from the `oid` claim of its own access token, so it works for users, service principals and
  managed identities.
* It is a pre-check, not a security boundary: Azure still decides. It does not model deny assignments, conditional
  (ABAC) assignments, or Privileged Identity Management roles that are eligible but not yet activated; those queries
  are still recorded as `failed` with Azure's 403 if the guess is wrong. The action names each query needs are in
  `foundry_finops/roles.py` and are my reading of the Azure resource provider operations, so please report any that are
  wrong.

Running it twice on the same day overwrites that day's files. Running it on another day creates a new dated folder.

## 7. What you get

Each file wraps its data with the details of the run:

```json
{
  "query": "cost_by_project",
  "description": "Billed cost per day by Foundry project (billing tag `project`)",
  "generated_at": "2026-11-21T09:12:08Z",
  "parameters": { "account": "contoso-foundry", "project": "fraud-risk", "start": "2026-01-01", "end": "2026-03-31" },
  "notes": [],
  "data": [
    { "Cost": 41.27, "UsageDate": "2026-01-05", "TagValue": "fraud-risk", "Currency": "USD" }
  ]
}
```

`_manifest.json` lists every query with `status` of `ok`, `skipped` (with the reason) or `failed` (with the error), the
row count and the time taken. Check it after each run: a missing file always has an entry explaining why.

The command prints the same summary as a table and exits with **0** if nothing failed, **1** if any query failed, and
**2** for invalid arguments. Skipped queries are not failures.

The folder contains billing data and is in this repository's `.gitignore`. Keep it out of source control.

## 8. Things to know about the data

* **Billed cost lags usage by 8 to 24 hours.** The last day or two of a range may be missing or partial.
* **Project cost comes from Foundry's `project` billing tag.** Spend without that tag, such as other resources in the
  group, does not appear in the project filter.
* **Ranges longer than a year** are fetched in several windows and merged automatically.
* **Metrics keep 93 days.** An older start date is clamped; the file's `notes` says so. Metrics, quota, deployments and
  prices exist per Foundry resource or region, not per project, and the notes say that too.
* **Forecast** only runs when the end date is in the future.
* **Cost-control usage is an estimate** rebuilt from billed cost and token metrics, because Foundry has no usage API for
  cost controls. Each row's `usage_note` explains how it was calculated.
* **Throttling:** Cost Management limits request rates. The command waits and retries on its own, so a large run can
  take a few minutes.

## 9. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `error: HTTP 401: Could not get an access token` | Not signed in. Run `az login`, or set the `AZURE_*` variables. |
| `--resource-group ... is required` or `--account ... is required` | Pass them, or set `FINOPS_RESOURCE_GROUP` and `FINOPS_FOUNDRY_ACCOUNT`. |
| A query shows `failed` with HTTP 403 | The identity lacks the role for that query. See step 4 and the README table. |
| `cost_*` files have no rows | No spend with that project tag in the range, the data is still within the 8 to 24 hour delay, or your billing type blocks cost access (step 4). |
| `foundry_account` fails with 404 | Wrong `--account` or resource group. Names are case-insensitive but must match exactly. |
| `quota_*` or `tags` fail but the rest work | Those calls need Reader on the whole subscription, not just the resource group. |
| `kql_*` or `apim_gateway` are `skipped` | Pass `--logs-target` or `--apim-service`. |
| `cost_controls` fails with 403 | Cost controls are a preview API. Add a Foundry role such as Foundry User on the resource. |
| A line break breaks the command in PowerShell | Use a backtick `` ` `` at the end of the line instead of `\`. |

## 10. Scheduling in GitHub Actions

`.github/workflows/finops-download.yml` runs the download every Monday at 05:17 UTC for the last 30 full days, and can
also be started by hand (Actions > finops-download > Run workflow) with your own start, end, project and query list.
It signs in to Azure with OpenID Connect, so no password or client secret is stored in GitHub.

**One-time setup**

1. Create an app registration (or user-assigned managed identity) and give it the roles from step 4 on the
   subscription.
2. Add a **federated credential** to it: issuer `https://token.actions.githubusercontent.com`, subject
   `repo:RuairiSpain/Azure-Foundry-FinOps:environment:finops` (change the owner and repo if you forked it).
3. In the repository go to Settings > Secrets and variables > Actions > **Variables** and add:

| Variable | Required | Value |
|---|---|---|
| `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | yes | From the app registration and subscription |
| `FINOPS_RESOURCE_GROUP`, `FINOPS_FOUNDRY_ACCOUNT` | yes | As in step 5 |
| `FINOPS_PROJECT` | no | Filter cost data to one project; leave out for all |
| `FINOPS_LOCATION`, `FINOPS_TEAM_TAG` | no | Default `eastus2` and `team` |
| `FINOPS_LOGS_TARGET`, `FINOPS_APIM_SERVICE` | no | Turn on the KQL and gateway files |
| `FINOPS_STORAGE_ACCOUNT`, `FINOPS_STORAGE_CONTAINER` | see below | Where to save the files (container default `finops`) |

4. Optional: in Settings > Environments > **finops**, add required reviewers if a person should approve each run.

**Where the files go.** The output is billing data, so the workflow refuses to keep it where it could be read publicly:

* **Private repository:** the `FinOps` folder is attached to the run as a workflow artifact for 30 days.
* **Azure Blob Storage** (works for public repositories too): set `FINOPS_STORAGE_ACCOUNT` and give the identity
  **Storage Blob Data Contributor** on the container. Files are uploaded as `<container>/<date>/<query>.json`.
* A **public** repository with no storage account fails at the first step with an explanation, before anything is
  downloaded.

Each run also writes a table of every query's status to the run summary. The job fails if any query failed (exit code 1),
after the files for the queries that worked have been saved. To change the schedule, edit the `cron` line.

