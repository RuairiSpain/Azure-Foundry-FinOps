"""Portal settings, read from ``PORTAL_*`` environment variables (App Service application settings)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _list(value: str) -> list[str]:
    return [v.strip() for v in value.replace(";", ",").split(",") if v.strip()]


@dataclass
class PortalSettings:
    """Everything the portal needs. ``mode`` is ``demo`` (illustrative data, pick-a-user sign-in) or ``entra``."""

    mode: str = "demo"
    # --- Entra ID (mode=entra)
    tenant_id: str = ""
    #: Client ID of the portal's app registration (the API; also the SPA's audience).
    client_id: str = ""
    #: Secret for the on-behalf-of exchange. Prefer a Key Vault reference in the App Service setting.
    client_secret: str = ""
    #: Scope the SPA requests, for example ``api://<client-id>/access_as_user``. The API checks ``scp`` for its name.
    required_scope: str = "access_as_user"
    # --- Azure scope the portal manages (one Foundry resource; extend by deploying one portal per scope)
    subscription_id: str = ""
    resource_group: str = ""
    foundry_account: str = ""
    location: str = "eastus2"
    apim_service: str = ""
    team_tag: str = "team"
    #: Log Analytics workspace ID (or resource ID) holding the gateway's records; needed for per-caller usage.
    logs_target: str = ""
    #: ``gateway`` (API Management policy records, experimental) or ``client`` (``llm.usage`` records from the SDK).
    usage_source: str = "gateway"
    # --- portal data
    db_path: str = "portal.db"
    #: Object IDs that are portal administrators from the first start (the rest is managed in the Admin page).
    bootstrap_admins: list[str] = field(default_factory=list)
    #: Origins allowed to call the API from a browser (the portal serves its own frontend, so usually empty).
    cors_origins: list[str] = field(default_factory=list)
    # --- notifications
    #: Shared secret the Event Grid subscription sends in the ``aeg-channel-secret`` query or header.
    alert_webhook_secret: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    allow_private_webhooks: bool = False
    # --- behaviour
    read_only: bool = False
    #: Serve the built frontend from this folder (empty: the packaged ``static`` folder).
    static_dir: str = ""
    brand_name: str = "Foundry FinOps"

    @property
    def demo(self) -> bool:
        return self.mode == "demo"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> PortalSettings:
        e = dict(os.environ if env is None else env)

        def g(name: str, default: str = "") -> str:
            return e.get(f"PORTAL_{name}", default)

        return cls(
            mode=g("MODE", "demo").lower(),
            tenant_id=g("TENANT_ID"), client_id=g("CLIENT_ID"), client_secret=g("CLIENT_SECRET"),
            required_scope=g("REQUIRED_SCOPE", "access_as_user"),
            subscription_id=g("SUBSCRIPTION_ID"), resource_group=g("RESOURCE_GROUP"),
            foundry_account=g("FOUNDRY_ACCOUNT"), location=g("LOCATION", "eastus2"), apim_service=g("APIM_SERVICE"),
            team_tag=g("TEAM_TAG", "team"),
            logs_target=g("LOGS_TARGET"), usage_source=g("USAGE_SOURCE", "gateway").lower(),
            db_path=g("DB_PATH", "portal.db"), bootstrap_admins=_list(g("ADMINS")),
            cors_origins=_list(g("CORS_ORIGINS")),
            alert_webhook_secret=g("ALERT_WEBHOOK_SECRET"),
            smtp_host=g("SMTP_HOST"), smtp_port=int(g("SMTP_PORT", "587") or 587), smtp_user=g("SMTP_USER"),
            smtp_password=g("SMTP_PASSWORD"), smtp_from=g("SMTP_FROM"),
            allow_private_webhooks=g("ALLOW_PRIVATE_WEBHOOKS").lower() in ("1", "true", "yes"),
            read_only=g("READ_ONLY").lower() in ("1", "true", "yes"),
            static_dir=g("STATIC_DIR"), brand_name=g("BRAND_NAME", "Foundry FinOps"),
        )

    def validate(self) -> None:
        """Refuse a configuration that would run without sign-in or without somewhere to read data from."""
        if self.mode not in ("demo", "entra"):
            raise ValueError("PORTAL_MODE must be 'demo' or 'entra'")
        if self.usage_source not in ("gateway", "client"):
            raise ValueError("PORTAL_USAGE_SOURCE must be 'gateway' or 'client'")
        if self.mode == "entra":
            missing = [n for n, v in (("PORTAL_TENANT_ID", self.tenant_id), ("PORTAL_CLIENT_ID", self.client_id),
                                       ("PORTAL_CLIENT_SECRET", self.client_secret),
                                       ("PORTAL_SUBSCRIPTION_ID", self.subscription_id),
                                       ("PORTAL_RESOURCE_GROUP", self.resource_group),
                                       ("PORTAL_FOUNDRY_ACCOUNT", self.foundry_account)) if not v]
            if missing:
                raise ValueError("Missing settings for entra mode: " + ", ".join(missing))
            if not self.bootstrap_admins:
                raise ValueError("PORTAL_ADMINS must list at least one administrator's object ID")
