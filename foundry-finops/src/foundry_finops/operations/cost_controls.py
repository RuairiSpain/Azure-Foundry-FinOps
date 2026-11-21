"""Foundry cost controls (preview) – real-time spending/token budgets that alert or block."""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from typing import Any

from .._ids import foundry_account_id
from ..errors import ForbiddenError
from ..models import CostControl, ProjectBudget, applies_to_project, project_budget
from ._base import _Operations


class CostControlOperations(_Operations):
    """CRUD for ``Microsoft.CognitiveServices/accounts[/projects]/costControls``.

    Pass ``project=`` to work with a project's own cost controls; omit it for the Foundry
    resource's cost controls. Changes take effect within about 15 minutes.

    The service documents that concurrent creates on one Foundry resource can fail with
    HTTP 409, so create cost controls one at a time.
    """

    _REGISTRATION_HINT = (
        "Selecting traffic by identity (agent or session filters) needs the feature "
        "Microsoft.CognitiveServices/CostControlsByIdentity registered on the subscription "
        "(az feature register --namespace Microsoft.CognitiveServices --name CostControlsByIdentity)."
    )

    def _write(self, method: str, url: str, *, body: Any = None, headers: Any = None) -> Any:
        """Write call that explains a ``FeatureNotRegistered`` refusal instead of leaving it opaque."""
        try:
            return self._arm(method, url, self._v.cost_controls, body=body, headers=headers)
        except ForbiddenError as exc:
            if (exc.code or "").lower() == "featurenotregistered":
                raise ForbiddenError(f"{exc.message} {self._REGISTRATION_HINT}", status_code=exc.status_code,
                                     code=exc.code, headers=exc.headers, body=exc.body, method=exc.method,
                                     url=exc.url) from exc
            raise

    def _base(self, account: str, project: str | None, resource_group: str | None) -> str:
        rid = foundry_account_id(self._sub, self._rg_or_default(resource_group), account)
        if project:
            rid += f"/projects/{self._seg(project)}"
        return f"{rid}/costControls"

    def list(
        self, account: str, *, project: str | None = None, resource_group: str | None = None
    ) -> Iterator[CostControl]:
        for d in self._list(self._base(account, project, resource_group), self._v.cost_controls):
            yield CostControl.from_dict(d)

    def list_all(self, account: str, *, resource_group: str | None = None) -> Iterator[CostControl]:
        """Resource-level cost controls followed by every project's cost controls."""
        from .foundry import FoundryOperations

        yield from self.list(account, resource_group=resource_group)
        foundry = FoundryOperations(
            self._t, subscription_id=self._sub, resource_group=self._rg, cloud=self._cloud, api_versions=self._v
        )
        for proj in foundry.list_projects(account, resource_group):
            if proj.name:
                yield from self.list(account, project=proj.name, resource_group=resource_group)

    def get(
        self, account: str, name: str, *, project: str | None = None, resource_group: str | None = None
    ) -> CostControl:
        url = f"{self._base(account, project, resource_group)}/{self._seg(name)}"
        return CostControl.from_dict(self._arm("GET", url, self._v.cost_controls).body)

    def create_or_update(
        self,
        account: str,
        cost_control: CostControl,
        *,
        project: str | None = None,
        resource_group: str | None = None,
        only_if_new: bool = False,
        if_match: str | None = None,
    ) -> CostControl:
        """PUT a cost control.

        ``only_if_new=True`` sends ``If-None-Match: *`` (raises ConflictError if it exists). ``if_match`` takes the
        ``etag`` of a cost control you read and replaces it only if nobody changed it since (ConflictError otherwise).
        """
        if not cost_control.name:
            raise ValueError("cost_control.name is required")
        if only_if_new and if_match:
            raise ValueError("Use only_if_new or if_match, not both")
        cost_control.project = project or cost_control.project
        url = f"{self._base(account, cost_control.project, resource_group)}/{self._seg(cost_control.name)}"
        headers = {"If-None-Match": "*"} if only_if_new else ({"If-Match": if_match} if if_match else None)
        resp = self._write("PUT", url, body=cost_control.to_dict(), headers=headers)
        return CostControl.from_dict(resp.body) if resp.body else self.get(
            account, cost_control.name, project=cost_control.project, resource_group=resource_group
        )

    def update(
        self,
        account: str,
        name: str,
        *,
        project: str | None = None,
        resource_group: str | None = None,
        if_match: str | None = None,
        **properties: object,
    ) -> CostControl:
        """PATCH only the given ARM properties, e.g. ``update(acct, "x", amount=750)``.

        Changing the reset period or sharing restarts the usage count. The budget type
        (``category``) can't be changed. ``if_match`` makes the change conditional on the ``etag`` you read.
        """
        if "category" in properties:
            raise ValueError("A cost control's budget type can't be changed; create a new one")
        if not properties:
            # The service treats an empty ``properties`` object as "clear every property", so never send one.
            raise ValueError("update() needs at least one property to change; an empty update would clear the whole "
                             "cost control")
        url = f"{self._base(account, project, resource_group)}/{self._seg(name)}"
        resp = self._write("PATCH", url, body={"properties": properties},
                           headers={"If-Match": if_match} if if_match else None)
        return CostControl.from_dict(resp.body) if resp.body else self.get(
            account, name, project=project, resource_group=resource_group
        )

    def delete(
        self, account: str, name: str, *, project: str | None = None, resource_group: str | None = None,
        if_match: str | None = None,
    ) -> None:
        url = f"{self._base(account, project, resource_group)}/{self._seg(name)}"
        self._write("DELETE", url, headers={"If-Match": if_match} if if_match else None)

    def exists(
        self, account: str, name: str, *, project: str | None = None, resource_group: str | None = None
    ) -> bool:
        """``True`` if the cost control exists (a ``HEAD`` request: no body is returned)."""
        from ..errors import NotFoundError

        url = f"{self._base(account, project, resource_group)}/{self._seg(name)}"
        try:
            self._arm("HEAD", url, self._v.cost_controls, expected=(200,))
        except NotFoundError:
            return False
        return True

    def _remove(
        self, kind: str, account: str, name: str, project: str | None, resource_group: str | None, if_match: str | None
    ) -> CostControl:
        current = self.get(account, name, project=project, resource_group=resource_group)
        threshold = current.alert if kind == "notifications" else current.block
        if threshold is None:
            return current
        entry = threshold._default_name("alert" if kind == "notifications" else "block")
        # A null entry deletes the named alert or action; every other property is kept.
        props: dict[str, Any] = {"notifications": {entry: None}} if kind == "notifications" else {
            "providerProperties": {"actions": {entry: None}}}
        return self.update(account, name, project=project, resource_group=resource_group, if_match=if_match, **props)

    def remove_alert(
        self, account: str, name: str, *, project: str | None = None, resource_group: str | None = None,
        if_match: str | None = None,
    ) -> CostControl:
        """Delete the cost control's alert and keep everything else (PATCH with a ``null`` notification)."""
        return self._remove("notifications", account, name, project, resource_group, if_match)

    def remove_block(
        self, account: str, name: str, *, project: str | None = None, resource_group: str | None = None,
        if_match: str | None = None,
    ) -> CostControl:
        """Delete the cost control's Block action and keep everything else. It then tracks and alerts only."""
        return self._remove("block", account, name, project, resource_group, if_match)

    def applicable(self, account: str, project: str, *, resource_group: str | None = None) -> builtins.list[CostControl]:
        """What a project's **Cost Control** tab lists: the project's own cost controls plus the Foundry resource's
        that apply to it (read-only there)."""
        return [cc for cc in self.list_all(account, resource_group=resource_group) if applies_to_project(cc, project)]

    def project_budget(self, account: str, project: str, *, resource_group: str | None = None) -> ProjectBudget:
        """The *Project budget* card: the lowest whole-project spending budget, and the counts. See
        :func:`foundry_finops.models.project_budget` for the rules."""
        return project_budget(self.applicable(account, project, resource_group=resource_group), project)

    def delete_project_cost_controls(
        self, account: str, project: str, *, resource_group: str | None = None
    ) -> builtins.list[str]:
        """Delete every cost control the project owns and return their names.

        A project that has its own cost controls can't be deleted (HTTP 409 ``CostControlInUse``), so call this first.
        The Foundry resource's cost controls are not touched.
        """
        names = [cc.name for cc in self.list(account, project=project, resource_group=resource_group) if cc.name]
        for n in names:
            self.delete(account, n, project=project, resource_group=resource_group)
        return names

    # -------------------------------------------------------------- usage data
    def get_usage_connection(self, account: str, *, resource_group: str | None = None) -> str | None:
        """Resource ID of the Application Insights connection that records usage for the account's cost controls
        (the portal's **Usage data**), or ``None`` when none is set."""
        rid = foundry_account_id(self._sub, self._rg_or_default(resource_group), account)
        body = self._arm("GET", rid, self._v.cost_controls).body or {}
        conn = ((body.get("properties") or {}).get("costControlConnections") or {})
        return conn.get("appInsightsConnectionId")

    def set_usage_connection(
        self, account: str, connection_id: str | None, *, resource_group: str | None = None
    ) -> None:
        """Set, or with ``None`` clear, the Application Insights connection for the account's cost controls.

        ``connection_id`` is the ID of a Foundry connection (``.../accounts/<account>/connections/<name>``). It is
        shared by every cost control on the account, so changing it affects all of them. Alerts and blocks work
        without it; it is only needed to see usage against each budget.
        """
        rid = foundry_account_id(self._sub, self._rg_or_default(resource_group), account)
        value = {"appInsightsConnectionId": connection_id} if connection_id else None
        self._write("PATCH", rid, body={"properties": {"costControlConnections": value}})

    def find_event_grid_connection_id(self, account: str, *, resource_group: str | None = None) -> str | None:
        """Return an ``eventGridConnectionId`` already used by an existing cost control.

        Foundry creates this connection when you save an alert in the portal; the docs advise
        reusing it because connections you create yourself are silently skipped.
        """
        for cc in self.list_all(account, resource_group=resource_group):
            if cc.alert and cc.alert.event_grid_connection_id:
                return cc.alert.event_grid_connection_id
        return None
