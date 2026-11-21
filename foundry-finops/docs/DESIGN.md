# Design: fitting the Microsoft SDKs

The goal is that a team can start on the Foundry cost features with this package and later move to the Microsoft SDKs
with the smallest possible change. This document records what the Microsoft SDKs look like (checked against the
published packages and the GitHub source on 21 November 2026), the decisions that follow, and what was left out.

## 1. What Microsoft ships today

### The management client already has cost controls

`azure-mgmt-cognitiveservices` **15.1.0b1** was published on 17 November 2026 (changelog entry dated 16 November). It adds
the operation groups `account_cost_controls` and `project_cost_controls`, the models `AccountCostControl`,
`ProjectCostControl`, `CostControlProperties`, `CostControlFilter`, `CostControlNotification`, `CostControlAction` and
their patch types, the enums, and the property `AccountProperties.cost_control_connections`. The Learn page for creating
cost controls still says the management libraries "don't include cost controls for this API version yet"; the
package on PyPI is ahead of that sentence.

Signatures, read from the installed package:

```text
CognitiveServicesManagementClient(credential, subscription_id, base_url=None, *, cloud_setting=None, **kwargs)

account_cost_controls.list(resource_group_name, account_name)                         -> ItemPaged[AccountCostControl]
account_cost_controls.get(resource_group_name, account_name, cost_control_name)       -> AccountCostControl
account_cost_controls.create_or_update(rg, account, name, resource, *, etag=None, match_condition=None)
account_cost_controls.update(rg, account, name, properties: CostControlPatch, *, etag=None, match_condition=None)
account_cost_controls.delete(rg, account, name, *, etag=None, match_condition=None)   -> None
account_cost_controls.check_existence(rg, account, name)                              -> bool
project_cost_controls.<same>                                                          (project_name after account_name)
```

These are the same operations the OpenAPI definition (`2026-11-15-preview`) describes: `GET`, `PUT`, `PATCH`,
`DELETE` and `HEAD` on `.../costControls/{name}`. Wire format: this package's spec and the Microsoft models produce an
identical request (`tests/test_official_parity.py`).

### The house style

* **Management plane** (`azure-mgmt-*`): `Client(credential, subscription_id, base_url=None, *, cloud_setting=None,
  **kwargs)`; one attribute per operation group (snake_case, plural); every operation takes the resource group first;
  `list` returns `ItemPaged`; long-running work is `begin_*` returning `LROPoller`; conditional writes use `etag=` with
  `azure.core.MatchConditions`; errors are `azure.core.exceptions`; models are keyword-constructable, expose
  `as_dict()` and keep the REST layout under `properties`; one `api_version` per client; a `_patch.py` per package for
  hand-written changes; `aio` mirrors; `py.typed`; a `VERSION` constant.
* **Foundry data plane** (`azure-ai-projects` 2.x): `AIProjectClient(endpoint, credential, allow_preview=None, **kwargs)`
  with operation groups as attributes (`agents`, `connections`, `deployments`, `datasets`, `indexes`, `evaluators`,
  `toolboxes`) and a `beta` namespace for preview operations.

Cost controls are Azure Resource Manager resources (`Microsoft.CognitiveServices/accounts[/projects]/costControls`), so
the management client is their home. `azure-ai-projects` has no cost operations.

### Where the other FinOps calls live

Each of the FinOps groups in this package has a Microsoft package with the same job (Cost Management, Consumption,
Monitor, Resource Manager, API Management, Authorization, Policy Insights, Event Grid, `azure-monitor-query`). Two things
have none: the public Retail Prices API, and edits to API Management policy XML for token limits. The full list is
`foundry_finops.migration.MAP`, and each entry is resolved against the installed package by a test.

## 2. Decisions

1. **`FinOpsClient` subclasses `CognitiveServicesManagementClient`.** The constructor starts with the same arguments, in
   the same order. Every official group is therefore on our client with the Microsoft signatures. This is the strongest
   guarantee available: for those calls there is nothing to translate, because they are not ours.
2. **Helpers take the client first and use only official groups** (`foundry_finops.cost_controls`,
   `foundry_finops.resources`). They run on a plain `CognitiveServicesManagementClient`, so they survive the move and
   the code around them does not change. A test runs them on the plain client.
3. **Models are the Microsoft models.** `CostControl` is not an SDK model. It is an authoring spec (flat fields,
   offline validation of the service's rules, `to_resource()` and `from_resource()`), and it is optional. The old read
   dataclasses are renamed `DeploymentView`, `AccountView`, `ProjectView` and `QuotaView` so they cannot be mistaken for
   the Microsoft models; they are built from `as_dict()` of the Microsoft models.
4. **Errors are azure-core errors.** Our classes subclass `azure.core.exceptions` (`ResourceNotFoundError`,
   `ResourceExistsError`, `ResourceModifiedError`, `ClientAuthenticationError`, `HttpResponseError`), so one `except`
   works before and after, and for both kinds of group.
5. **Credentials are `azure.core.credentials.TokenCredential`**, the same type every Microsoft client takes. Cloud
   selection is `cloud_setting=AzureClouds.…`.
6. **Remove, don't alias.** The 0.1 groups `foundry`, `cost_controls` and `quota` are gone rather than kept as
   wrappers. Wrappers would have to stay in step with the Microsoft ones and would hide the real call. The change is
   documented call by call in [MIGRATION.md](MIGRATION.md).
7. **Name the Microsoft equivalent for everything that stays.** `migration.MAP` is data, printed by
   `foundry-finops migration-map`, and checked by a test against the installed Microsoft packages.
8. **Keep the pre-release dependency visible.** `azure-mgmt-cognitiveservices>=15.1.0b1,<16` is pinned in a range, and
   CI runs the parity tests weekly against the newest release, so a renamed model fails here before it fails a user.

## 3. What is the same as the Microsoft SDK, and what is not

| | Same | Different |
|---|---|---|
| Constructor | `(credential, subscription_id, base_url=None, *, cloud_setting=None, **kwargs)` | extra keyword arguments: `resource_group`, `api_versions`, `retry`, `timeout`, `session` |
| Cost controls, Foundry resources, quota | everything: groups, arguments, models, paging, errors | none |
| FinOps groups (`cost`, `metrics`, ...) | azure-core credentials and errors; iterables that follow paging | their own transport (retries with `Retry-After`, never retrying a cost control block) and their own models |
| Resource group | official groups take it first | the FinOps groups default it from the client |

## 4. Deliberately not done

* **Async.** The Microsoft package has `aio`. An async `FinOpsClient` over the official groups is straightforward, but the
  helpers and the FinOps groups are synchronous, so it is left for a later release rather than half-built.
* **The generated layout** (`_patch.py`, a split `models/` package). This package is hand-written, so the structure that
  exists to host generated code adds nothing.
* **Moving the FinOps groups onto the azure-core pipeline.** They share a transport with retry rules the Microsoft
  pipeline doesn't have (the cost control block is a 429 that must not be retried), and their REST calls are covered by
  tests today. Each group has a Microsoft equivalent listed in the map for the day that is worth doing.
* **`AIProjectClient`-style `endpoint` constructors.** Cost controls have no data-plane endpoint.
* **A guess about the GA surface.** Everything above is what the beta ships. If the GA package changes a name, the
  parity tests fail and the lower bound is raised; no name here is invented.

## 5. Known behaviours of the beta that the helpers hide

* `check_existence` raises `ResourceNotFoundError` for a missing resource; `cost_controls.exists` returns `False`.
* `FeatureNotRegistered` (403) is not explained; `cost_controls.upsert` adds the registration command.
* A PATCH delete of a named alert or block needs `None` as a dict value, which the models send as `null`.
* Clearing `costControlConnections` needs an explicit `null`, which a model field cannot carry; the helper sends JSON.
