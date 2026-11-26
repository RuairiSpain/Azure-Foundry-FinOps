"""Azure Cost Management cost allocation rule definitions, built from a usage split.

An allocation rule moves the billed cost of some resources onto others by fixed percentages. This module builds the body
of the *Cost Allocation Rules - Create Or Update* call (REST API 2024-08-01) and checks it against the limits that page
documents. It builds the definition only; :meth:`CostManagementOperations.put_cost_allocation_rule` sends it.

What Azure's feature can and cannot say, from the documentation read on 26 November 2026:

* A rule has **one** source and **one** target, each with at most 25 values.
* A source or target is a ``Dimension`` (``ResourceGroupName`` or ``SubscriptionId`` only) or a ``Tag`` key.
* The percentages are fixed, have two decimals and add up to exactly 100.00. Azure does not compute them from usage, so
  a rule is a snapshot of one period's split and has to be rebuilt for the next.
* The rule lives on a billing account (the documented URL has no billing profile or subscription scope).
* The target is a resource group, a subscription or a tag value. A cost centre only shows up in Cost Management if
  resources carry a tag with the cost centre's code. A cost centre that exists only in the portal has nothing to receive
  the cost.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

__all__ = ["ALLOCATION_RULES_API_VERSION", "MAX_VALUES", "cost_allocation_rule", "rule_name", "split_percentages"]

#: The version whose request body was checked against Microsoft's reference.
ALLOCATION_RULES_API_VERSION = "2024-08-01"
#: Documented limit on the values of a source and of a target.
MAX_VALUES = 25
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,260}$")
_STATUSES = ("NotActive", "Active")   # "Processing" is read-only and can't be sent


def rule_name(label: str, *, prefix: str = "finops") -> str:
    """A rule name Azure accepts (letters, digits, ``-`` and ``_``, at most 260 characters) from any label."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-") or "rule"
    return f"{prefix}-{slug}"[:260]


def split_percentages(shares: Mapping[str, float]) -> dict[str, float]:
    """Turn weights into percentages with two decimals that add up to exactly 100.00.

    Done in hundredths of a percent with the largest remainder, so no rounding error is left over. A weight too small to
    reach 0.01 % is dropped; an empty result can't happen because the largest weight always gets something.
    """
    weights = {k: float(v) for k, v in shares.items() if float(v) > 0}
    if any(float(v) < 0 for v in shares.values()):
        raise ValueError("shares can't be negative")
    if not weights:
        raise ValueError("There is nothing to allocate: every share is zero")
    total = sum(weights.values())
    exact = {k: v / total * 10000 for k, v in weights.items()}
    hundredths = {k: int(v // 1) for k, v in exact.items()}
    for k in sorted(exact, key=lambda k: exact[k] - hundredths[k], reverse=True)[: 10000 - sum(hundredths.values())]:
        hundredths[k] += 1
    return {k: v / 100 for k, v in hundredths.items() if v > 0}


def _clean(values: Iterable[str], what: str) -> list[str]:
    out = sorted({str(v).strip() for v in values if str(v).strip()})
    if not out:
        raise ValueError(f"{what} needs at least one value")
    if len(out) > MAX_VALUES:
        raise ValueError(f"{what} has {len(out)} values; a rule allows at most {MAX_VALUES}")
    return out


def cost_allocation_rule(
    *,
    source_tag: str,
    source_values: Iterable[str],
    target_tag: str,
    shares: Mapping[str, float],
    description: str = "",
    status: str = "NotActive",
) -> dict[str, Any]:
    """The body for Create Or Update: the cost of resources tagged ``source_tag`` = each of ``source_values`` is moved onto
    resources tagged ``target_tag`` = each key of ``shares``, in proportion to the share values.

    ``status`` defaults to ``NotActive``: the rule is saved but changes no cost until someone activates it. Percentages
    come from :func:`split_percentages`.
    """
    if status not in _STATUSES:
        raise ValueError(f"status must be one of {_STATUSES} ('Processing' is read-only)")
    if not source_tag.strip() or not target_tag.strip():
        raise ValueError("source_tag and target_tag are required")
    sources = _clean(source_values, "The source")
    percentages = split_percentages({k.strip(): v for k, v in shares.items() if k.strip()})
    if len(percentages) > MAX_VALUES:
        raise ValueError(f"The target has {len(percentages)} values; a rule allows at most {MAX_VALUES}")
    return {"properties": {
        "description": description, "status": status,
        "details": {
            "sourceResources": [{"resourceType": "Tag", "name": source_tag.strip(), "values": sources}],
            "targetResources": [{
                "resourceType": "Tag", "policyType": "FixedProportion", "name": target_tag.strip(),
                "values": [{"name": k, "percentage": v} for k, v in sorted(percentages.items())],
            }],
        },
    }}


def check_rule_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise ValueError("A rule name has 1 to 260 letters, digits, '-' or '_' (no spaces)")
    return name
