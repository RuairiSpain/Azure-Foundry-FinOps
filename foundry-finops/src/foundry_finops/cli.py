"""``foundry-finops`` command line: quick reports straight from the terminal.

Examples::

    foundry-finops --subscription <id> -g rg-ai cost-controls contoso-foundry
    foundry-finops --subscription <id> -g rg-ai cost --by project --scope <foundry-resource-id> --format csv
    foundry-finops --subscription <id> -g rg-ai tokens contoso-foundry --hours 48
    foundry-finops --subscription <id> quota --location eastus2
    foundry-finops --demo cost --by team
    foundry-finops --subscription <id> -g rg-ai download --account contoso-foundry --project fraud-risk \
        --start 2026-01-01 --end 2026-03-31          # every query -> ./FinOps/<today>/<query>.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

from .errors import FinOpsError
from .models import Column, Table, dumps
from .service import GROUPINGS


def _emit(data: Any, fmt: str) -> None:
    if isinstance(data, dict) and "columns" in data and "rows" in data:
        cols = [c["name"] for c in data["columns"]]
        data = Table.from_records([dict(zip(cols, r, strict=False)) for r in data["rows"]])
        if not data.columns:  # an empty result still has a header row
            data = Table([Column(c) for c in cols], [])
    elif isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
        data = Table.from_records(data)
    if isinstance(data, Table):
        if fmt == "csv":
            sys.stdout.write(data.to_csv())
        elif fmt == "json":
            print(json.dumps(data.to_records(), indent=2, default=str))
        else:
            _print_table(data.column_names, data.rows)
        return
    print(dumps(data))


def _print_table(cols: list[str], rows: list[list[Any]]) -> None:
    cells = [[("" if v is None else (json.dumps(v) if isinstance(v, (dict, list)) else str(v))) for v in r] for r in rows]
    widths = [min(max([len(c)] + [len(r[i]) for r in cells]), 48) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths, strict=False)))
    print("  ".join("-" * w for w in widths))
    for r in cells:
        print("  ".join(v[:w].ljust(w) for v, w in zip(r, widths, strict=False)))


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a date; use YYYY-MM-DD") from None


def _download(ap: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    from .export import QUERIES, ExportContext, allowed_by_roles, known_role_names, parse_roles, run_export

    auto = args.roles is not None and args.roles.strip().lower() == "auto"
    roles = None if (auto or args.roles is None) else parse_roles(args.roles)
    if roles:
        unknown = [r for r in roles if r.lower() not in known_role_names()]
        if unknown:  # a typo here would silently skip queries, so say so
            print(f"warning: not a role this tool recognises (ignored): {', '.join(unknown)}", file=sys.stderr)
    if args.list and not auto:
        for q in QUERIES:
            mark = "" if roles is None else ("run   " if allowed_by_roles(q, roles) else "skip  ")
            print(f"{mark}{q.name:42} {q.description}")
        return 0
    if args.demo:
        ap.error("download reads from Azure and can't use --demo")
    if not args.subscription:
        ap.error("--subscription (or FINOPS_SUBSCRIPTION_ID) is required")
    if not args.account:
        ap.error("download: pass --account (or set FINOPS_FOUNDRY_ACCOUNT)")
    if not args.resource_group:
        ap.error("download: pass -g/--resource-group (or set FINOPS_RESOURCE_GROUP): the Foundry resource lives in it")
    if not args.list:
        if not args.start or not args.end:
            ap.error("download: --start and --end are required (YYYY-MM-DD)")
        if args.end < args.start:
            ap.error("--end must not be before --start")
    from .service import LiveService, Settings

    team_tag = os.environ.get("FINOPS_TEAM_TAG", "team")
    svc = LiveService(Settings(
        subscription_id=args.subscription, resource_group=args.resource_group or "", foundry_account=args.account,
        apim_service=args.apim_service, location=args.location, logs_target=args.logs_target, team_tag=team_tag,
    ))
    try:
        access = _discover_access(svc, args) if auto else None
        if args.list and access is not None:
            for q in QUERIES:
                print(f"{'run   ' if access.check(q.name) is None else 'skip  '}{q.name:42} {q.description}")
            return 0
        ctx = ExportContext(
            client=svc.fo, service=svc, account=args.account, start=args.start, end=args.end, project=args.project,
            location=args.location, logs_target=args.logs_target, apim_service=args.apim_service, team_tag=team_tag,
        )
        only = [n.strip() for n in args.only.split(",") if n.strip()] if args.only else None
        result = run_export(ctx, Path(args.output_dir), only, roles, access)
    finally:
        svc.fo.close()
    rows = [{"query": r.name, "status": r.status, "rows": r.rows, "detail": r.detail or ""} for r in result.results]
    _emit(rows, "table")
    ok = sum(r.status == "ok" for r in result.results)
    print(f"\n{ok} of {len(result.results)} queries saved to {result.folder}", file=sys.stderr)
    return 1 if result.failed else 0


def _discover_access(svc: Any, args: argparse.Namespace) -> Any:
    """``--roles auto``: read the signed-in identity's real role assignments from Azure."""
    from .roles import discover, summarize

    fo = svc.fo
    sub = f"/subscriptions/{args.subscription}"
    scopes = {
        "subscription": sub,
        "resource_group": fo.cost.resource_group_scope(),
        "account": fo.foundry.account_id(args.account),
        "apim": fo.apim.service_id(args.apim_service) if args.apim_service else fo.cost.resource_group_scope(),
        "anywhere": sub,
    }
    access = discover(fo, scopes)
    if access.grants:
        print("Roles found for the signed-in identity:", file=sys.stderr)
        for line in summarize(access.grants):
            print(f"  {line}", file=sys.stderr)
    else:
        print("warning: no role assignments found for the signed-in identity; only the public queries will run",
              file=sys.stderr)
    return access


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="foundry-finops", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subscription", default=os.environ.get("FINOPS_SUBSCRIPTION_ID"))
    ap.add_argument("-g", "--resource-group", default=os.environ.get("FINOPS_RESOURCE_GROUP"))
    ap.add_argument("--format", choices=["table", "json", "csv"], default="table")
    ap.add_argument("--demo", action="store_true", help="use illustrative demo data (no Azure calls)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    account_default = os.environ.get("FINOPS_FOUNDRY_ACCOUNT")
    p = sub.add_parser("cost-controls", help="list Foundry cost controls")
    p.add_argument("account", nargs="?", default=account_default, help="Foundry resource name (or FINOPS_FOUNDRY_ACCOUNT)")
    p = sub.add_parser("cost", help="billed cost grouped by project/agent/deployment/model/team")
    p.add_argument("--by", default="project", choices=GROUPINGS)
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--account", default=account_default, help="Foundry resource name (project/agent/deployment tags)")
    p = sub.add_parser("tokens", help="token metrics per deployment")
    p.add_argument("account", nargs="?", default=account_default, help="Foundry resource name (or FINOPS_FOUNDRY_ACCOUNT)")
    p.add_argument("--hours", type=int, default=24)
    p = sub.add_parser(
        "download", help="save every query's JSON to FinOps/<today>/<query>.json",
        description="Run every read query the SDK offers for a Foundry resource (optionally one project) over a date "
                    "range and save each result as <query-name>.json in ./FinOps/<today>/, plus _manifest.json.",
    )
    p.add_argument("--account", default=account_default, help="Foundry resource name (or FINOPS_FOUNDRY_ACCOUNT)")
    p.add_argument("--project", help="limit cost data to this Foundry project (billing tag `project`)")
    p.add_argument("--start", type=_date, help="first day, YYYY-MM-DD")
    p.add_argument("--end", type=_date, help="last day (inclusive), YYYY-MM-DD")
    p.add_argument("--output-dir", default=".", help="where the FinOps folder is created (default: working directory)")
    p.add_argument("--only", help="comma-separated query names to run (default: all)")
    p.add_argument("--roles", help="the identity's Azure roles, comma-separated (e.g. 'Reader,Monitoring Reader'), or "
                                   "'auto' to read them from Azure: only the queries those roles allow are run. "
                                   "Default: try everything")
    p.add_argument("--list", action="store_true", help="list the query names and exit")
    p.add_argument("--location", default=os.environ.get("FINOPS_LOCATION", "eastus2"), help="region for quota and prices")
    p.add_argument("--logs-target", default=os.environ.get("FINOPS_LOGS_TARGET", ""),
                   help="Log Analytics workspace ID or App Insights resource ID, for the KQL queries")
    p.add_argument("--apim-service", default=os.environ.get("FINOPS_APIM_SERVICE", ""),
                   help="API Management instance, for the gateway query")
    sub.add_parser("budgets", help="Cost Management budgets")
    p = sub.add_parser("quota", help="regional model quota")
    p.add_argument("--location", default=os.environ.get("FINOPS_LOCATION", "eastus2"))
    p = sub.add_parser("kql", help="run a KQL query")
    p.add_argument("target", help="workspace ID or App Insights resource ID (ignored in --demo)")
    p.add_argument("query")
    p.add_argument("--timespan", default="P1D")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    for name in ("days", "hours"):
        if getattr(args, name, 1) < 1:
            ap.error(f"--{name} must be at least 1")
    if args.cmd == "download":
        try:
            return _download(ap, args)
        except FinOpsError as exc:
            lines = str(exc).splitlines() or [""]
            print(f"error: {lines[0]}", file=sys.stderr)
            if "access token" in lines[0]:
                print("hint: sign in first (for example `az login`) or set the AZURE_* environment variables.",
                      file=sys.stderr)
            return 1
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if args.demo:
        from .service import DemoService

        svc: Any = DemoService()
    else:
        if not args.subscription:
            # Never fall back to demo data silently: invented numbers in a spend report are worse than an error.
            ap.error("--subscription (or FINOPS_SUBSCRIPTION_ID) is required; use --demo for illustrative data")
        if args.cmd in ("cost-controls", "tokens") and not args.account:
            ap.error(f"{args.cmd}: pass the Foundry resource name (or set FINOPS_FOUNDRY_ACCOUNT)")
        from .service import LiveService, Settings

        svc = LiveService(Settings(
            subscription_id=args.subscription, resource_group=args.resource_group or "",
            foundry_account=getattr(args, "account", None) or "",
            location=getattr(args, "location", None) or "eastus2",
            logs_target=getattr(args, "target", None) or "",
            team_tag=os.environ.get("FINOPS_TEAM_TAG", "team"),
        ))

    try:
        result: Any
        if args.cmd == "cost-controls":
            result = svc.list_cost_controls()
        elif args.cmd == "cost":
            result = svc.cost_series(args.by, args.days)
        elif args.cmd == "tokens":
            result = svc.token_series("deployment", args.hours)
        elif args.cmd == "budgets":
            result = svc.list_budgets()
        elif args.cmd == "quota":
            result = svc.list_quota()
        else:
            result = svc.kql(args.query, args.timespan)
        _emit(result, args.format)
    except FinOpsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (ValueError, KeyError) as exc:
        print(f"error: {str(exc).strip(chr(39))}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # `foundry-finops ... | head`
        return 0
    finally:
        fo = getattr(svc, "fo", None)
        if fo is not None:
            fo.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
