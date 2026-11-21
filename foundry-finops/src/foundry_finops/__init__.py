"""foundry-finops – a Pythonic SDK over the REST APIs that track and control Microsoft Foundry spend.

Quick start::

    from azure.identity import DefaultAzureCredential
    from foundry_finops import FinOpsClient
    client = FinOpsClient(DefaultAzureCredential(), "<subscription-id>", resource_group="rg-ai")
    list(client.account_cost_controls.list("rg-ai", "contoso-foundry"))
"""

from ._auth import StaticTokenCredential, TokenCredential, default_credential
from ._client import FinOpsClient
from ._config import CHINA_CLOUD, PUBLIC_CLOUD, US_GOV_CLOUD, ApiVersions, AzureCloud
from ._ids import ResourceId, apim_service_id, foundry_account_id, foundry_project_id
from ._transport import RetryPolicy
from ._version import __version__
from .errors import (
    AuthenticationError,
    BudgetExceededError,
    BudgetHeaders,
    ConflictError,
    FinOpsError,
    ForbiddenError,
    HttpResponseError,
    NotFoundError,
    PreconditionFailedError,
    ServiceError,
    ThrottledError,
    is_budget_exceeded,
    raise_for_budget,
)
from .models import (
    ActionGroup,
    AlertDeduper,
    ApimProduct,
    ApimSubscription,
    Budget,
    BudgetCategory,
    BudgetNotification,
    BudgetSharing,
    CostControl,
    CostControlAlertEvent,
    CostControlState,
    CostControlStatus,
    CostQuery,
    DeploymentView,
    Granularity,
    LogAlert,
    MetricAlert,
    ProjectBudget,
    QuotaView,
    Table,
    Threshold,
    ThresholdType,
    TimeGrain,
    applies_to_project,
    evaluate_cost_control,
    project_budget,
)
from .operations.apim import LlmEmitTokenMetric, LlmTokenLimit
from .operations.monitor import FoundryMetrics
from .operations.pricing import ModelPrice, PriceSheet

__all__ = [
    "CHINA_CLOUD",
    "PUBLIC_CLOUD",
    "US_GOV_CLOUD",
    "ActionGroup",
    "AlertDeduper",
    "ApiVersions",
    "ApimProduct",
    "ApimSubscription",
    "AuthenticationError",
    "AzureCloud",
    "Budget",
    "BudgetCategory",
    "BudgetExceededError",
    "BudgetHeaders",
    "BudgetNotification",
    "BudgetSharing",
    "ConflictError",
    "CostControl",
    "CostControlAlertEvent",
    "CostControlState",
    "CostControlStatus",
    "CostQuery",
    "DeploymentView",
    "FinOpsClient",
    "FinOpsError",
    "ForbiddenError",
    "FoundryMetrics",
    "Granularity",
    "HttpResponseError",
    "LlmEmitTokenMetric",
    "LlmTokenLimit",
    "LogAlert",
    "MetricAlert",
    "ModelPrice",
    "NotFoundError",
    "PreconditionFailedError",
    "PriceSheet",
    "ProjectBudget",
    "QuotaView",
    "ResourceId",
    "RetryPolicy",
    "ServiceError",
    "StaticTokenCredential",
    "Table",
    "Threshold",
    "ThresholdType",
    "ThrottledError",
    "TimeGrain",
    "TokenCredential",
    "__version__",
    "apim_service_id",
    "applies_to_project",
    "default_credential",
    "evaluate_cost_control",
    "foundry_account_id",
    "foundry_project_id",
    "is_budget_exceeded",
    "project_budget",
    "raise_for_budget",
]
