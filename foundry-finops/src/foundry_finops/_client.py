"""The :class:`FinOpsClient`: the Microsoft management client for Foundry, plus the FinOps operation groups.

``FinOpsClient`` **is** ``azure.mgmt.cognitiveservices.CognitiveServicesManagementClient`` (the official client, which
since 15.1.0b1 has the Foundry cost controls), extended with groups for what that package doesn't cover: Cost
Management queries, token metrics, KQL, alerts, API Management policies, pricing and so on. Code that uses the
official groups (``accounts``, ``projects``, ``deployments``, ``account_cost_controls``, ``project_cost_controls``,
``usages``, ...) therefore moves to the plain Microsoft client by changing only the constructor and the import.
"""

from __future__ import annotations

from dataclasses import replace
from functools import cached_property
from typing import Any

import requests
from azure.core import AzureClouds
from azure.core.credentials import TokenCredential
from azure.core.pipeline.transport import RequestsTransport
from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient

from ._auth import default_credential
from ._config import CHINA_CLOUD, PUBLIC_CLOUD, US_GOV_CLOUD, ApiVersions, AzureCloud
from ._ids import foundry_account_id
from ._transport import RetryPolicy, Transport
from .operations import (
    AlertOperations,
    ApimOperations,
    AuthorizationOperations,
    CostManagementOperations,
    EventGridOperations,
    LogsOperations,
    MetricsOperations,
    PolicyOperations,
    PricingOperations,
    TagOperations,
)

_CLOUDS = {
    AzureClouds.AZURE_PUBLIC_CLOUD: PUBLIC_CLOUD,
    AzureClouds.AZURE_US_GOVERNMENT: US_GOV_CLOUD,
    AzureClouds.AZURE_CHINA_CLOUD: CHINA_CLOUD,
}


class FinOpsClient(CognitiveServicesManagementClient):
    """The official Foundry management client with FinOps operation groups added.

    The constructor is the one every Azure management client has: ``(credential, subscription_id, base_url=None, *,
    cloud_setting=None, **kwargs)``. The extra keyword arguments are specific to this package.

    Args:
        credential: A ``TokenCredential``, for example ``azure.identity.DefaultAzureCredential()``.
        subscription_id: Azure subscription ID.
        base_url: Azure Resource Manager endpoint (default: the one for ``cloud_setting``).
        cloud_setting: ``azure.core.AzureClouds`` value for a sovereign cloud; default public Azure.
        resource_group: Default resource group for the FinOps groups. The official groups always take the resource
            group as their first argument, as in the Microsoft SDK.
        api_versions: Override REST API versions of the FinOps groups. The official groups take ``api_version=``.
        retry: Retry policy for the FinOps groups. It is also applied to the official groups
            (``retry_total`` and ``retry_backoff_factor``).
        timeout: Per-request timeout in seconds.
        session: Optional ``requests.Session`` shared by every group (proxies, custom adapters, tests).
        **kwargs: Passed to the Microsoft client (``api_version``, ``transport``, ``logging_enable``, ...).

    Example::

        from azure.identity import DefaultAzureCredential
        from foundry_finops import FinOpsClient

        with FinOpsClient(DefaultAzureCredential(), "<subscription-id>", resource_group="rg-ai") as client:
            for cc in client.account_cost_controls.list("rg-ai", "contoso-foundry"):      # official group
                print(cc.name, cc.properties.amount)
            by_project = client.cost.cost_by("project", scope=client.account_id("contoso-foundry"))   # FinOps group

    FinOps groups (everything else is the Microsoft client):

    =====================  ==========================================================
    ``cost``               Cost Management query, forecast, budgets, exports
    ``metrics``            Azure Monitor token/request metrics
    ``logs``               KQL against Log Analytics / Application Insights
    ``tags``               ARM tags on any scope
    ``alerts``             Metric alerts, log alerts, action groups
    ``apim``               APIM products, subscriptions, token-limit/metric policies
    ``pricing``            Retail prices and token cost estimates
    ``policy``             Azure Policy compliance (the fleet's policy violations)
    ``event_grid``         Event Grid topics (alert destinations for cost controls)
    ``authorization``      The signed-in identity's role assignments and role definitions
    =====================  ==========================================================
    """

    def __init__(
        self,
        credential: TokenCredential,
        subscription_id: str,
        base_url: str | None = None,
        *,
        cloud_setting: AzureClouds | None = None,
        resource_group: str | None = None,
        api_versions: ApiVersions | None = None,
        retry: RetryPolicy | None = None,
        timeout: float = 60.0,
        session: requests.Session | None = None,
        **kwargs: Any,
    ) -> None:
        if not subscription_id:
            raise ValueError("subscription_id is required")
        retry = retry or RetryPolicy()
        self._session = session or requests.Session()
        self._owns_session = session is None
        if "transport" not in kwargs:
            kwargs["transport"] = RequestsTransport(
                session=self._session, session_owner=False, connection_timeout=timeout, read_timeout=timeout
            )
        kwargs.setdefault("retry_total", retry.total)
        kwargs.setdefault("retry_backoff_factor", retry.backoff_factor)
        super().__init__(credential, subscription_id, base_url, cloud_setting=cloud_setting, **kwargs)
        cloud: AzureCloud = _CLOUDS.get(cloud_setting or AzureClouds.AZURE_PUBLIC_CLOUD, PUBLIC_CLOUD)
        if base_url:
            cloud = replace(cloud, resource_manager=base_url)
        self.subscription_id = subscription_id
        self.resource_group = resource_group
        self.cloud = cloud
        self.api_versions = api_versions or ApiVersions()
        self._transport = Transport(credential, session=self._session, retry=retry, timeout=timeout)

    @classmethod
    def from_default_credential(cls, subscription_id: str, **kwargs: Any) -> FinOpsClient:
        """``FinOpsClient(DefaultAzureCredential(), subscription_id, ...)`` for the cloud in ``cloud_setting``."""
        cloud = _CLOUDS.get(kwargs.get("cloud_setting") or AzureClouds.AZURE_PUBLIC_CLOUD, PUBLIC_CLOUD)
        return cls(default_credential(authority=cloud.authority), subscription_id, **kwargs)

    # ------------------------------------------------------------- conveniences
    def account_id(self, account: str, resource_group: str | None = None) -> str:
        """Resource ID of a Foundry resource, for example as the ``scope`` of a cost query."""
        rg = resource_group or self.resource_group
        if not rg:
            raise ValueError("resource_group is required (pass it or set a default on FinOpsClient)")
        return foundry_account_id(self.subscription_id, rg, account)

    def _ops(self, cls: type[Any]) -> Any:
        return cls(
            self._transport,
            subscription_id=self.subscription_id,
            resource_group=self.resource_group,
            cloud=self.cloud,
            api_versions=self.api_versions,
        )

    # ------------------------------------------------------------ FinOps groups
    @cached_property
    def cost(self) -> CostManagementOperations:
        return self._ops(CostManagementOperations)  # type: ignore[no-any-return]

    @cached_property
    def metrics(self) -> MetricsOperations:
        return self._ops(MetricsOperations)  # type: ignore[no-any-return]

    @cached_property
    def logs(self) -> LogsOperations:
        return self._ops(LogsOperations)  # type: ignore[no-any-return]

    @cached_property
    def tags(self) -> TagOperations:
        return self._ops(TagOperations)  # type: ignore[no-any-return]

    @cached_property
    def alerts(self) -> AlertOperations:
        return self._ops(AlertOperations)  # type: ignore[no-any-return]

    @cached_property
    def apim(self) -> ApimOperations:
        return self._ops(ApimOperations)  # type: ignore[no-any-return]

    @cached_property
    def authorization(self) -> AuthorizationOperations:
        return self._ops(AuthorizationOperations)  # type: ignore[no-any-return]

    @cached_property
    def pricing(self) -> PricingOperations:
        return self._ops(PricingOperations)  # type: ignore[no-any-return]

    @cached_property
    def policy(self) -> PolicyOperations:
        return self._ops(PolicyOperations)  # type: ignore[no-any-return]

    @cached_property
    def event_grid(self) -> EventGridOperations:
        return self._ops(EventGridOperations)  # type: ignore[no-any-return]

    def close(self) -> None:
        super().close()
        if self._owns_session:
            self._session.close()

    def __repr__(self) -> str:
        return f"FinOpsClient(subscription_id={self.subscription_id!r}, resource_group={self.resource_group!r})"
