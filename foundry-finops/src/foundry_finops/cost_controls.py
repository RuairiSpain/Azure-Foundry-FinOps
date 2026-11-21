"""Helpers for Foundry cost controls that work with the **official** Microsoft models and client.

Create, read, update and delete are not in this package: they are the operation groups
``client.account_cost_controls`` and ``client.project_cost_controls`` of
``azure.mgmt.cognitiveservices`` (version 15.1.0b1 and later), with the same signatures and models whether you use
:class:`foundry_finops.FinOpsClient` or ``CognitiveServicesManagementClient``.

What is here has no Microsoft equivalent. Every function takes the client as its first argument and uses only its
official groups, so **it also works with a plain** ``CognitiveServicesManagementClient``: you keep these helpers
after you move to the Microsoft client.

* :class:`CostControl` is an authoring spec. It validates offline against the rules in the service definition and
  converts to and from the official models (:meth:`CostControl.to_resource`, :meth:`CostControl.from_resource`).
* :func:`list_all`, :func:`applicable`, :func:`project_budget`, :func:`remove_alert`, :func:`remove_block`,
  :func:`exists`, :func:`upsert` and :func:`delete_project_cost_controls` cover what the portal does around them.
* :func:`get_usage_connection` and :func:`set_usage_connection` manage the Application Insights connection
  (the portal's **Usage data**).
* :func:`evaluate_cost_control`, :class:`CostControlAlertEvent`, :class:`AlertDeduper` and :class:`BudgetHeaders`
  interpret what the service sends back.
"""

from __future__ import annotations

import builtins
import json
from collections.abc import Iterator
from typing import Any

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError as _AzureHttpResponseError
from azure.core.exceptions import ResourceNotFoundError
from azure.mgmt.cognitiveservices import models as cs

from .errors import BudgetHeaders, ForbiddenError
from .models import (
    AlertDeduper,
    BudgetCategory,
    BudgetSharing,
    CostControl,
    CostControlAlertEvent,
    CostControlState,
    CostControlStatus,
    ProjectBudget,
    Threshold,
    ThresholdType,
    TimeGrain,
    applies_to_project,
    evaluate_cost_control,
    project_budget,
)

__all__ = [
    "AlertDeduper",
    "BudgetCategory",
    "BudgetHeaders",
    "BudgetSharing",
    "CostControl",
    "CostControlAlertEvent",
    "CostControlState",
    "CostControlStatus",
    "ProjectBudget",
    "Threshold",
    "ThresholdType",
    "TimeGrain",
    "applicable",
    "applies_to_project",
    "delete_project_cost_controls",
    "evaluate_cost_control",
    "exists",
    "find_event_grid_connection_id",
    "get_project_budget",
    "get_usage_connection",
    "list_all",
    "list_specs",
    "project_budget",
    "remove_alert",
    "remove_block",
    "set_usage_connection",
    "upsert",
]

CostControlResource = cs.AccountCostControl | cs.ProjectCostControl

REGISTRATION_HINT = (
    "Selecting traffic by identity (agent or session filters) needs the feature "
    "Microsoft.CognitiveServices/CostControlsByIdentity registered on the subscription "
    "(az feature register --namespace Microsoft.CognitiveServices --name CostControlsByIdentity)."
)


def explain(exc: _AzureHttpResponseError) -> _AzureHttpResponseError:
    """Return ``exc``, or a :class:`~foundry_finops.errors.ForbiddenError` that says how to register
    ``CostControlsByIdentity`` when the service answered 403 ``FeatureNotRegistered``."""
    code = (getattr(getattr(exc, "error", None), "code", None) or "").lower()
    if exc.status_code == 403 and code == "featurenotregistered":
        return ForbiddenError(f"{exc.message} {REGISTRATION_HINT}", status_code=403, code="FeatureNotRegistered")
    return exc


def _from_resource(resource: Any) -> CostControl:
    return CostControl.from_resource(resource)


# ================================================================ reads
def list_all(client: Any, resource_group: str, account: str) -> Iterator[CostControlResource]:
    """The Foundry resource's cost controls followed by every project's, as official models."""
    yield from client.account_cost_controls.list(resource_group, account)
    for project in client.projects.list(resource_group, account):
        name = (project.name or "").split("/")[-1]
        if name:
            yield from client.project_cost_controls.list(resource_group, account, name)


def list_specs(client: Any, resource_group: str, account: str) -> Iterator[CostControl]:
    """:func:`list_all` as :class:`CostControl` specs (flat fields, the project taken from the resource ID)."""
    for resource in list_all(client, resource_group, account):
        yield _from_resource(resource)


def exists(
    client: Any, resource_group: str, account: str, name: str, *, project: str | None = None
) -> bool:
    """``True`` if the cost control exists.

    The official ``check_existence`` raises ``ResourceNotFoundError`` for a missing resource in 15.1.0b1; this returns
    ``False`` instead.
    """
    group = client.project_cost_controls if project else client.account_cost_controls
    args = (resource_group, account, project, name) if project else (resource_group, account, name)
    try:
        return bool(group.check_existence(*args))
    except ResourceNotFoundError:
        return False


def applicable(client: Any, resource_group: str, account: str, project: str) -> builtins.list[CostControl]:
    """What a project's **Cost Control** tab lists: the project's own cost controls plus the Foundry resource's that
    apply to it (read-only there)."""
    return [cc for cc in list_specs(client, resource_group, account) if applies_to_project(cc, project)]


def get_project_budget(client: Any, resource_group: str, account: str, project: str) -> ProjectBudget:
    """The *Project budget* card: the lowest whole-project spending budget and the counts. See
    :func:`foundry_finops.models.project_budget` for the rules."""
    return project_budget(applicable(client, resource_group, account, project), project)


def find_event_grid_connection_id(client: Any, resource_group: str, account: str) -> str | None:
    """An ``eventGridConnectionId`` already used by an existing cost control's alert.

    Foundry creates this connection when you save an alert in the portal. Connections you create yourself are
    silently skipped by alerts, so reuse this one.
    """
    for cc in list_specs(client, resource_group, account):
        if cc.alert and cc.alert.event_grid_connection_id:
            return cc.alert.event_grid_connection_id
    return None


# ============================================================== writes
def upsert(
    client: Any,
    resource_group: str,
    account: str,
    cost_control: CostControl | CostControlResource,
    *,
    project: str | None = None,
    only_if_new: bool = False,
    etag: str | None = None,
) -> CostControl:
    """Create or replace a cost control with the official ``create_or_update``.

    ``cost_control`` is a :class:`CostControl` spec (validated first) or an official ``AccountCostControl`` /
    ``ProjectCostControl``. ``only_if_new`` sends ``If-None-Match: *`` (the service answers 409 if it exists);
    ``etag`` sends ``If-Match`` so a change made since you read it is refused with 412.
    """
    if only_if_new and etag:
        raise ValueError("Use only_if_new or etag, not both")
    if isinstance(cost_control, CostControl):
        resource: Any = cost_control.to_resource(project)
        owner, name = cost_control.project, cost_control.name
    else:
        resource, name = cost_control, cost_control.name
        rid = resource.id or ""
        owner = project or (rid.split("/projects/")[1].split("/")[0] if "/projects/" in rid else None)
    if not name:
        raise ValueError("The cost control needs a name")
    kwargs: dict[str, Any] = {}
    if only_if_new:
        kwargs = {"match_condition": MatchConditions.IfMissing}
    elif etag:
        kwargs = {"etag": etag, "match_condition": MatchConditions.IfNotModified}
    group = client.project_cost_controls if owner else client.account_cost_controls
    args = (resource_group, account, owner, name, resource) if owner else (resource_group, account, name, resource)
    try:
        return _from_resource(group.create_or_update(*args, **kwargs))
    except _AzureHttpResponseError as exc:
        raise explain(exc) from exc


def _patch(
    client: Any, resource_group: str, account: str, name: str, project: str | None, patch: Any, etag: str | None,
) -> CostControl:
    group = client.project_cost_controls if project else client.account_cost_controls
    args = (resource_group, account, project, name) if project else (resource_group, account, name)
    kwargs: dict[str, Any] = {"etag": etag, "match_condition": MatchConditions.IfNotModified} if etag else {}
    try:
        return _from_resource(group.update(*args, patch, **kwargs))
    except _AzureHttpResponseError as exc:
        raise explain(exc) from exc


def _current(client: Any, resource_group: str, account: str, name: str, project: str | None) -> CostControl:
    if project:
        return _from_resource(client.project_cost_controls.get(resource_group, account, project, name))
    return _from_resource(client.account_cost_controls.get(resource_group, account, name))


def remove_alert(
    client: Any, resource_group: str, account: str, name: str, *, project: str | None = None,
    etag: str | None = None,
) -> CostControl:
    """Delete the cost control's alert and keep everything else (a PATCH with a ``null`` notification)."""
    current = _current(client, resource_group, account, name, project)
    if current.alert is None:
        return current
    entry = current.alert._default_name("alert")
    # A None value in the dict is sent as null, which deletes the named notification; the rest is preserved.
    patch = cs.CostControlPatch(properties=cs.CostControlPatchProperties(notifications={entry: None}))  # type: ignore[dict-item]
    return _patch(client, resource_group, account, name, project, patch, etag)


def remove_block(
    client: Any, resource_group: str, account: str, name: str, *, project: str | None = None,
    etag: str | None = None,
) -> CostControl:
    """Delete the cost control's Block action and keep everything else. It then tracks and alerts only."""
    current = _current(client, resource_group, account, name, project)
    if current.block is None:
        return current
    entry = current.block._default_name("block")
    patch = cs.CostControlPatch(properties=cs.CostControlPatchProperties(
        provider_properties=cs.CostControlProviderPropertiesPatch(actions={entry: None})))  # type: ignore[dict-item]
    return _patch(client, resource_group, account, name, project, patch, etag)


def delete_project_cost_controls(
    client: Any, resource_group: str, account: str, project: str
) -> builtins.list[str]:
    """Delete every cost control the project owns and return their names.

    A project that has its own cost controls can't be deleted (HTTP 409 ``CostControlInUse``), so call this first.
    The Foundry resource's cost controls are not touched.
    """
    names = [c.name for c in client.project_cost_controls.list(resource_group, account, project) if c.name]
    for n in names:
        client.project_cost_controls.delete(resource_group, account, project, n)
    return names


# ======================================================== usage data connection
def get_usage_connection(client: Any, resource_group: str, account: str) -> str | None:
    """Resource ID of the Application Insights connection that records usage for the account's cost controls (the
    portal's **Usage data**), or ``None`` when none is set."""
    acct = client.accounts.get(resource_group, account)
    conn = getattr(getattr(acct, "properties", None), "cost_control_connections", None)
    return getattr(conn, "app_insights_connection_id", None)


def set_usage_connection(
    client: Any, resource_group: str, account: str, connection_id: str | None
) -> None:
    """Set, or with ``None`` clear, the Application Insights connection for the account's cost controls.

    ``connection_id`` is the ID of a Foundry connection (``.../accounts/<account>/connections/<name>``). It is shared
    by every cost control on the account, so changing it affects all of them. Alerts and blocks work without it; it
    is only needed to see usage against each budget.
    """
    # Clearing needs an explicit null, which a model field can't carry, so this one is sent as JSON.
    value = {"appInsightsConnectionId": connection_id} if connection_id else None
    body = json.dumps({"properties": {"costControlConnections": value}}).encode()
    client.accounts.begin_update(resource_group, account, body).result()
