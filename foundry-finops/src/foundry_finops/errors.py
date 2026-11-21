"""Exception hierarchy and helpers for Foundry cost-control blocks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from azure.core import exceptions as _az

BUDGET_CAUSE_HEADER = "x-ms-budget-cause"
BUDGET_RESET_HEADER = "x-ms-budget-window-reset"
#: Foundry documents both names for the ID of the cost control that blocked a request.
BUDGET_POLICY_HEADERS = ("x-ms-applicable-policy-id", "x-ms-applied-cost-control-id")
BUDGET_EXCEEDED_MESSAGE = "Budget exceeded for cost control"


class FinOpsError(Exception):
    """Base class for every error raised by this package."""


class HttpResponseError(FinOpsError, _az.HttpResponseError):
    """A REST call returned an unsuccessful status code.

    It is an :class:`azure.core.exceptions.HttpResponseError`, so ``except azure.core.exceptions.HttpResponseError``
    catches errors from this package and from the Microsoft Azure SDKs alike, and ``status_code``, ``reason`` and
    ``error.code`` are populated the way those libraries populate them. ``code``, ``headers``, ``body``, ``method``
    and ``url`` are extras.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        headers: Mapping[str, str] | None = None,
        body: Any = None,
        method: str | None = None,
        url: str | None = None,
    ) -> None:
        _az.HttpResponseError.__init__(self, message=message)
        self.message = message
        self.status_code = status_code
        self.error = _az.ODataV4Format({"code": code, "message": message}) if code or message else None
        self.code = code
        self.headers = dict(headers or {})
        self.body = body
        self.method = method
        self.url = url

    def __str__(self) -> str:
        where = f" ({self.method} {self.url})" if self.method else ""
        code = f" [{self.code}]" if self.code else ""
        return f"HTTP {self.status_code}{code}: {self.message}{where}"


class AuthenticationError(HttpResponseError, _az.ClientAuthenticationError):
    """401 – the token was missing, expired or for the wrong audience."""


class ForbiddenError(HttpResponseError):
    """403 – the caller lacks an RBAC role (or an APIM token quota was exceeded)."""


class NotFoundError(HttpResponseError, _az.ResourceNotFoundError):
    """404 – the resource does not exist."""


class ConflictError(HttpResponseError, _az.ResourceExistsError):
    """409 – concurrent write or an ``If-None-Match: *`` create that found the resource."""


class PreconditionFailedError(ConflictError, _az.ResourceModifiedError):
    """412 – the entity tag you sent no longer matches (somebody changed the resource)."""


class ThrottledError(HttpResponseError):
    """429 – rate limited after retries were exhausted."""

    @property
    def retry_after(self) -> float | None:
        value = self.headers.get("Retry-After") or self.headers.get("retry-after")
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None


@dataclass(frozen=True)
class BudgetHeaders:
    """The ``x-ms-*`` headers Foundry adds to responses that a cost control covers.

    A blocked request carries ``x-ms-budget-cause: budget_exceeded`` (only cost control blocks have it) with the ID
    of the cost control, when its budget resets and its budget type. A successful request covered by a cost control
    carries ``x-ms-budget-state`` and ``x-ms-applicable-policy-id``; for a spending budget
    ``x-ms-available-pre-call-budget`` falls as usage is counted. If those are missing, or the budget doesn't fall, the
    model might not have a price in that region yet.
    """

    cause: str | None = None
    policy_id: str | None = None
    window_reset: str | None = None
    cost_control_type: str | None = None  #: ``Cost`` for a spending budget, ``Tokens`` for a token budget
    state: str | None = None
    available_pre_call_budget: float | None = None

    @property
    def blocked(self) -> bool:
        return (self.cause or "").lower() == "budget_exceeded"

    @property
    def covered(self) -> bool:
        """``True`` if a cost control covered the request (blocked or not)."""
        return any((self.cause, self.policy_id, self.state, self.cost_control_type))

    @classmethod
    def from_headers(cls, headers: Mapping[str, str] | None) -> BudgetHeaders:
        """Read the headers from a ``requests``/``httpx``/OpenAI response or a plain dict (case-insensitive)."""
        h = headers or {}
        policy = next((v for v in (_get_header(h, n) for n in BUDGET_POLICY_HEADERS) if v), None)
        raw = _get_header(h, "x-ms-available-pre-call-budget")
        try:
            remaining = float(raw) if raw not in (None, "") else None
        except ValueError:
            remaining = None
        return cls(
            cause=_get_header(h, BUDGET_CAUSE_HEADER),
            policy_id=policy,
            window_reset=_get_header(h, BUDGET_RESET_HEADER),
            cost_control_type=_get_header(h, "x-ms-cost-control-type"),
            state=_get_header(h, "x-ms-budget-state"),
            available_pre_call_budget=remaining,
        )


class BudgetExceededError(ThrottledError):
    """429 caused by a Foundry cost control ``Block usage`` action. Retrying won't help until reset."""

    @property
    def budget(self) -> BudgetHeaders:
        return BudgetHeaders.from_headers(self.headers)

    @property
    def cost_control_type(self) -> str | None:
        """``Cost`` (spending budget) or ``Tokens`` (token budget)."""
        return self.budget.cost_control_type

    @property
    def reset_at(self) -> str | None:
        return _get_header(self.headers, BUDGET_RESET_HEADER)

    @property
    def cost_control_id(self) -> str | None:
        for name in BUDGET_POLICY_HEADERS:
            value = _get_header(self.headers, name)
            if value:
                return value
        return None


class ServiceError(HttpResponseError):
    """5xx after retries were exhausted."""


class DependencyMissingError(FinOpsError, ImportError):
    """An optional dependency (pandas, OpenTelemetry, …) is required for this feature."""


def _get_header(headers: Mapping[str, str], name: str) -> str | None:
    lname = name.lower()
    for key, value in headers.items():
        if key.lower() == lname:
            return value
    return None


def is_budget_exceeded(headers: Mapping[str, str] | None, body: Any = None) -> bool:
    """Return ``True`` if a response was blocked by a Foundry cost control.

    Works with ``requests``/``httpx`` headers, ``openai.APIStatusError.response.headers``
    or a plain dict. Streaming responses return HTTP 200 and carry the block as an error
    event; pass that event's text/dict as ``body`` to detect it.
    """
    if headers and (_get_header(headers, BUDGET_CAUSE_HEADER) or "").lower() == "budget_exceeded":
        return True
    if body is not None:
        return BUDGET_EXCEEDED_MESSAGE.lower() in str(body).lower()
    return False


def raise_for_budget(exc: BaseException) -> None:
    """Re-raise an OpenAI/HTTP exception as :class:`BudgetExceededError` when a cost control blocked it.

    >>> try:
    ...     client.responses.create(model="gpt-5.4-mini", input="hi")
    ... except openai.RateLimitError as e:
    ...     raise_for_budget(e)   # raises BudgetExceededError, else returns
    ...     raise
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    if is_budget_exceeded(headers, getattr(exc, "message", None) or str(exc)):
        raise BudgetExceededError(
            BUDGET_EXCEEDED_MESSAGE,
            status_code=int(getattr(response, "status_code", 429) or 429),
            code="budget_exceeded",
            headers=dict(headers),
        ) from exc
