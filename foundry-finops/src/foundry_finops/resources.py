"""Read views of Foundry resources built from the **official** Microsoft models.

Reading accounts, projects, deployments, connections and quota is done with the operation groups of
``azure.mgmt.cognitiveservices`` (``client.accounts``, ``client.projects``, ``client.deployments``,
``client.account_connections``, ``client.usages``), which :class:`foundry_finops.FinOpsClient` inherits. What those
return are the Microsoft models. The functions here add the numbers FinOps work needs on top of them (tokens per
minute, whether a deployment is provisioned, quota percent used) as small read-only views. They take the client as
the first argument and work with a plain ``CognitiveServicesManagementClient`` as well.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from .models import AccountView, DeploymentView, ProjectView, QuotaView

__all__ = [
    "get_account",
    "get_deployment",
    "get_project",
    "list_connections",
    "list_deployments",
    "list_projects",
    "list_quota",
    "set_deployment_capacity",
]


def _d(model: Any) -> dict[str, Any]:
    return dict(model.as_dict())


def get_account(client: Any, resource_group: str, account: str) -> AccountView:
    return AccountView.from_dict(_d(client.accounts.get(resource_group, account)))


def list_projects(client: Any, resource_group: str, account: str) -> Iterator[ProjectView]:
    for p in client.projects.list(resource_group, account):
        yield ProjectView.from_dict(_d(p))


def get_project(client: Any, resource_group: str, account: str, project: str) -> ProjectView:
    return ProjectView.from_dict(_d(client.projects.get(resource_group, account, project)))


def list_deployments(client: Any, resource_group: str, account: str) -> Iterator[DeploymentView]:
    for d in client.deployments.list(resource_group, account):
        yield DeploymentView.from_dict(_d(d))


def get_deployment(client: Any, resource_group: str, account: str, deployment: str) -> DeploymentView:
    return DeploymentView.from_dict(_d(client.deployments.get(resource_group, account, deployment)))


def list_connections(client: Any, resource_group: str, account: str) -> Iterator[dict[str, Any]]:
    """The account's connections as JSON (the alert destination of a cost control is one of them)."""
    for c in client.account_connections.list(resource_group, account):
        yield _d(c)


def list_quota(client: Any, location: str, *, filter: str | None = None) -> Iterator[QuotaView]:
    """Subscription quota (TPM and others) in a region: ``client.usages.list`` with ``percent_used`` added."""
    for u in client.usages.list(location, filter=filter):
        yield QuotaView.from_dict(_d(u))


def set_deployment_capacity(
    client: Any, resource_group: str, account: str, deployment: str, capacity: int
) -> DeploymentView:
    """Change a deployment's capacity (TPM or PTU allocation) and nothing else.

    Sends a PATCH of ``sku.capacity`` and waits for it. A full PUT would have to send every property back, and anything
    the client doesn't know about (service tier ``Priority``, spillover deployment, paused state, routing) would be
    reset to its default: a silent change to price and behaviour.
    """
    if capacity < 1:
        raise ValueError("capacity must be at least 1")
    current = client.deployments.get(resource_group, account, deployment)
    sku = {**(current.sku.as_dict() if current.sku is not None else {}), "capacity": capacity}
    client.deployments.begin_update(resource_group, account, deployment, json.dumps({"sku": sku}).encode()).result()
    return get_deployment(client, resource_group, account, deployment)
