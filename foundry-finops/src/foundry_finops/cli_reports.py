"""Extra ``foundry-finops`` commands that compute reports from the Azure APIs' own data.

``estimated-cost``  near-real-time estimated cost from the ``FoundryModelEstimatedCost`` metric
``prices``          retail prices (with an optional discount you supply) or the effective prices in your billing
``anomalies``       days whose billed cost jumps above the group's own history
``ptu``             provisioned-throughput deployments, utilisation and quota
``allocate``        spread a shared cost across groups in proportion to their tokens
``query``           run a Cost Management query saved as JSON
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from typing import Any

from ._client import FinOpsClient
from .analysis import allocate_cost, anomalies_from_table, ptu_headroom
from .errors import FinOpsError
from .models import CostQuery, Table

COMMANDS = ("estimated-cost", "prices", "anomalies", "ptu", "allocate", "query")
_DIMENSIONS = {"project": "ProjectId", "deployment": "ModelDeploymentName", "model": "ModelName",
               "version": "ModelVersion", "region": "Region"}


def add_parsers(sub: Any, account_default: str | None) -> None:
    acct = {"nargs": "?", "default": account_default, "help": "Foundry resource name (or FINOPS_FOUNDRY_ACCOUNT)"}

    p = sub.add_parser(
        "estimated-cost", help="near-real-time ESTIMATED cost per project/deployment (Azure Monitor, preview)",
        description="Estimated USD cost of Foundry model usage from the FoundryModelEstimatedCost metric (Preview). It is "
                    "an estimate, not billed cost: Cost Management stays the system of record. Metrics are kept 93 days.")
    p.add_argument("account", **acct)
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--by", default="project", help="comma-separated: project, deployment, model, version, region "
                                                   "(default: project)")
    p.add_argument("--over-time", action="store_true", help="one row per interval instead of totals")
    p.add_argument("--interval", default="PT1H", help="ISO 8601 interval for --over-time (default PT1H)")

    p = sub.add_parser(
        "prices", help="retail prices per 1M tokens, or the effective prices in your billing",
        description="--source retail: the public Azure retail price list (no sign-in needed). --discount-percent applies a "
                    "discount you know to those prices. --source billed: prices derived from your own billed cost and "
                    "quantity, which already include your discounts. --compare shows the discount your billing implies.")
    p.add_argument("--source", choices=["retail", "billed"], default="retail")
    p.add_argument("--location", default=os.environ.get("FINOPS_LOCATION", "eastus2"), help="region for retail prices")
    p.add_argument("--discount-percent", type=float, default=0.0,
                   help="retail only: reduce every price by this percentage (a value you supply)")
    p.add_argument("--account", default=account_default, help="billed only: the Foundry resource to read billing for")
    p.add_argument("--scope", help="billed only: another Cost Management scope instead of the Foundry resource")
    p.add_argument("--days", type=int, default=30, help="billed only: look-back window (default 30)")
    p.add_argument("--compare", action="store_true", help="billed only: also show the discount against retail")

    p = sub.add_parser(
        "anomalies", help="days whose billed cost jumps above the group's own history",
        description="Statistical outliers in your own billed daily cost (modified z-score against the median of the "
                    "previous days). Not Azure's anomaly alerting; planned changes will show up too.")
    p.add_argument("--by", default="project", help="project, agent, deployment, model, team, or any tag or dimension")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--account", default=account_default)
    p.add_argument("--scope")
    p.add_argument("--threshold", type=float, default=3.5, help="modified z-score cut-off (default 3.5)")
    p.add_argument("--min-increase", type=float, default=0.0, help="ignore changes smaller than this amount")
    p.add_argument("--window", type=int, default=28, help="days of history to compare with (default 28)")
    p.add_argument("--min-history", type=int, default=7, help="days of history needed before a day is judged")
    p.add_argument("--direction", choices=["up", "down", "both"], default="up")

    p = sub.add_parser("ptu", help="provisioned-throughput deployments: capacity, utilisation, quota")
    p.add_argument("account", **acct)
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--location", help="region for the quota lookup (default: the account's region)")

    p = sub.add_parser(
        "allocate", help="spread a shared cost over deployments or models in proportion to their tokens",
        description="An allocation, not billed cost: the total is either --total or the billed cost at --cost-scope over the "
                    "last --days days, split by each group's share of TokenTransaction in the same period.")
    p.add_argument("account", **acct)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--total", type=float, help="the amount to allocate (in your billing currency)")
    g.add_argument("--cost-scope", help="allocate the billed cost at this scope (for example an API Management resource ID)")
    p.add_argument("--by", choices=["deployment", "model"], default="deployment")
    p.add_argument("--days", type=int, default=30)

    p = sub.add_parser("query", help="run a Cost Management query saved as JSON (see CostQuery.save)")
    p.add_argument("file")
    p.add_argument("--scope", help="default: the resource group")


def run(ap: argparse.ArgumentParser, args: argparse.Namespace, emit: Callable[[Any, str], None]) -> int:
    if args.demo:
        ap.error(f"{args.cmd} reads from Azure and can't use --demo")
    retail_only = args.cmd == "prices" and args.source == "retail"
    if not args.subscription and not retail_only:
        ap.error("--subscription (or FINOPS_SUBSCRIPTION_ID) is required")
    for name in ("hours", "days"):
        if getattr(args, name, 1) < 1:
            ap.error(f"--{name} must be at least 1")
    if args.cmd == "prices":
        if args.discount_percent and args.source != "retail":
            ap.error("--discount-percent applies to retail prices only; billed prices already include your discounts")
        if args.compare and args.source != "billed":
            ap.error("--compare needs --source billed")
        if not 0 <= args.discount_percent < 100:
            ap.error("--discount-percent must be at least 0 and below 100")

    if retail_only:  # public API: no Azure sign-in needed
        from ._auth import StaticTokenCredential

        fo = FinOpsClient(StaticTokenCredential("not-used"), args.subscription or "00000000-0000-0000-0000-000000000000")
    else:
        fo = FinOpsClient.from_default_credential(args.subscription, resource_group=args.resource_group or None)
    try:
        return _dispatch(ap, args, emit, fo)
    except FinOpsError as exc:
        print(f"error: {str(exc).splitlines()[0] if str(exc) else exc}", file=sys.stderr)
        return 1
    except (ValueError, KeyError, OSError) as exc:
        print(f"error: {str(exc).strip(chr(39))}", file=sys.stderr)
        return 2
    finally:
        fo.close()


def _need(ap: argparse.ArgumentParser, args: argparse.Namespace, *, rg: bool = True) -> str:
    if not args.account:
        ap.error(f"{args.cmd}: pass the Foundry resource name (or set FINOPS_FOUNDRY_ACCOUNT)")
    if rg and not args.resource_group:
        ap.error(f"{args.cmd}: pass -g/--resource-group (or set FINOPS_RESOURCE_GROUP)")
    return str(args.account)


def _dispatch(ap: argparse.ArgumentParser, args: argparse.Namespace, emit: Callable[[Any, str], None],
              fo: FinOpsClient) -> int:
    fmt = args.format
    if args.cmd == "estimated-cost":
        account = _need(ap, args)
        try:
            dims = tuple(_DIMENSIONS[d.strip()] for d in args.by.split(",") if d.strip())
        except KeyError as exc:
            ap.error(f"--by must be a comma-separated list of {', '.join(_DIMENSIONS)} (got {exc.args[0]!r})")
        if not dims:
            ap.error("--by needs at least one of " + ", ".join(_DIMENSIONS))
        rid = fo.account_id(account)
        rows = (fo.metrics.estimated_cost(rid, hours=args.hours, by=dims, interval=args.interval) if args.over_time
                else fo.metrics.estimated_cost_totals(rid, hours=args.hours, by=dims))
        emit(rows, fmt)
        print("Estimate from the FoundryModelEstimatedCost metric (Preview), in USD. Not billed cost.", file=sys.stderr)
    elif args.cmd == "prices":
        _prices(ap, args, emit, fo)
    elif args.cmd == "anomalies":
        _anomalies(ap, args, emit, fo)
    elif args.cmd == "ptu":
        account = _need(ap, args)
        emit(ptu_headroom(fo, account, hours=args.hours, location=args.location), fmt)
    elif args.cmd == "allocate":
        _allocate(ap, args, emit, fo)
    else:  # query
        q = CostQuery.load(args.file)
        emit(fo.cost.query(q, scope=args.scope), fmt)
    return 0


def _prices(ap: argparse.ArgumentParser, args: argparse.Namespace, emit: Callable[[Any, str], None],
            fo: FinOpsClient) -> None:
    from .operations.pricing import effective_discounts

    if args.source == "retail":
        sheet = fo.pricing.openai_price_sheet(args.location, discount_percent=args.discount_percent)
        emit(sheet.to_records(), args.format)
        note = "Public retail prices per 1M tokens"
        note += f", reduced by {args.discount_percent:g}% as you asked (not read from Azure)." if args.discount_percent else "."
        print(note, file=sys.stderr)
        return
    scope = args.scope or (fo.account_id(_need(ap, args)) if args.account else None)
    effective = fo.cost.effective_prices(scope=scope, days=args.days)
    billed = effective.to_price_sheet()
    if args.compare:
        retail = fo.pricing.openai_price_sheet(args.location)
        emit(effective_discounts(billed, retail), args.format)
        print("discount_percent = (1 - billed / retail) x 100 for models present in both. Positive means cheaper than "
              "retail.", file=sys.stderr)
    else:
        emit(billed.to_records(), args.format)
    if effective.skipped:
        print(f"{len(effective.skipped)} meter(s) had no usable quantity and got no price.", file=sys.stderr)


def _anomalies(ap: argparse.ArgumentParser, args: argparse.Namespace, emit: Callable[[Any, str], None],
               fo: FinOpsClient) -> None:
    from .service import LiveService, Settings

    svc = LiveService(Settings(
        subscription_id=args.subscription, resource_group=args.resource_group or "",
        foundry_account=args.account or "", team_tag=os.environ.get("FINOPS_TEAM_TAG", "team")), fo)
    data = svc.cost_series(args.by, args.days, scope=args.scope)
    table = Table.from_records([dict(zip([c["name"] for c in data["columns"]], r, strict=True)) for r in data["rows"]])
    found = anomalies_from_table(
        table, group_key=args.by, window=args.window, min_history=args.min_history, threshold=args.threshold,
        min_change=args.min_increase, direction=args.direction)
    emit([a.__dict__ for a in found], args.format)
    print(f"{len(found)} day(s) flagged. Method: modified z-score above {args.threshold:g} against the median of the previous "
          f"{args.window} days; needs {args.min_history} days of history. Days without a charge count as zero.",
          file=sys.stderr)


def _allocate(ap: argparse.ArgumentParser, args: argparse.Namespace, emit: Callable[[Any, str], None],
              fo: FinOpsClient) -> None:
    from .operations.monitor import FoundryMetrics

    account = _need(ap, args)
    if args.days > 93:
        ap.error("--days can be at most 93: Azure Monitor keeps metrics for 93 days")
    if args.total is not None:
        total, currency = args.total, None
    else:
        rows = list(fo.cost.query(CostQuery.last_days(args.days).total(), scope=args.cost_scope))
        costs = [(float(r.get("Cost") or r.get("PreTaxCost") or 0), r.get("Currency")) for r in rows]
        if not costs:
            raise ValueError(f"No billed cost at {args.cost_scope} in the last {args.days} days")
        currency = costs[0][1]
        if any(c != currency for _, c in costs):
            raise ValueError("The cost at that scope is in several currencies; allocate one currency at a time")
        total = sum(c for c, _ in costs)
    dim = FoundryMetrics.MODEL_DIMENSION if args.by == "model" else FoundryMetrics.DEPLOYMENT_DIMENSION
    series = fo.metrics.query(fo.account_id(account), [FoundryMetrics.TOTAL_TOKENS], hours=args.days * 24,
                              interval="P1D", split_by=(dim,))
    weights: dict[str, float] = {}
    for s in series:
        key = s.dimensions.get(dim.lower()) or s.dimensions.get(dim) or "(none)"
        weights[key] = weights.get(key, 0.0) + s.total
    rows_out = allocate_cost(total, {k: v for k, v in weights.items() if v > 0})
    for r in rows_out:
        r["currency"] = currency
    emit(rows_out, args.format)
    print(f"Allocation of {total:,.2f}{' ' + str(currency) if currency else ''} by each {args.by}'s share of tokens over "
          f"{args.days} days. It is an allocation rule you chose, not billed cost per {args.by}.", file=sys.stderr)
