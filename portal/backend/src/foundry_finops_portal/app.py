"""The portal's HTTP API (FastAPI) and the static frontend."""

from __future__ import annotations

import hmac
import json
import logging
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from azure.core.exceptions import HttpResponseError as AzureHttpResponseError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from foundry_finops import CostControlAlertEvent
from foundry_finops.service import ReadOnlyError
from pydantic import BaseModel, Field

from .access import Access, ScopeRef
from .auth import Authenticator, EntraTokenValidator, Principal
from .azure_access import AzureClientFactory, ClientFactory, PermissionChecker
from .backends import BackendProvider
from .config import PortalSettings
from .db import CHANNELS, ROLES, SCOPE_TYPES, TRIGGERS, Store
from .directory import Directory, DirectoryUser, GraphDirectory, StaticDirectory, is_valid_identifier
from .notifications import Dispatcher, validate_webhook_target
from .seed import seed_demo
from .services import Views
from .writes import (
    BUDGET_DELETE,
    BUDGET_WRITE,
    TAGS_WRITE,
    WriteGuard,
)

logger = logging.getLogger("foundry_finops_portal")


# ------------------------------------------------------------- request bodies
class GrantIn(BaseModel):
    scope_type: str
    scope_value: str = ""
    can_write: bool = False


class UserIn(BaseModel):
    identifier: str = Field(description="Object ID or user principal name of someone who exists in Entra ID")
    role: str = "viewer"
    grants: list[GrantIn] = []


class UserPatch(BaseModel):
    role: str | None = None
    enabled: bool | None = None


class CostCentreIn(BaseModel):
    code: str
    name: str
    owner_oid: str = ""
    description: str = ""
    monthly_budget: float | None = None


class RuleIn(BaseModel):
    match_type: str
    match_value: str
    match_key: str = ""
    share_percent: float = 100


class NotificationIn(BaseModel):
    name: str
    scope_type: str = "all"
    scope_value: str = ""
    trigger: str
    threshold: float | None = None
    channel: str
    target: str = ""
    enabled: bool = True


class TagsIn(BaseModel):
    scope: str
    tags: dict[str, str]


@dataclass
class Ctx:
    """Everything a route needs about the current request."""

    principal: Principal
    access: Access
    views: Views
    backend: Any


def create_app(
    settings: PortalSettings | None = None,
    *,
    store: Store | None = None,
    directory: Directory | None = None,
    authenticator: Authenticator | None = None,
    clients: ClientFactory | None = None,
    permissions: PermissionChecker | None = None,
    dispatcher: Dispatcher | None = None,
    key_resolver: Callable[[str], Any] | None = None,
    backends: BackendProvider | None = None,
) -> FastAPI:
    """Build the application. Every collaborator can be replaced, which is how the tests run without Azure."""
    settings = settings or PortalSettings.from_env()
    settings.validate()
    store = store or Store(settings.db_path)
    if settings.demo:
        seed_demo(store)
        directory = directory or StaticDirectory([
            DirectoryUser("11111111-1111-1111-1111-111111111111", "alice.admin@contoso.example", "Alice Admin"),
            DirectoryUser("22222222-2222-2222-2222-222222222222", "bob.risk@contoso.example", "Bob (owner, Risk)"),
            DirectoryUser("33333333-3333-3333-3333-333333333333", "carol.care@contoso.example", "Carol (viewer, Support)"),
            DirectoryUser("44444444-4444-4444-4444-444444444444", "dave.nobody@contoso.example", "Dave"),
            DirectoryUser("55555555-5555-5555-5555-555555555555", "erin.finance@contoso.example", "Erin Finance"),
        ])
        authenticator = authenticator or Authenticator(None, demo=True)
    else:
        if clients is None:
            clients = AzureClientFactory(settings)
        if authenticator is None:
            authenticator = Authenticator(EntraTokenValidator(
                settings.tenant_id, settings.client_id, settings.required_scope, key_resolver))
        if directory is None:
            directory = GraphDirectory(clients.service()._transport.credential)  # the portal's own identity
        if permissions is None:
            permissions = clients.permissions()
        for oid in settings.bootstrap_admins:
            if not store.get_user(oid):
                store.upsert_user(oid, "", "Bootstrap administrator", "admin", added_by="PORTAL_ADMINS")
            elif store.get_user(oid)["role"] != "admin":  # type: ignore[index]
                store.update_user(oid, role="admin")
    backends = backends or BackendProvider(settings, clients)
    guard = WriteGuard(settings, store, permissions)
    dispatcher = dispatcher or Dispatcher(settings, store)
    assert authenticator is not None and directory is not None
    auth = authenticator
    directory_ = directory

    def home() -> ScopeRef:
        return ScopeRef(settings.subscription_id or "demo", settings.resource_group or "rg-ai-demo",
                        settings.foundry_account or "contoso-foundry")

    def build_access(row: dict[str, Any]) -> Access:
        centres = {cc["code"]: {r["match_value"] for r in cc["rules"] if r["match_type"] == "project"}
                   for cc in store.list_cost_centres()}
        return Access(row["oid"], row["role"], store.grants_for(row["oid"]), centres, home())

    def ctx(request: Request) -> Ctx:
        principal = auth(request)
        row = store.get_user(principal.oid)
        if row is None and settings.bootstrap_admins and principal.oid in settings.bootstrap_admins:
            row = store.upsert_user(principal.oid, principal.upn, principal.name, "admin", added_by="PORTAL_ADMINS")
        if row is None or not row["enabled"]:
            raise HTTPException(403, "You're signed in, but an administrator hasn't given you access to this portal yet.")
        if principal.upn and row["upn"] != principal.upn:
            row = store.upsert_user(row["oid"], principal.upn, principal.name or row["display_name"], row["role"],
                                    added_by=row["added_by"], enabled=bool(row["enabled"]))
        access = build_access(row)
        backend = backends.for_user(principal)
        return Ctx(principal, access, Views(store, access, backend), backend)

    def admin(c: Ctx = Depends(ctx)) -> Ctx:
        if not c.access.is_admin:
            raise HTTPException(403, "Portal administrators only")
        return c

    # ----------------------------------------------------------------- app
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        stop = threading.Event()
        if not settings.demo and settings.mode == "entra":
            def poll() -> None:
                while not stop.wait(900):  # every 15 minutes, with the portal's own identity
                    try:
                        dispatcher.evaluate(backends.for_service().list_cost_controls())
                    except Exception as exc:
                        logger.warning("Threshold notification poll failed: %s", exc)

            threading.Thread(target=poll, name="notification-poll", daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="Foundry FinOps portal", version="0.1.0", lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json")
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                           allow_headers=["Authorization", "Content-Type", "X-Demo-User"])
    app.state.store, app.state.settings = store, settings

    @app.exception_handler(ReadOnlyError)
    async def _readonly(_: Request, exc: ReadOnlyError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(ValueError)
    async def _value(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc).strip("'")}, status_code=400)

    @app.exception_handler(AzureHttpResponseError)
    async def _azure(_: Request, exc: AzureHttpResponseError) -> JSONResponse:
        status = exc.status_code if exc.status_code in (401, 403, 404, 409, 412, 429) else 502
        return JSONResponse({"detail": f"Azure: {getattr(exc, 'message', None) or exc}"}, status_code=status)

    # ------------------------------------------------------------ public
    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": settings.mode}

    @app.get("/api/config")
    def public_config() -> dict[str, Any]:
        out: dict[str, Any] = {"mode": settings.mode, "brand": settings.brand_name, "read_only": settings.read_only}
        if settings.demo:
            from .auth import DEMO_USERS

            out["demo_users"] = [{"oid": u.oid, "name": u.name, "upn": u.upn} for u in DEMO_USERS.values()]
        else:
            out["msal"] = {"client_id": settings.client_id, "tenant_id": settings.tenant_id,
                           "authority": f"https://login.microsoftonline.com/{settings.tenant_id}",
                           "scope": f"api://{settings.client_id}/{settings.required_scope}"}
        return out

    @app.get("/api/me")
    def me(c: Ctx = Depends(ctx)) -> dict[str, Any]:
        return {"oid": c.principal.oid, "upn": c.principal.upn, "name": c.principal.name, "role": c.access.role,
                "grants": c.access.grants, "account_level": c.access.account_level,
                "visible_projects": sorted(c.views.visible_projects() or []) if c.views.visible_projects() is not None else None,
                "mode": settings.mode, "read_only": settings.read_only,
                "azure": {"subscription": settings.subscription_id, "resource_group": settings.resource_group,
                          "account": settings.foundry_account}}

    # -------------------------------------------------------------- views
    @app.get("/api/fleet")
    def fleet(days: int = Query(30, ge=1, le=90), c: Ctx = Depends(ctx)) -> dict[str, Any]:
        return c.views.fleet(days)

    @app.get("/api/operations")
    def operations(hours: int = Query(168, ge=1, le=2160), c: Ctx = Depends(ctx)) -> dict[str, Any]:
        return c.views.operations(hours)

    @app.get("/api/cost/overview")
    def cost_overview(days: int = Query(30, ge=1, le=90), c: Ctx = Depends(ctx)) -> dict[str, Any]:
        return c.views.cost_overview(days)

    @app.get("/api/cost/series")
    def cost_series(group_by: str = "project", days: int = Query(30, ge=1, le=90),
                    c: Ctx = Depends(ctx)) -> dict[str, Any]:
        return c.views.cost_series(group_by, days)

    @app.get("/api/cost/table")
    def cost_table(group_by: str = "project", days: int = Query(30, ge=1, le=90),
                   c: Ctx = Depends(ctx)) -> dict[str, Any]:
        return c.views.cost_table(group_by, days)

    @app.get("/api/projects/{project}/monitor")
    def project_monitor(project: str, days: int = Query(30, ge=1, le=90), c: Ctx = Depends(ctx)) -> dict[str, Any]:
        return c.views.project_monitor(project, days)

    @app.get("/api/budgets")
    def budgets(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
        return c.views.budgets()

    @app.get("/api/tags")
    def tags(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
        return c.views.tags()

    @app.get("/api/alert-events")
    def alert_events(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
        return c.views.alert_events()

    # ------------------------------------------------------- cost controls
    @app.get("/api/cost-controls")
    def cost_controls(project: str | None = None, c: Ctx = Depends(ctx)) -> dict[str, Any]:
        return c.views.cost_controls(project)

    @app.post("/api/cost-controls")
    def save_cost_control(payload: dict[str, Any], c: Ctx = Depends(ctx)) -> dict[str, Any]:
        project = payload.get("project") or None
        rid, action = guard.cost_control_target(project)
        guard.authorize(c.principal, c.access, ref=ScopeRef(project=project, **_home_kw(home())), resource_id=rid,
                        action=action, label="cost_control.save", detail={"name": payload.get("name"), "project": project})
        try:
            out = backends.for_service().upsert_cost_control(payload)
        except Exception as exc:
            guard.done(c.principal, "cost_control.save", rid, {"name": payload.get("name")}, error=str(exc))
            raise
        guard.done(c.principal, "cost_control.save", rid, {"name": payload.get("name"), "amount": payload.get("amount")})
        return dict(out)

    @app.delete("/api/cost-controls/{name}")
    def delete_cost_control(name: str, project: str | None = None, c: Ctx = Depends(ctx)) -> dict[str, str]:
        rid, action = guard.cost_control_target(project, delete=True)
        guard.authorize(c.principal, c.access, ref=ScopeRef(project=project, **_home_kw(home())), resource_id=rid,
                        action=action, label="cost_control.delete", detail={"name": name, "project": project})
        try:
            backends.for_service().delete_cost_control(name, project)
        except Exception as exc:
            guard.done(c.principal, "cost_control.delete", rid, {"name": name}, error=str(exc))
            raise
        guard.done(c.principal, "cost_control.delete", rid, {"name": name})
        return {"deleted": name}

    # ------------------------------------------------------------- budgets, tags
    @app.post("/api/budgets")
    def save_budget(payload: dict[str, Any], c: Ctx = Depends(ctx)) -> dict[str, Any]:
        guard.authorize(c.principal, c.access, ref=ScopeRef(**_home_kw(home())), resource_id=guard.rg_id,
                        action=BUDGET_WRITE, label="budget.save", detail={"name": payload.get("name")})
        out = backends.for_service().upsert_budget(payload)
        guard.done(c.principal, "budget.save", guard.rg_id, {"name": payload.get("name")})
        return dict(out)

    @app.delete("/api/budgets/{name}")
    def delete_budget(name: str, c: Ctx = Depends(ctx)) -> dict[str, str]:
        guard.authorize(c.principal, c.access, ref=ScopeRef(**_home_kw(home())), resource_id=guard.rg_id,
                        action=BUDGET_DELETE, label="budget.delete", detail={"name": name})
        backends.for_service().delete_budget(name)
        guard.done(c.principal, "budget.delete", guard.rg_id, {"name": name})
        return {"deleted": name}

    @app.put("/api/tags")
    def set_tags(body: TagsIn, c: Ctx = Depends(ctx)) -> dict[str, str]:
        guard.authorize(c.principal, c.access, ref=ScopeRef(**_home_kw(home())), resource_id=body.scope,
                        action=TAGS_WRITE, label="tags.set", detail={"scope": body.scope, "keys": sorted(body.tags)})
        out = backends.for_service().set_tags(body.scope, body.tags)
        guard.done(c.principal, "tags.set", body.scope, {"keys": sorted(body.tags)})
        return dict(out)

    # ------------------------------------------------------------ cost centres
    @app.get("/api/cost-centres")
    def list_centres(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
        centres = store.list_cost_centres()
        visible = c.views.visible_projects()
        if visible is None:
            return centres
        return [cc for cc in centres if any(r["match_type"] == "project" and r["match_value"] in visible for r in cc["rules"])]

    @app.get("/api/cost-centres/report")
    def centre_report(basis: str = "project", days: int = Query(30, ge=1, le=90),
                      c: Ctx = Depends(ctx)) -> dict[str, Any]:
        return c.views.cost_centre_report(basis, days)

    @app.post("/api/cost-centres")
    def create_centre(body: CostCentreIn, c: Ctx = Depends(admin)) -> dict[str, Any]:
        try:
            out = store.save_cost_centre(body.code, body.name, body.owner_oid, body.description, body.monthly_budget)
        except Exception as exc:
            raise HTTPException(400, f"Could not save the cost centre: {exc}") from exc
        store.audit(c.principal.oid, c.principal.upn, "cost_centre.create", body.code)
        return out

    @app.put("/api/cost-centres/{cc_id}")
    def update_centre(cc_id: int, body: CostCentreIn, c: Ctx = Depends(admin)) -> dict[str, Any]:
        if store.get_cost_centre(cc_id) is None:
            raise HTTPException(404, "No such cost centre")
        try:
            out = store.save_cost_centre(body.code, body.name, body.owner_oid, body.description, body.monthly_budget, cc_id)
        except Exception as exc:
            raise HTTPException(400, f"Could not save the cost centre: {exc}") from exc
        store.audit(c.principal.oid, c.principal.upn, "cost_centre.update", body.code)
        return out

    @app.delete("/api/cost-centres/{cc_id}")
    def delete_centre(cc_id: int, c: Ctx = Depends(admin)) -> dict[str, bool]:
        ok = store.delete_cost_centre(cc_id)
        store.audit(c.principal.oid, c.principal.upn, "cost_centre.delete", str(cc_id))
        return {"deleted": ok}

    @app.post("/api/cost-centres/{cc_id}/rules")
    def add_rule(cc_id: int, body: RuleIn, c: Ctx = Depends(admin)) -> dict[str, Any]:
        centre = store.get_cost_centre(cc_id)
        if centre is None:
            raise HTTPException(404, "No such cost centre")
        # One value of one dimension can be shared across cost centres, but never above 100 %.
        used = sum(r["share_percent"] for cc in store.list_cost_centres() for r in cc["rules"]
                   if (r["match_type"], r["match_key"], r["match_value"]) == (body.match_type, body.match_key, body.match_value))
        if used + body.share_percent > 100.0001:
            raise HTTPException(400, f"{body.match_value!r} is already allocated {used:g} %; the shares can't exceed 100 %")
        try:
            out = store.add_rule(cc_id, body.match_type, body.match_value, body.match_key, body.share_percent)
        except Exception as exc:
            raise HTTPException(400, f"Could not add the rule: {exc}") from exc
        store.audit(c.principal.oid, c.principal.upn, "cost_centre.rule.add", centre["code"],
                    {"match_type": body.match_type, "match_value": body.match_value, "share": body.share_percent})
        return out

    @app.delete("/api/cost-centres/rules/{rule_id}")
    def delete_rule(rule_id: int, c: Ctx = Depends(admin)) -> dict[str, bool]:
        ok = store.delete_rule(rule_id)
        store.audit(c.principal.oid, c.principal.upn, "cost_centre.rule.delete", str(rule_id))
        return {"deleted": ok}

    # ----------------------------------------------------- notification rules
    def _rule_scope_ref(body: NotificationIn) -> ScopeRef:
        base = _home_kw(home())
        if body.scope_type == "project":
            return ScopeRef(project=body.scope_value, **base)
        if body.scope_type == "cost_centre":
            return ScopeRef(cost_centre=body.scope_value, **base)
        return ScopeRef(**base)

    def _check_rule(body: NotificationIn, c: Ctx) -> None:
        if body.scope_type not in SCOPE_TYPES or body.trigger not in TRIGGERS or body.channel not in CHANNELS:
            raise HTTPException(400, "Unknown scope type, trigger or channel")
        if body.scope_type != "all" and not body.scope_value.strip():
            raise HTTPException(400, "scope_value is required for this scope type")
        if body.channel == "webhook":
            try:
                validate_webhook_target(body.target, settings.allow_private_webhooks)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        if body.channel == "email" and "@" not in body.target:
            raise HTTPException(400, "An email rule needs an address in target")
        if not c.access.can_write(_rule_scope_ref(body)):
            raise HTTPException(403, "You can manage notification rules only for scopes you have write access to")

    @app.get("/api/notification-rules")
    def list_rules(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
        rules = store.list_notification_rules()
        if c.access.is_admin:
            return rules
        return [r for r in rules if c.access.can_read(_rule_scope_ref(NotificationIn(**{
            k: r[k] for k in ("name", "scope_type", "scope_value", "trigger", "channel")})))]

    @app.post("/api/notification-rules")
    def create_rule(body: NotificationIn, c: Ctx = Depends(ctx)) -> dict[str, Any]:
        _check_rule(body, c)
        try:
            out = store.save_notification_rule(body.model_dump(), c.principal.oid)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        store.audit(c.principal.oid, c.principal.upn, "notification_rule.create", body.name)
        return out

    @app.put("/api/notification-rules/{rule_id}")
    def update_rule(rule_id: int, body: NotificationIn, c: Ctx = Depends(ctx)) -> dict[str, Any]:
        existing = next((r for r in store.list_notification_rules() if r["id"] == rule_id), None)
        if existing is None:
            raise HTTPException(404, "No such rule")
        _check_rule(body, c)
        try:
            out = store.save_notification_rule(body.model_dump(), c.principal.oid, rule_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        store.audit(c.principal.oid, c.principal.upn, "notification_rule.update", body.name)
        return out

    @app.delete("/api/notification-rules/{rule_id}")
    def delete_rule_(rule_id: int, c: Ctx = Depends(ctx)) -> dict[str, bool]:
        existing = next((r for r in store.list_notification_rules() if r["id"] == rule_id), None)
        if existing is None:
            raise HTTPException(404, "No such rule")
        if not c.access.can_write(_rule_scope_ref(NotificationIn(**{k: existing[k] for k in (
                "name", "scope_type", "scope_value", "trigger", "channel")}))):
            raise HTTPException(403, "You can manage notification rules only for scopes you have write access to")
        ok = store.delete_notification_rule(rule_id)
        store.audit(c.principal.oid, c.principal.upn, "notification_rule.delete", str(rule_id))
        return {"deleted": ok}

    @app.post("/api/notification-rules/evaluate")
    def evaluate_now(c: Ctx = Depends(admin)) -> dict[str, int]:
        return {"sent": dispatcher.evaluate(c.backend.list_cost_controls())}

    # -------------------------------------------------- Event Grid alert intake
    @app.options("/api/events/cost-control-alert")
    def event_handshake(request: Request) -> Response:
        """CloudEvents delivery validation: allow Event Grid to send to this endpoint."""
        origin = request.headers.get("webhook-request-origin", "")
        return Response(status_code=200, headers={"WebHook-Allowed-Origin": origin, "WebHook-Allowed-Rate": "*",
                                                   "Allow": "POST, OPTIONS"})

    @app.post("/api/events/cost-control-alert")
    async def receive_event(request: Request) -> dict[str, int]:
        secret = settings.alert_webhook_secret
        if not secret:
            raise HTTPException(503, "Alert intake is off: set PORTAL_ALERT_WEBHOOK_SECRET")
        given = request.query_params.get("secret") or request.headers.get("x-portal-secret", "")
        if not hmac.compare_digest(given.encode(), secret.encode()):
            raise HTTPException(401, "Bad secret")
        body = json.loads(await request.body() or b"[]")
        events = body if isinstance(body, list) else [body]
        stored = sent = 0
        for raw in events:
            try:
                ev = CostControlAlertEvent.from_cloud_event(raw)
            except ValueError:
                continue  # not a cost control alert
            key = json.dumps(ev.dedupe_key_for("agent" if ev.agent_name else "project" if ev.project_name else None))
            row = store.add_alert_event(key, raw)
            if row is None:
                continue  # Foundry sends one event per request over a threshold: this one was already stored
            stored += 1
            sent += dispatcher.handle_event(row["id"], raw)
        return {"stored": stored, "notified": sent}

    # ------------------------------------------------------------------ admin
    @app.get("/api/admin/users")
    def admin_users(c: Ctx = Depends(admin)) -> list[dict[str, Any]]:
        return store.list_users()

    @app.get("/api/admin/directory/lookup")
    def directory_lookup(identifier: str, c: Ctx = Depends(admin)) -> dict[str, Any]:
        if not is_valid_identifier(identifier):
            raise HTTPException(400, "Enter an object ID or a user principal name such as name@contoso.com")
        found = directory_.lookup(identifier)
        if found is None:
            raise HTTPException(404, "Entra ID has no such user")
        return {"oid": found.oid, "upn": found.upn, "display_name": found.display_name, "enabled": found.enabled}

    @app.post("/api/admin/users")
    def add_user(body: UserIn, c: Ctx = Depends(admin)) -> dict[str, Any]:
        if body.role not in ROLES:
            raise HTTPException(400, f"role must be one of {ROLES}")
        found = directory_.lookup(body.identifier.strip()) if is_valid_identifier(body.identifier.strip()) else None
        if found is None:
            raise HTTPException(404, "Entra ID has no such user. A person must already exist in your directory.")
        if not found.enabled:
            raise HTTPException(400, "That Entra account is disabled")
        if store.get_user(found.oid):
            raise HTTPException(409, "That person already has access; change their role or grants instead")
        user = store.upsert_user(found.oid, found.upn, found.display_name, body.role, added_by=c.principal.upn)
        try:
            store.replace_grants(found.oid, [g.model_dump() for g in body.grants])
        except ValueError as exc:
            store.delete_user(found.oid)
            raise HTTPException(400, str(exc)) from exc
        store.audit(c.principal.oid, c.principal.upn, "user.add", found.upn, {"role": body.role})
        return {**user, "grants": store.grants_for(found.oid)}

    def _last_admin(oid: str) -> bool:
        target = store.get_user(oid)
        return bool(target and target["role"] == "admin" and target["enabled"] and store.count_admins() <= 1)

    @app.patch("/api/admin/users/{oid}")
    def patch_user(oid: str, body: UserPatch, c: Ctx = Depends(admin)) -> dict[str, Any]:
        if store.get_user(oid) is None:
            raise HTTPException(404, "No such user")
        if _last_admin(oid) and (body.role not in (None, "admin") or body.enabled is False):
            raise HTTPException(400, "There must be at least one enabled administrator")
        try:
            out = store.update_user(oid, role=body.role, enabled=body.enabled)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        store.audit(c.principal.oid, c.principal.upn, "user.update", oid, body.model_dump(exclude_none=True))
        return out or {}

    @app.put("/api/admin/users/{oid}/grants")
    def put_grants(oid: str, grants: list[GrantIn], c: Ctx = Depends(admin)) -> list[dict[str, Any]]:
        if store.get_user(oid) is None:
            raise HTTPException(404, "No such user")
        try:
            out = store.replace_grants(oid, [g.model_dump() for g in grants])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        store.audit(c.principal.oid, c.principal.upn, "user.grants", oid, {"grants": [g.model_dump() for g in grants]})
        return out

    @app.delete("/api/admin/users/{oid}")
    def remove_user(oid: str, c: Ctx = Depends(admin)) -> dict[str, bool]:
        if store.get_user(oid) is None:
            raise HTTPException(404, "No such user")
        if _last_admin(oid):
            raise HTTPException(400, "There must be at least one enabled administrator")
        ok = store.delete_user(oid)
        store.audit(c.principal.oid, c.principal.upn, "user.remove", oid)
        return {"deleted": ok}

    @app.get("/api/admin/audit")
    def audit_log(limit: int = Query(200, ge=1, le=1000), c: Ctx = Depends(admin)) -> list[dict[str, Any]]:
        return store.list_audit(limit)

    # ---------------------------------------------------------------- access help
    @app.get("/api/access/pages")
    def access_pages(c: Ctx = Depends(ctx)) -> list[dict[str, Any]]:
        """Which pages the person can use, and the Azure permission each one reads (the portal's version of the
        Foundry docs' 'Permissions for each page')."""
        pages = [
            ("Fleet", ["Microsoft.CostManagement/query/read", "Microsoft.Insights/metrics/read",
                       "Microsoft.PolicyInsights/*/read"], False),
            ("Operations", ["Microsoft.Insights/metrics/read"], True),
            ("Cost", ["Microsoft.CostManagement/query/read", "Microsoft.CostManagement/forecast/read"], False),
            ("Cost controls", ["Microsoft.CognitiveServices/accounts/costControls/read",
                               "Microsoft.CognitiveServices/accounts/projects/costControls/read"], False),
            ("Budgets", ["Microsoft.Consumption/budgets/read"], True),
        ]
        out = []
        for name, perms, needs_account in pages:
            allowed = c.access.account_level or not needs_account
            out.append({"page": name, "azure_permissions": perms, "portal_access": allowed,
                        "message": None if allowed else "Your portal access is limited to projects."})
        return out

    # ---------------------------------------------------------------- frontend
    static = Path(settings.static_dir) if settings.static_dir else Path(__file__).parent / "static"
    if (static / "index.html").exists():
        index = static / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404, "No such endpoint")
            candidate = (static / path).resolve()
            if path and candidate.is_file() and static.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index)

    return app


def _home_kw(ref: ScopeRef) -> dict[str, str]:
    return {"subscription": ref.subscription, "resource_group": ref.resource_group, "account": ref.account}


def create_app_from_env() -> FastAPI:
    """The entry point for ``uvicorn foundry_finops_portal.app:create_app_from_env --factory``."""
    return create_app()
