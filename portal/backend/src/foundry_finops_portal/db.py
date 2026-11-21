"""The portal's own data in SQLite: users, scope grants, cost centres, allocation rules, notification rules, alert
events and an audit log.

SQLite suits a single App Service instance. The file must be on persistent storage (App Service's ``/home``), and the
portal must not be scaled out to several instances. ``Store`` opens a short-lived connection per call, so it is safe
to use from FastAPI's worker threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

ROLES = ("admin", "owner", "viewer")
SCOPE_TYPES = ("all", "subscription", "resource_group", "account", "project", "cost_centre")
MATCH_TYPES = ("project", "agent", "deployment", "tag")
TRIGGERS = ("percent_used", "state", "event")
CHANNELS = ("webhook", "email", "log")

_SCHEMA = """
CREATE TABLE users (
  oid TEXT PRIMARY KEY, upn TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL CHECK (role IN ('admin','owner','viewer')),
  enabled INTEGER NOT NULL DEFAULT 1, added_by TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL
);
CREATE TABLE grants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_oid TEXT NOT NULL REFERENCES users(oid) ON DELETE CASCADE,
  scope_type TEXT NOT NULL CHECK (scope_type IN ('all','subscription','resource_group','account','project','cost_centre')),
  scope_value TEXT NOT NULL DEFAULT '', can_write INTEGER NOT NULL DEFAULT 0,
  UNIQUE (user_oid, scope_type, scope_value)
);
CREATE TABLE cost_centres (
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  owner_oid TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', monthly_budget REAL
);
CREATE TABLE allocation_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cost_centre_id INTEGER NOT NULL REFERENCES cost_centres(id) ON DELETE CASCADE,
  match_type TEXT NOT NULL CHECK (match_type IN ('project','agent','deployment','tag')),
  match_key TEXT NOT NULL DEFAULT '', match_value TEXT NOT NULL,
  share_percent REAL NOT NULL DEFAULT 100 CHECK (share_percent > 0 AND share_percent <= 100)
);
CREATE TABLE notification_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
  scope_type TEXT NOT NULL, scope_value TEXT NOT NULL DEFAULT '',
  trigger TEXT NOT NULL CHECK (trigger IN ('percent_used','state','event')),
  threshold REAL, channel TEXT NOT NULL CHECK (channel IN ('webhook','email','log')),
  target TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
  created_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, last_fired_at TEXT
);
CREATE TABLE alert_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, received_at TEXT NOT NULL, dedupe_key TEXT NOT NULL,
  cost_control TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT '', agent TEXT NOT NULL DEFAULT '',
  threshold TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT '', total_consumed REAL, limit_value REAL,
  payload TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX alert_events_dedupe ON alert_events(dedupe_key);
CREATE TABLE audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor_oid TEXT NOT NULL, actor_upn TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}', outcome TEXT NOT NULL
);
CREATE INDEX audit_at ON audit(at DESC);
"""
# Schema 2: which cost centre a department's usage is charged to, and a dated record of each caller's department.
_SCHEMA_V2 = """
CREATE TABLE department_map (
  department TEXT PRIMARY KEY COLLATE NOCASE,
  cost_centre_id INTEGER NOT NULL REFERENCES cost_centres(id) ON DELETE CASCADE
);
CREATE TABLE department_snapshot (
  day TEXT NOT NULL, oid TEXT NOT NULL, department TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (day, oid)
);
"""
_VERSION = 2


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    """All reads and writes of the portal's own data."""

    def __init__(self, path: str = "portal.db") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._memory: sqlite3.Connection | None = None
        if path == ":memory:":  # one shared connection, or each call would see an empty database
            self._memory = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory.row_factory = sqlite3.Row
        self._migrate()

    # ----------------------------------------------------------------- plumbing
    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self._memory is not None:
                conn = self._memory
                conn.execute("PRAGMA foreign_keys = ON")
                try:
                    yield conn
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                return
            conn = sqlite3.connect(self.path, timeout=15)
            conn.row_factory = sqlite3.Row
            # DELETE journal (not WAL): the file may sit on App Service's network-backed /home share.
            conn.execute("PRAGMA journal_mode = DELETE")
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _migrate(self) -> None:
        with self._conn() as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                c.executescript(_SCHEMA + _SCHEMA_V2)
                c.execute(f"PRAGMA user_version = {_VERSION}")
            elif version == 1:
                c.executescript(_SCHEMA_V2)
                c.execute(f"PRAGMA user_version = {_VERSION}")
            elif version > _VERSION:
                raise RuntimeError(f"{self.path} was written by a newer portal (schema {version})")

    @staticmethod
    def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
        return [dict(r) for r in cur.fetchall()]

    # -------------------------------------------------------------------- users
    def upsert_user(self, oid: str, upn: str, display_name: str, role: str, added_by: str = "",
                    enabled: bool = True) -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        with self._conn() as c:
            c.execute(
                "INSERT INTO users(oid, upn, display_name, role, enabled, added_by, added_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(oid) DO UPDATE SET upn=excluded.upn, display_name=excluded.display_name, "
                "role=excluded.role, enabled=excluded.enabled",
                (oid, upn, display_name, role, int(enabled), added_by, now_iso()),
            )
        found = self.get_user(oid)
        assert found is not None
        return found

    def get_user(self, oid: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM users WHERE oid = ?", (oid,)).fetchone()
        return dict(row) if row else None

    def find_user_by_upn(self, upn: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM users WHERE lower(upn) = lower(?)", (upn,)).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            users = self._rows(c.execute("SELECT * FROM users ORDER BY display_name, upn"))
            grants = self._rows(c.execute("SELECT * FROM grants ORDER BY scope_type, scope_value"))
        by_user: dict[str, list[dict[str, Any]]] = {}
        for g in grants:
            g["can_write"] = bool(g["can_write"])
            by_user.setdefault(g["user_oid"], []).append(g)
        for u in users:
            u["grants"] = by_user.get(u["oid"], [])
            u["enabled"] = bool(u["enabled"])
        return users

    def count_admins(self) -> int:
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0])

    def update_user(self, oid: str, *, role: str | None = None, enabled: bool | None = None) -> dict[str, Any] | None:
        if role is not None and role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        with self._conn() as c:
            if role is not None:
                c.execute("UPDATE users SET role = ? WHERE oid = ?", (role, oid))
            if enabled is not None:
                c.execute("UPDATE users SET enabled = ? WHERE oid = ?", (int(enabled), oid))
        return self.get_user(oid)

    def delete_user(self, oid: str) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM users WHERE oid = ?", (oid,)).rowcount > 0

    # ------------------------------------------------------------------- grants
    def grants_for(self, oid: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = self._rows(c.execute("SELECT * FROM grants WHERE user_oid = ? ORDER BY id", (oid,)))
        for r in rows:
            r["can_write"] = bool(r["can_write"])
        return rows

    def replace_grants(self, oid: str, grants: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for g in grants:
            if g["scope_type"] not in SCOPE_TYPES:
                raise ValueError(f"scope_type must be one of {SCOPE_TYPES}")
            if g["scope_type"] != "all" and not str(g.get("scope_value", "")).strip():
                raise ValueError("scope_value is required unless scope_type is 'all'")
        with self._conn() as c:
            c.execute("DELETE FROM grants WHERE user_oid = ?", (oid,))
            for g in grants:
                c.execute(
                    "INSERT OR REPLACE INTO grants(user_oid, scope_type, scope_value, can_write) VALUES (?,?,?,?)",
                    (oid, g["scope_type"], str(g.get("scope_value", "")).strip(), int(bool(g.get("can_write")))),
                )
        return self.grants_for(oid)

    # ------------------------------------------------------------ cost centres
    def list_cost_centres(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            centres = self._rows(c.execute("SELECT * FROM cost_centres ORDER BY code"))
            rules = self._rows(c.execute("SELECT * FROM allocation_rules ORDER BY id"))
        by_cc: dict[int, list[dict[str, Any]]] = {}
        for r in rules:
            by_cc.setdefault(r["cost_centre_id"], []).append(r)
        for cc in centres:
            cc["rules"] = by_cc.get(cc["id"], [])
        return centres

    def get_cost_centre(self, cc_id: int) -> dict[str, Any] | None:
        return next((c for c in self.list_cost_centres() if c["id"] == cc_id), None)

    def save_cost_centre(self, code: str, name: str, owner_oid: str = "", description: str = "",
                         monthly_budget: float | None = None, cc_id: int | None = None) -> dict[str, Any]:
        code = code.strip()
        if not code or not name.strip():
            raise ValueError("A cost centre needs a code and a name")
        if monthly_budget is not None and monthly_budget < 0:
            raise ValueError("monthly_budget can't be negative")
        with self._conn() as c:
            if cc_id is None:
                cur = c.execute(
                    "INSERT INTO cost_centres(code, name, owner_oid, description, monthly_budget) VALUES (?,?,?,?,?)",
                    (code, name.strip(), owner_oid, description, monthly_budget))
                cc_id = int(cur.lastrowid or 0)
            else:
                c.execute("UPDATE cost_centres SET code=?, name=?, owner_oid=?, description=?, monthly_budget=? WHERE id=?",
                          (code, name.strip(), owner_oid, description, monthly_budget, cc_id))
        found = self.get_cost_centre(cc_id)
        assert found is not None
        return found

    def delete_cost_centre(self, cc_id: int) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM cost_centres WHERE id = ?", (cc_id,)).rowcount > 0

    def add_rule(self, cc_id: int, match_type: str, match_value: str, match_key: str = "",
                 share_percent: float = 100) -> dict[str, Any]:
        if match_type not in MATCH_TYPES:
            raise ValueError(f"match_type must be one of {MATCH_TYPES}")
        if not match_value.strip():
            raise ValueError("match_value is required")
        if match_type == "tag" and not match_key.strip():
            raise ValueError("A tag rule needs match_key (the tag name)")
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO allocation_rules(cost_centre_id, match_type, match_key, match_value, share_percent) "
                "VALUES (?,?,?,?,?)", (cc_id, match_type, match_key.strip(), match_value.strip(), share_percent))
            row = c.execute("SELECT * FROM allocation_rules WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def delete_rule(self, rule_id: int) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM allocation_rules WHERE id = ?", (rule_id,)).rowcount > 0

    # ------------------------------------------------- departments and usage split
    def set_department_map(self, department: str, cc_id: int) -> dict[str, Any]:
        """Charge the usage of people in ``department`` (matched ignoring case and surrounding spaces) to a cost centre."""
        department = department.strip()
        if not department:
            raise ValueError("department is required")
        if self.get_cost_centre(cc_id) is None:
            raise ValueError("No such cost centre")
        with self._conn() as c:
            c.execute("INSERT INTO department_map(department, cost_centre_id) VALUES (?, ?) "
                      "ON CONFLICT(department) DO UPDATE SET cost_centre_id = excluded.cost_centre_id", (department, cc_id))
        return {"department": department, "cost_centre_id": cc_id}

    def delete_department_map(self, department: str) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM department_map WHERE department = ?", (department.strip(),)).rowcount > 0

    def list_department_map(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return self._rows(c.execute(
                "SELECT m.department, m.cost_centre_id, cc.code AS cost_centre_code FROM department_map m "
                "JOIN cost_centres cc ON cc.id = m.cost_centre_id ORDER BY m.department"))

    def snapshot_departments(self, day: str, departments: dict[str, str]) -> None:
        """Record each person's department as it was on ``day``. Later days never overwrite earlier ones."""
        with self._conn() as c:
            c.executemany("INSERT OR REPLACE INTO department_snapshot(day, oid, department) VALUES (?,?,?)",
                          [(day, oid, (dept or "").strip()) for oid, dept in departments.items()])

    def latest_departments(self, oids: list[str]) -> dict[str, tuple[str, str]]:
        """``{oid: (day, department)}`` from each person's most recent snapshot. People never looked up are absent."""
        if not oids:
            return {}
        out: dict[str, tuple[str, str]] = {}
        with self._conn() as c:
            for i in range(0, len(oids), 500):  # SQLite limits the number of parameters in one statement
                chunk = oids[i:i + 500]
                marks = ",".join("?" * len(chunk))
                for r in c.execute(
                        f"SELECT s.oid, s.day, s.department FROM department_snapshot s JOIN "
                        f"(SELECT oid, MAX(day) AS day FROM department_snapshot WHERE oid IN ({marks}) GROUP BY oid) m "
                        f"ON m.oid = s.oid AND m.day = s.day", chunk):
                    out[r["oid"]] = (r["day"], r["department"])
        return out

    # ------------------------------------------------------ notification rules
    def list_notification_rules(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = self._rows(c.execute("SELECT * FROM notification_rules ORDER BY id"))
        for r in rows:
            r["enabled"] = bool(r["enabled"])
        return rows

    def save_notification_rule(self, rule: dict[str, Any], created_by: str, rule_id: int | None = None) -> dict[str, Any]:
        if rule["trigger"] not in TRIGGERS:
            raise ValueError(f"trigger must be one of {TRIGGERS}")
        if rule["channel"] not in CHANNELS:
            raise ValueError(f"channel must be one of {CHANNELS}")
        if rule["trigger"] == "percent_used" and not 0 < float(rule.get("threshold") or 0) <= 1000:
            raise ValueError("A percent_used rule needs a threshold above 0")
        with self._conn() as c:
            if rule_id is None:
                cur = c.execute(
                    "INSERT INTO notification_rules(name, scope_type, scope_value, trigger, threshold, channel, target,"
                    " enabled, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rule["name"], rule["scope_type"], rule.get("scope_value", ""), rule["trigger"],
                     rule.get("threshold"), rule["channel"], rule.get("target", ""), int(rule.get("enabled", True)),
                     created_by, now_iso()))
                rule_id = int(cur.lastrowid or 0)
            else:
                c.execute(
                    "UPDATE notification_rules SET name=?, scope_type=?, scope_value=?, trigger=?, threshold=?, "
                    "channel=?, target=?, enabled=? WHERE id=?",
                    (rule["name"], rule["scope_type"], rule.get("scope_value", ""), rule["trigger"],
                     rule.get("threshold"), rule["channel"], rule.get("target", ""), int(rule.get("enabled", True)),
                     rule_id))
            row = c.execute("SELECT * FROM notification_rules WHERE id = ?", (rule_id,)).fetchone()
        out = dict(row)
        out["enabled"] = bool(out["enabled"])
        return out

    def delete_notification_rule(self, rule_id: int) -> bool:
        with self._conn() as c:
            return c.execute("DELETE FROM notification_rules WHERE id = ?", (rule_id,)).rowcount > 0

    def mark_rule_fired(self, rule_id: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE notification_rules SET last_fired_at = ? WHERE id = ?", (now_iso(), rule_id))

    # ------------------------------------------------------------- alert events
    def add_alert_event(self, dedupe_key: str, event: dict[str, Any]) -> dict[str, Any] | None:
        """Store an alert event. Returns ``None`` when the same threshold and period was already stored."""
        data = event.get("data") or {}
        try:
            with self._conn() as c:
                cur = c.execute(
                    "INSERT INTO alert_events(received_at, dedupe_key, cost_control, project, agent, threshold, unit, "
                    "total_consumed, limit_value, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (now_iso(), dedupe_key, str(data.get("costControl", "")), str(data.get("projectName", "")),
                     str(data.get("agentName", "")), str(data.get("threshold", "")), str(data.get("unit", "")),
                     _num(data.get("totalConsumed")), _num(data.get("limit")), json.dumps(event)))
                row = c.execute("SELECT * FROM alert_events WHERE id = ?", (cur.lastrowid,)).fetchone()
        except sqlite3.IntegrityError:
            return None
        return dict(row)

    def mark_notified(self, event_id: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE alert_events SET notified = 1 WHERE id = ?", (event_id,))

    def list_alert_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = self._rows(c.execute("SELECT * FROM alert_events ORDER BY id DESC LIMIT ?", (limit,)))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
            r["notified"] = bool(r["notified"])
        return rows

    # -------------------------------------------------------------------- audit
    def audit(self, actor_oid: str, actor_upn: str, action: str, target: str = "",
              detail: dict[str, Any] | None = None, outcome: str = "ok") -> None:
        with self._conn() as c:
            c.execute("INSERT INTO audit(at, actor_oid, actor_upn, action, target, detail, outcome) VALUES (?,?,?,?,?,?,?)",
                      (now_iso(), actor_oid, actor_upn, action, target, json.dumps(detail or {}, default=str), outcome))

    def list_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = self._rows(c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)))
        for r in rows:
            r["detail"] = json.loads(r["detail"])
        return rows


def _num(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
