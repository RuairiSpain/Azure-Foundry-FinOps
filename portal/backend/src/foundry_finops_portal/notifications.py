"""Sending notifications for alert events and for cost controls that cross a threshold."""

from __future__ import annotations

import ipaddress
import logging
import smtplib
import socket
import urllib.parse
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

import requests

from .config import PortalSettings
from .db import Store

logger = logging.getLogger("foundry_finops_portal.notifications")


def validate_webhook_target(url: str, allow_private: bool = False) -> None:
    """A webhook target must be https and must not point at a private, loopback or link-local address.

    An administrator chooses the URL, so this is the guard against the portal being used to reach internal services
    (or the Azure metadata endpoint). It resolves the name once when the rule is saved and again when it is used.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("A webhook target must be an https URL")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Can't resolve {parts.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("A webhook target must not resolve to a private or internal address")


class Dispatcher:
    """Delivers a message through a rule's channel: ``webhook`` (JSON with a ``text`` field that Teams and Slack
    incoming webhooks accept), ``email`` (SMTP, when configured) or ``log``."""

    def __init__(self, settings: PortalSettings, store: Store, session: requests.Session | None = None,
                 smtp_factory: Callable[..., Any] | None = None) -> None:
        self.settings, self.store = settings, store
        self._session = session or requests.Session()
        self._smtp = smtp_factory or smtplib.SMTP

    def send(self, rule: dict[str, Any], subject: str, body: str, data: dict[str, Any] | None = None) -> bool:
        channel = rule["channel"]
        try:
            if channel == "webhook":
                validate_webhook_target(rule["target"], self.settings.allow_private_webhooks)
                resp = self._session.post(rule["target"], json={"text": f"{subject}\n{body}", "subject": subject,
                                                               "body": body, "data": data or {}}, timeout=10)
                resp.raise_for_status()
            elif channel == "email":
                if not self.settings.smtp_host:
                    logger.warning("Email rule %r skipped: PORTAL_SMTP_HOST is not set", rule["name"])
                    return False
                msg = EmailMessage()
                msg["Subject"], msg["From"], msg["To"] = subject, self.settings.smtp_from, rule["target"]
                msg.set_content(body)
                with self._smtp(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as s:
                    s.starttls()
                    if self.settings.smtp_user:
                        s.login(self.settings.smtp_user, self.settings.smtp_password)
                    s.send_message(msg)
            else:
                logger.info("Notification %s: %s", rule["name"], subject)
        except Exception as exc:
            logger.warning("Notification %r failed: %s", rule["name"], exc)
            return False
        if rule.get("id"):
            self.store.mark_rule_fired(int(rule["id"]))
        return True

    # ------------------------------------------------------------- alert events
    def _centre_projects(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for cc in self.store.list_cost_centres():
            out[cc["code"]] = {r["match_value"] for r in cc["rules"] if r["match_type"] == "project"}
        return out

    def rule_matches_project(self, rule: dict[str, Any], project: str, centres: dict[str, set[str]]) -> bool:
        t, v = rule["scope_type"], rule["scope_value"]
        if t in ("all", "subscription", "resource_group", "account"):
            return True
        if t == "project":
            return bool(v == project)
        if t == "cost_centre":
            return project in centres.get(v, set())
        return False

    def handle_event(self, event_id: int, event: dict[str, Any]) -> int:
        """Send the rules that watch for alert events. Returns how many were delivered."""
        data = event.get("data") or {}
        project = str(data.get("projectName", ""))
        centres = self._centre_projects()
        sent = 0
        for rule in self.store.list_notification_rules():
            if not rule["enabled"] or rule["trigger"] != "event":
                continue
            if not self.rule_matches_project(rule, project, centres):
                continue
            subject = f"[{data.get('severity', 'warning')}] Foundry cost control reached {data.get('threshold', '')}"
            body = (f"{data.get('agentName') or project or 'The Foundry resource'} used {data.get('totalConsumed')} of "
                    f"{data.get('limit')} {data.get('unit', '')} until {data.get('windowEnd', 'the next reset')}.")
            sent += self.send(rule, subject, body, data)
        if sent:
            self.store.mark_notified(event_id)
        return sent

    # ------------------------------------------------------- threshold polling
    def evaluate(self, cost_controls: list[dict[str, Any]], throttle_hours: int = 24) -> int:
        """Send the ``percent_used`` and ``state`` rules for cost controls that are over them now.

        ``cost_controls`` are the rows of the reporting facade (usage is an estimate). A rule that fired in the last
        ``throttle_hours`` hours doesn't fire again, so a control that stays over its threshold doesn't send every poll.
        """
        from datetime import datetime, timedelta, timezone

        centres = self._centre_projects()
        sent = 0
        now = datetime.now(timezone.utc)
        for rule in self.store.list_notification_rules():
            if not rule["enabled"] or rule["trigger"] == "event":
                continue
            last = rule.get("last_fired_at")
            if last and datetime.fromisoformat(last.replace("Z", "+00:00")) > now - timedelta(hours=throttle_hours):
                continue
            hits = []
            for cc in cost_controls:
                project = cc.get("project") or ""
                if rule["scope_type"] not in ("all", "account", "subscription", "resource_group") and not (
                        self.rule_matches_project(rule, project, centres)):
                    continue
                if rule["trigger"] == "percent_used" and (cc.get("percent_used") or 0) >= float(rule["threshold"] or 0):
                    hits.append(f"{cc['name']} at {cc['percent_used']}%")
                if rule["trigger"] == "state" and cc.get("state") in ("Near threshold", "Limit reached"):
                    hits.append(f"{cc['name']} is {cc['state']}")
            if hits:
                sent += self.send(rule, f"Foundry cost controls: {rule['name']}", "\n".join(hits), {"hits": hits})
        return sent
