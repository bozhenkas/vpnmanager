#!/usr/bin/env python3
"""
client-bot.py — отдельный бот и api для клиентской mini app goida.

без внешних pip-зависимостей: telegram polling, http api, static files.
"""

from __future__ import annotations

import base64
import calendar
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from remnawave_client import (
    pg_quote,
    remnawave_delete_device,
    remnawave_devices_by_username as remnawave_devices,
    remnawave_query,
    remnawave_user,
    remnawave_user_squads,
)


ROOT = Path(__file__).resolve().parents[1]
ENV_PATHS = [ROOT / ".env", Path(__file__).with_name(".env")]
WEB_ROOT = ROOT / "client-web"

DEFAULT_DEVICES = 2
BASE_PRICE_RUB = 170
EXTRA_DEVICE_PRICE_RUB = 30
MAX_DEVICES = 7
PAYMENT_DAY = 14
INIT_DATA_TTL_SECONDS = 1800
RU_MONTHS_GENITIVE = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

BASE_SERVER_CATALOG = [
    {"key": "smart", "label": "Оптимальный 🇸🇨", "description": "Авто-выбор лучшего сервера"},
    {"key": "smart-lite", "label": "Оптимальный Лайт 🇸🇨", "description": "Оптимальный сервер (другой протокол, отлично подойдёт для FaceTime)"},
    {"key": "fi", "label": "Финляндия 🇫🇮", "description": "Финляндия (10гб/сек)"},
    {"key": "se", "label": "Швеция 🇸🇪", "description": "Швеция (300мб/сек)"},
    {"key": "zapret", "label": "Русский (YouTube, Discord) 🇷🇺", "description": "Youtube, Discord"},
]
REMNAWAVE_SERVER_CATALOG = {
    "smart": {"key": "smart", "label": "Оптимальный 🇸🇨", "description": "Авто-выбор лучшего сервера"},
    "smart-lite": {"key": "smart-lite", "label": "Оптимальный Лайт 🇸🇨", "description": "Оптимальный сервер (другой протокол, отлично подойдёт для FaceTime)"},
    "smart2": {"key": "smart2", "label": "Оптимальный 2 🇸🇨", "description": "Оптимальный сервер (backup IP, xhttp+reality) — временно скрыт"},
    "reserve": {"key": "reserve", "label": "Резервный 🇰🇵 (мобильная связь)", "description": "Запасной вход для мобильной связи (если\u00a0основной заблокирован)"},
    "fi": {"key": "fi", "label": "Финляндия 🇫🇮", "description": "Финляндия"},
    "fra": {"key": "fra", "label": "Франция 🇫🇷", "description": "Франция"},
    "se": {"key": "se", "label": "Швеция 🇸🇪", "description": "Швеция"},
    "zapret": {"key": "zapret", "label": "Русский (YouTube, Discord) 🇷🇺", "description": "Youtube, Discord"},
    "hydra:usa": {"key": "hydra:usa", "label": "США 🇺🇸 (сторонний)", "description": "Дополнительный сервер"},
    "hydra:pol": {"key": "hydra:pol", "label": "Польша 🇵🇱 (сторонний)", "description": "Дополнительный сервер"},
    "hydra:tur": {"key": "hydra:tur", "label": "Турция 🇹🇷 (сторонний)", "description": "Дополнительный сервер"},
    "hydra:nl": {"key": "hydra:nl", "label": "Нидерланды 🇳🇱 (сторонний)", "description": "Дополнительный сервер"},
    "hydra:de": {"key": "hydra:de", "label": "Германия 🇩🇪 (сторонний)", "description": "Дополнительный сервер"},
}
HYDRA_PORTS = {
    10011: {"key": "hydra:usa", "label": "США 🇺🇸", "prefix": "usa-"},
    10012: {"key": "hydra:pol", "label": "Польша 🇵🇱", "prefix": "pol-"},
    10013: {"key": "hydra:tur", "label": "Турция 🇹🇷", "prefix": "tur-"},
    10014: {"key": "hydra:nl", "label": "Нидерланды 🇳🇱", "prefix": "nl-"},
    10015: {"key": "hydra:de", "label": "Германия 🇩🇪", "prefix": "de-"},
    10016: {"key": "hydra:fiws", "label": "Финляндия 🇫🇮", "prefix": "fi2-"},
}


def load_dotenv() -> None:
    for path in ENV_PATHS:
        if not path.exists():
            continue
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv()

TOKEN = os.environ.get("CLIENT_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("CLIENT_OWNER_ID", "294057781"))
BOT_DB = os.environ.get("CLIENT_BOT_DB", "/root/vpn-bot/bot.db")
XUI_DB = os.environ.get("CLIENT_XUI_DB", "/etc/x-ui/x-ui.db")
WEB_PORT = int(os.environ.get("CLIENT_WEB_PORT", "9081"))
WEBAPP_URL = os.environ.get("CLIENT_WEBAPP_URL", "https://web.goida.fun/")
SUBSCRIPTION_BASE = os.environ.get("CLIENT_SUBSCRIPTION_BASE", "https://ru.goida.fun/subscribe")
NOTIFY_TOKEN = os.environ.get("NOTIFY_TOKEN", "")
VPNBOT_INTERNAL_URL = os.environ.get("VPNBOT_INTERNAL_URL", "http://127.0.0.1:9090")

pending_input: dict[int, dict] = {}
BOT_USERNAME: str = ""

PAGE_SIZE_USERS = 8


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    Path(BOT_DB).parent.mkdir(parents=True, exist_ok=True)
    conn = db()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_profiles (
            username TEXT PRIMARY KEY,
            device_limit INTEGER NOT NULL DEFAULT 2,
            paid_until TEXT NOT NULL DEFAULT '',
            free_access INTEGER NOT NULL DEFAULT 0,
            payment_reminders_enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_tg_links (
            tg_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            tg_username TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_invite_tokens (
            username TEXT PRIMARY KEY,
            token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_plan_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            old_limit INTEGER NOT NULL,
            new_limit INTEGER NOT NULL,
            old_price INTEGER NOT NULL,
            new_price INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_reminders (
            username TEXT NOT NULL,
            remind_date TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            PRIMARY KEY (username, remind_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_server_prefs (
            username TEXT NOT NULL,
            server_key TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (username, server_key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            inviter_username TEXT NOT NULL,
            token TEXT NOT NULL,
            created_at TEXT NOT NULL,
            first_fetch_at TEXT NOT NULL DEFAULT '',
            tg_linked_at TEXT NOT NULL DEFAULT '',
            activated_username TEXT NOT NULL DEFAULT '',
            activated_tg_id INTEGER NOT NULL DEFAULT 0,
            activated_tg_username TEXT NOT NULL DEFAULT '',
            trial_ends_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'created',
            reward_days INTEGER NOT NULL DEFAULT 0,
            reward_applied_at TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_client_invites_token ON client_invites(token)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_client_invites_inviter ON client_invites(inviter_username)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_client_invites_activated_user ON client_invites(activated_username)")
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_client_invites_one_activation
            ON client_invites(activated_tg_id) WHERE activated_tg_id > 0
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_invite_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invite_id INTEGER NOT NULL,
            event TEXT NOT NULL,
            ts TEXT NOT NULL,
            meta TEXT NOT NULL DEFAULT '{}'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_client_invite_events_invite ON client_invite_events(invite_id, ts)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_invite_ref_codes (
            username TEXT PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)
    cols = table_columns(conn, "client_profiles")
    if "free_access" not in cols:
        conn.execute("ALTER TABLE client_profiles ADD COLUMN free_access INTEGER NOT NULL DEFAULT 0")
    if "payment_reminders_enabled" not in cols:
        conn.execute("ALTER TABLE client_profiles ADD COLUMN payment_reminders_enabled INTEGER NOT NULL DEFAULT 1")
    cols_links = table_columns(conn, "client_tg_links")
    if "tg_username" not in cols_links:
        conn.execute("ALTER TABLE client_tg_links ADD COLUMN tg_username TEXT NOT NULL DEFAULT ''")
    conn.commit()
    conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_msk() -> date:
    return datetime.utcnow().date()


def get_bot_username() -> str:
    global BOT_USERNAME
    if not BOT_USERNAME and TOKEN:
        try:
            data = tg_api("getMe", {})
            BOT_USERNAME = data.get("result", {}).get("username", "")
        except Exception:
            pass
    return BOT_USERNAME


def set_bot_setting(key: str, value: str) -> None:
    conn = db()
    conn.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def get_bot_setting(key: str, default: str = "") -> str:
    conn = db()
    row = conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def call_vpnbot_internal(path: str, payload: dict) -> dict | None:
    if not NOTIFY_TOKEN:
        return None
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        f"{VPNBOT_INTERNAL_URL.rstrip('/')}{path}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {NOTIFY_TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        print(f"call_vpnbot_internal({path}) error: {exc}", file=sys.stderr)
        return None


def log_invite_event(invite_id: int, event: str, meta: dict | None = None) -> None:
    conn = db()
    conn.execute(
        "INSERT INTO client_invite_events (invite_id, event, ts, meta) VALUES (?, ?, ?, ?)",
        (invite_id, event, now_iso(), json.dumps(meta or {}, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


def can_invite(username: str) -> bool:
    conn = db()
    row = conn.execute(
        "SELECT paid_until, free_access FROM client_profiles WHERE username=?", (username,)
    ).fetchone()
    conn.close()
    if not row:
        return False
    return bool(row["free_access"]) or bool(row["paid_until"])


def invite_state_for_username(username: str) -> dict:
    conn = db()
    row = conn.execute(
        "SELECT * FROM client_invites WHERE activated_username=? ORDER BY id DESC LIMIT 1",
        (username,),
    ).fetchone()
    conn.close()
    if not row:
        return {"status": None, "paymentLocked": False, "eligible": can_invite(username)}
    locked = row["status"] not in ("approved", "paid")
    return {"status": row["status"], "paymentLocked": locked, "eligible": can_invite(username)}


def generate_friend_username(conn: sqlite3.Connection, tg_id: int, tg_username: str) -> str:
    base = re.sub(r"[^a-z0-9_]", "", (tg_username or "").lower()) or f"guest{tg_id}"
    candidate = base
    suffix = 1
    while get_user_by_username(conn, candidate) or remnawave_user(candidate):
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


def first_inviter_for_tg(conn: sqlite3.Connection, tg_id: int) -> str:
    row = conn.execute(
        "SELECT inviter_username FROM client_invites WHERE activated_tg_id=? ORDER BY id ASC LIMIT 1",
        (tg_id,),
    ).fetchone()
    return row["inviter_username"] if row else ""


INVITE_STATUS_TEXT = {
    "created": "создан",
    "opened": "открыт",
    "tg_pending": "ждёт телеграм",
    "trial": "триал",
    "approved": "ждёт оплаты",
    "paid": "оплатил ✓",
    "expired": "истёк",
    "revoked": "отозван",
    "banned": "забанен",
}


def get_or_create_ref_code(username: str) -> str:
    conn = db()
    row = conn.execute("SELECT code FROM client_invite_ref_codes WHERE username=?", (username,)).fetchone()
    if row:
        conn.close()
        return row["code"]
    code = re.sub(r"[^A-Za-z0-9_-]", "", secrets_token())[:10]
    conn.execute(
        "INSERT OR IGNORE INTO client_invite_ref_codes (username, code, created_at) VALUES (?, ?, ?)",
        (username, code, now_iso()),
    )
    conn.commit()
    row = conn.execute("SELECT code FROM client_invite_ref_codes WHERE username=?", (username,)).fetchone()
    conn.close()
    return row["code"]


def secrets_token() -> str:
    return secrets.token_urlsafe(10)


def sub_url_for_activated_username(activated_username: str) -> str:
    if not activated_username:
        return ""
    conn = db()
    user = get_user_by_username(conn, activated_username)
    conn.close()
    if user:
        return f"{SUBSCRIPTION_BASE.rstrip('/')}/{user['token']}"
    remna = remnawave_user(activated_username)
    short_uuid = str(remna.get("shortUuid") or "") if remna else ""
    return f"{SUBSCRIPTION_BASE.rstrip('/')}/{short_uuid}" if short_uuid else ""


def build_invites_payload(username: str) -> dict:
    eligible = can_invite(username)
    if not eligible:
        return {
            "eligible": False,
            "refCode": None,
            "refLink": "",
            "shareText": "",
            "earnedDays": 0,
            "invited": [],
            "personalPending": [],
        }
    ref_code = get_or_create_ref_code(username)
    bot = get_bot_username()
    ref_link = f"https://t.me/{bot}?start=ref_{ref_code}"
    conn = db()
    earned_row = conn.execute(
        "SELECT COALESCE(SUM(reward_days), 0) AS s FROM client_invites WHERE inviter_username=? AND reward_applied_at != ''",
        (username,),
    ).fetchone()
    earned_days = int(earned_row["s"] or 0)
    invited_rows = conn.execute(
        "SELECT * FROM client_invites WHERE inviter_username=? ORDER BY id DESC",
        (username,),
    ).fetchall()
    invited = [
        {
            "id": int(row["id"]),
            "type": row["type"],
            "name": row["activated_username"],
            "status": row["status"],
            "statusText": INVITE_STATUS_TEXT.get(row["status"], row["status"]),
            "createdAt": row["created_at"],
        }
        for row in invited_rows
    ]
    personal_pending_rows = conn.execute(
        """
        SELECT * FROM client_invites
        WHERE inviter_username=? AND type='personal' AND status IN ('created','opened','tg_pending')
        ORDER BY id DESC
        """,
        (username,),
    ).fetchall()
    conn.close()
    personal_pending = [
        {
            "id": int(row["id"]),
            "subUrl": sub_url_for_activated_username(row["activated_username"]),
            "deepLink": f"https://t.me/{bot}?start=link_{row['token']}",
            "status": row["status"],
            "createdAt": row["created_at"],
        }
        for row in personal_pending_rows
    ]
    return {
        "eligible": True,
        "refCode": ref_code,
        "refLink": ref_link,
        "shareText": f"присоединяйся к goida vpn: {ref_link}",
        "earnedDays": earned_days,
        "invited": invited,
        "personalPending": personal_pending,
    }


def create_personal_invite(username: str) -> dict | None:
    if not can_invite(username):
        return None
    token = secrets.token_urlsafe(24)
    friend_username = f"friend-{token[:10]}"
    result = call_vpnbot_internal(
        "/internal/invites/create-user",
        {"username": friend_username, "deviceLimit": 1, "expireDays": 14},
    )
    if not result or not result.get("ok"):
        return None
    sub_url = result.get("subUrl", "")
    conn = db()
    cur = conn.execute(
        """
        INSERT INTO client_invites
            (type, inviter_username, token, created_at, activated_username, status)
        VALUES ('personal', ?, ?, ?, ?, 'created')
        """,
        (username, token, now_iso(), friend_username),
    )
    invite_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    log_invite_event(invite_id, "create", {})
    bot = get_bot_username()
    deep_link = f"https://t.me/{bot}?start=link_{token}"
    return {
        "id": invite_id,
        "subUrl": sub_url,
        "deepLink": deep_link,
        "shareText": f"вот доступ в goida vpn: {sub_url}",
    }


def revoke_personal_invite(username: str, invite_id: int) -> bool:
    conn = db()
    row = conn.execute("SELECT * FROM client_invites WHERE id=?", (invite_id,)).fetchone()
    if not row or row["inviter_username"] != username or row["status"] not in ("created", "opened", "tg_pending"):
        conn.close()
        return False
    cur = conn.execute(
        """
        UPDATE client_invites SET status='revoked' WHERE id=? AND inviter_username=?
        AND status IN ('created','opened','tg_pending')
        """,
        (invite_id, username),
    )
    if cur.rowcount == 0:
        conn.close()
        return False
    conn.commit()
    activated_username = row["activated_username"]
    conn.close()
    if activated_username:
        call_vpnbot_internal(
            "/internal/invites/set-remna",
            {"username": activated_username, "deviceLimit": None, "expireDays": None, "expireNow": True},
        )
    log_invite_event(invite_id, "revoke", {})
    return True


def any_invite_id_for_tg(conn: sqlite3.Connection, tg_id: int) -> int:
    row = conn.execute(
        "SELECT id FROM client_invites WHERE activated_tg_id=? ORDER BY id ASC LIMIT 1",
        (tg_id,),
    ).fetchone()
    return int(row["id"]) if row else 0


def get_or_create_invite_token(username: str) -> str:
    conn = db()
    row = conn.execute("SELECT token FROM client_invite_tokens WHERE username=?", (username,)).fetchone()
    if row:
        conn.close()
        return row["token"]
    token = base64.urlsafe_b64encode(os.urandom(15)).decode().rstrip("=")
    conn.execute(
        "INSERT INTO client_invite_tokens (username, token, created_at) VALUES (?, ?, ?)",
        (username, token, now_iso()),
    )
    conn.commit()
    conn.close()
    return token


def invite_link_for(username: str) -> str:
    token = get_or_create_invite_token(username)
    bot = get_bot_username()
    return f"https://t.me/{bot}?start={token}"


def resolve_invite_token(token: str) -> str:
    conn = db()
    row = conn.execute("SELECT username FROM client_invite_tokens WHERE token=?", (token,)).fetchone()
    conn.close()
    return row["username"] if row else ""


def detach_user(username: str) -> None:
    conn = db()
    conn.execute("DELETE FROM client_tg_links WHERE username=?", (username,))
    cols = table_columns(conn, "users")
    if "tg_id" in cols:
        conn.execute("UPDATE users SET tg_id=NULL WHERE name=?", (username,))
    conn.commit()
    conn.close()


def price_for(limit: int) -> int:
    limit = max(DEFAULT_DEVICES, int(limit))
    return BASE_PRICE_RUB + max(0, limit - DEFAULT_DEVICES) * EXTRA_DEVICE_PRICE_RUB


def devices_word(count: int) -> str:
    count = abs(int(count))
    if count % 10 == 1 and count % 100 != 11:
        return "устройство"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "устройства"
    return "устройств"


def format_bytes(value: int) -> str:
    value = max(0, int(value or 0))
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "Б" else f"{int(size)} {unit}"
        size /= 1024


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def get_user_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE name=?", (username,)).fetchone()


def get_username_for_tg(conn: sqlite3.Connection, tg_id: int) -> str:
    cols = table_columns(conn, "users")
    if "tg_id" in cols:
        row = conn.execute("SELECT name FROM users WHERE tg_id=?", (str(tg_id),)).fetchone()
        if row:
            return row["name"]
    row = conn.execute("SELECT username FROM client_tg_links WHERE tg_id=?", (tg_id,)).fetchone()
    return row["username"] if row else ""


def get_client_link_username(conn: sqlite3.Connection, tg_id: int) -> str:
    row = conn.execute("SELECT username FROM client_tg_links WHERE tg_id=?", (tg_id,)).fetchone()
    return row["username"] if row else ""


def ensure_profile(conn: sqlite3.Connection, username: str) -> sqlite3.Row:
    conn.execute(
        "INSERT OR IGNORE INTO client_profiles (username, device_limit, paid_until, updated_at) VALUES (?, ?, '', ?)",
        (username, DEFAULT_DEVICES, now_iso()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM client_profiles WHERE username=?", (username,)).fetchone()


def set_paid_until(username: str, paid_until: str) -> None:
    conn = db()
    ensure_profile(conn, username)
    conn.execute(
        "UPDATE client_profiles SET paid_until=?, updated_at=? WHERE username=?",
        (paid_until, now_iso(), username),
    )
    conn.commit()
    conn.close()


def set_device_limit(username: str, limit: int) -> None:
    conn = db()
    ensure_profile(conn, username)
    conn.execute(
        "UPDATE client_profiles SET device_limit=?, updated_at=? WHERE username=?",
        (max(DEFAULT_DEVICES, min(MAX_DEVICES, limit)), now_iso(), username),
    )
    conn.commit()
    conn.close()


def set_free_access(username: str, enabled: bool) -> None:
    conn = db()
    ensure_profile(conn, username)
    conn.execute(
        "UPDATE client_profiles SET free_access=?, updated_at=? WHERE username=?",
        (1 if enabled else 0, now_iso(), username),
    )
    conn.commit()
    conn.close()


def user_hydra_enabled(username: str) -> bool:
    if not username:
        return False
    conn = db()
    try:
        row = conn.execute(
            "SELECT hydra_enabled FROM users WHERE name=?",
            (username,),
        ).fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return False
    conn.close()
    return bool(row and row[0])


def hydra_client_enabled(row: sqlite3.Row, username: str) -> bool:
    if username and user_hydra_enabled(username):
        return True
    meta = HYDRA_PORTS.get(int(row["port"]))
    if not meta:
        return False
    email = f"{meta['prefix']}{username}"
    try:
        settings = json.loads(row["settings"] or "{}")
    except Exception:
        return False
    for client in settings.get("clients", []):
        if client.get("email") == email:
            return bool(client.get("enable", True))
    return False


def server_catalog(conn: sqlite3.Connection, username: str = "") -> list[dict]:
    catalog = remnawave_server_catalog(username) if username else []
    if catalog:
        return catalog
    catalog = list(BASE_SERVER_CATALOG)
    xconn = None
    try:
        xconn = sqlite3.connect(XUI_DB, timeout=30)
        xconn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in HYDRA_PORTS)
        rows = xconn.execute(
            f"SELECT port, settings FROM inbounds WHERE enable=1 AND port IN ({placeholders}) ORDER BY port",
            tuple(HYDRA_PORTS.keys()),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        if xconn:
            xconn.close()
    for row in rows:
        meta = HYDRA_PORTS.get(int(row["port"]))
        if meta and (not username or hydra_client_enabled(row, username)):
            catalog.append({
                "key": meta["key"],
                "label": meta["label"],
                "description": "Дополнительный сервер",
            })
    return catalog


def remnawave_server_catalog(username: str) -> list[dict]:
    """строит remnawave-серверы из squads панели; fallback остаётся legacy."""
    if not username:
        return []
    squads = remnawave_user_squads(username)
    if not squads:
        return []

    keys: list[str] = []
    if "SMART_RU_REMNA" in squads:
        keys.append("smart")
        keys.append("smart-lite")
        keys.append("reserve")  # запасной вход через ru-4, тот же smart-роутинг
    if "SMART_REMNA" in squads:
        keys.append("fi")
    if "FRA" in squads:
        keys.append("fra")
    if "SMART_REMNA" in squads:
        keys.append("se")
    keys.append("zapret")
    hydra_on = user_hydra_enabled(username)
    for squad, key in (
        ("HYDRA_USA_REMNA", "hydra:usa"),
        ("HYDRA_POL_REMNA", "hydra:pol"),
        ("HYDRA_TUR_REMNA", "hydra:tur"),
        ("HYDRA_NL_REMNA", "hydra:nl"),
        ("HYDRA_DE_REMNA", "hydra:de"),
    ):
        if hydra_on and squad in squads:
            keys.append(key)

    seen = set()
    catalog = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        item = REMNAWAVE_SERVER_CATALOG.get(key)
        if item:
            catalog.append(dict(item))
    return catalog


def server_preferences(conn: sqlite3.Connection, username: str) -> list[dict]:
    rows = conn.execute(
        "SELECT server_key, enabled FROM client_server_prefs WHERE username=?",
        (username,),
    ).fetchall()
    by_key = {row["server_key"]: bool(row["enabled"]) for row in rows}
    return [
        {
            "key": item["key"],
            "label": item["label"],
            "description": item["description"],
            "enabled": by_key.get(item["key"], True),
        }
        for item in server_catalog(conn, username)
    ]


def set_server_preference(username: str, server_key: str, enabled: bool) -> None:
    conn = db()
    allowed = {item["key"] for item in server_catalog(conn, username)}
    if server_key not in allowed:
        conn.close()
        raise ValueError("unknown server")
    if not get_user_by_username(conn, username) and not remnawave_user(username):
        conn.close()
        raise ValueError("user not found")
    conn.execute(
        """
        INSERT INTO client_server_prefs (username, server_key, enabled, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(username, server_key)
        DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at
        """,
        (username, server_key, 1 if enabled else 0, now_iso()),
    )
    conn.commit()
    conn.close()


def user_traffic(username: str) -> dict:
    emails = {username, f"fin-{username}", f"swe-{username}", f"zapret-{username}"}
    for meta in HYDRA_PORTS.values():
        emails.add(f"{meta['prefix']}{username}")
    placeholders = ",".join("?" for _ in emails)
    try:
        conn = sqlite3.connect(XUI_DB, timeout=30)
        rows = conn.execute(
            f"SELECT up, down FROM client_traffics WHERE email IN ({placeholders})",
            tuple(sorted(emails)),
        ).fetchall()
        conn.close()
    except Exception:
        rows = []
    up = sum(int(row[0] or 0) for row in rows)
    down = sum(int(row[1] or 0) for row in rows)
    total = up + down
    return {
        "up": up,
        "down": down,
        "total": total,
        "label": f"↑{format_bytes(up)} ↓{format_bytes(down)}",
    }


def set_payment_reminders(username: str, enabled: bool) -> None:
    conn = db()
    ensure_profile(conn, username)
    conn.execute(
        "UPDATE client_profiles SET payment_reminders_enabled=?, updated_at=? WHERE username=?",
        (1 if enabled else 0, now_iso(), username),
    )
    conn.commit()
    conn.close()


def fmt_dt(value: str) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%d.%m.%Y, %H:%M")
    except Exception:
        return value[:16].replace("T", ", ")


def fmt_ru_date(value: str) -> str:
    if not value:
        return ""
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return value
    result = f"{day.day} {RU_MONTHS_GENITIVE[day.month]}"
    if day.year != today_msk().year:
        result += f" {day.year}"
    return result


def subscription_status(paid_until: str) -> dict:
    if not paid_until:
        return {"state": "unknown", "expiresInDays": None}
    try:
        end = datetime.strptime(paid_until[:10], "%Y-%m-%d").date()
    except ValueError:
        return {"state": "unknown", "expiresInDays": None}
    days = (end - today_msk()).days
    state = "expired" if days < 0 else "ending" if days <= 3 else "active"
    return {"state": state, "expiresInDays": days}


def pending_plan_request(conn: sqlite3.Connection, username: str) -> dict | None:
    row = conn.execute(
        """
        SELECT id, old_limit, new_limit, old_price, new_price, created_at
        FROM client_plan_requests
        WHERE username=? AND status='new'
        ORDER BY id DESC
        LIMIT 1
        """,
        (username,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "oldLimit": int(row["old_limit"]),
        "newLimit": int(row["new_limit"]),
        "oldPrice": int(row["old_price"]),
        "newPrice": int(row["new_price"]),
        "createdAt": row["created_at"],
    }


def new_subscription_token() -> str:
    return base64.urlsafe_b64encode(os.urandom(33)).decode().rstrip("=")


def reset_subscription_token(username: str) -> dict:
    conn = db()
    user = get_user_by_username(conn, username)
    if not user:
        conn.close()
        raise ValueError("сброс ссылки для native-подписки пока недоступен")
    old_token = user["token"]
    new_token = new_subscription_token()
    while conn.execute("SELECT 1 FROM users WHERE token=?", (new_token,)).fetchone():
        new_token = new_subscription_token()
    conn.execute("UPDATE users SET token=? WHERE name=?", (new_token, username))
    conn.execute("UPDATE user_devices SET token=? WHERE token=?", (new_token, old_token))
    conn.execute("UPDATE user_ips SET token=? WHERE token=?", (new_token, old_token))
    if "deleted_subs" in table_names(conn):
        conn.execute(
            "INSERT OR REPLACE INTO deleted_subs (token, deleted_at) VALUES (?, ?)",
            (old_token, now_iso()),
        )
    conn.commit()
    conn.close()
    return build_profile(username)


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def key_id(token: str) -> str:
    return "da-" + hashlib.sha1(token.encode()).hexdigest()[:6]


def platform_icon(platform: str) -> str:
    p = (platform or "").lower()
    if "ios" in p or "mac" in p or "catalyst" in p:
        return "apple"
    if "android" in p:
        return "android"
    if "windows" in p:
        return "windows"
    return "device"


def device_name_for_platform(platform: str) -> str:
    p = (platform or "").lower()
    if "ios" in p:
        return "iPhone"
    if "mac" in p or "catalyst" in p:
        return "MacBook"
    if "android" in p:
        return "Android"
    if "windows" in p:
        return "Windows"
    return "устройство"


def display_platform(platform: str) -> str:
    p = (platform or "").strip()
    low = p.lower()
    if low == "ios":
        return "iOS"
    if low in ("macos", "macos catalyst") or "catalyst" in low:
        return "macOS"
    if low == "android":
        return "Android"
    if low == "windows":
        return "Windows"
    if low in {"unknown", "undefined", "null"}:
        return ""
    return p


def normalize_device_label(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    compact = value.replace(" ", "").replace("-", "").replace("_", "")
    if compact.isdigit() and len(compact) >= 8:
        return ""
    if value.lower().startswith("hwid:"):
        return ""
    if re.fullmatch(r"[0-9a-f]{8,}(?:-[0-9a-f]{4,})*", value, re.I):
        return ""
    words = [part for part in re.split(r"[-_\s]+", value) if part]
    special = {
        "iphone": "iPhone",
        "ipad": "iPad",
        "macbook": "MacBook",
        "mac": "Mac",
        "pro": "Pro",
        "max": "Max",
        "air": "Air",
        "mini": "mini",
        "m1": "M1",
        "m2": "M2",
        "m3": "M3",
        "m4": "M4",
        "m3pro": "M3 Pro",
        "m3max": "M3 Max",
        "m4pro": "M4 Pro",
        "m4max": "M4 Max",
    }
    if value.lower() in {"happ", "v2rayn", "v2rayng", "clash", "stash", "shadowrocket", "singbox", "streisand"}:
        return ""
    if len(words) == 1 and re.fullmatch(r"[a-z0-9]{12,}", words[0], re.I) and re.search(r"\d", words[0]) and re.search(r"[a-z]", words[0], re.I):
        return ""
    normalized = [special.get(part.lower(), part[:1].upper() + part[1:]) for part in words]
    return " ".join(normalized).strip()


_APP_NAMES = {"happ", "v2rayn", "v2rayng", "clash", "stash", "shadowrocket", "singbox", "streisand"}

def is_generic_device_name(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return True
    if value.lower().startswith("hwid:"):
        return True
    if value.lower() in _APP_NAMES:
        return True
    compact = value.replace(" ", "").replace("-", "").replace("_", "")
    return compact.isdigit() and len(compact) >= 8


def real_device_name(row: sqlite3.Row) -> str:
    stored = normalize_device_label(row["device_name"])
    if stored:
        return stored
    return device_name_for_platform(row["platform"])


def remnawave_device_name(item: dict) -> str:
    stored = normalize_device_label(item.get("deviceModel", ""))
    if stored:
        return stored
    return device_name_for_platform(item.get("platform", ""))


def normalize_reported_model(model: str, platform: str) -> str:
    model = normalize_device_label(model)
    platform_low = (platform or "").lower()
    if not model:
        return ""
    if "mac" in platform_low and model.lower().startswith("apple "):
        chip = model[6:].strip()
        if chip:
            return f"MacBook {chip}"
    if "mac" in platform_low and re.fullmatch(r"M[1-9](?: Pro| Max| Ultra)?", model):
        return f"MacBook {model}"
    return model[:120]


def remnawave_update_device_model(username: str, platform: str, model: str) -> bool:
    """обновляет device_model в Remnawave hwid_user_devices по платформе."""
    plat_variants = [platform.lower()]
    if platform == "macOS":
        plat_variants.append("macos catalyst")
    conditions = " OR ".join(f"lower(d.platform)={pg_quote(p)}" for p in plat_variants)
    sql = (
        f"UPDATE hwid_user_devices d SET device_model={pg_quote(model)}, updated_at=now() "
        f"FROM users u WHERE d.user_uuid=u.uuid AND u.username={pg_quote(username)} "
        f"AND ({conditions}) "
        f"AND (d.device_model IS NULL OR lower(d.device_model) IN ("
        + ", ".join(pg_quote(a) for a in _APP_NAMES)
        + "));"
    )
    result = remnawave_query(sql)
    return True


def update_current_device_model(username: str, model: str, platform: str, platform_version: str) -> bool:
    model = normalize_reported_model(model, platform)
    platform = display_platform(platform)
    platform_version = (platform_version or "").strip()[:60]
    if not model:
        return False
    changed = False
    conn = db()
    try:
        user = get_user_by_username(conn, username)
        if user:
            rows = conn.execute(
                """
                SELECT device_id, device_name, platform_version
                FROM user_devices
                WHERE token=? AND device_id LIKE 'hwid:%'
                  AND lower(platform) IN (?, ?)
                ORDER BY last_seen DESC
                """,
                (user["token"], platform.lower(), "macos catalyst" if platform == "macOS" else platform.lower()),
            ).fetchall()
            for row in rows:
                if not is_generic_device_name(row["device_name"]):
                    continue
                conn.execute(
                    """
                    UPDATE user_devices
                    SET device_name=?, platform_version=COALESCE(NULLIF(platform_version, ''), ?)
                    WHERE token=? AND device_id=?
                    """,
                    (model, platform_version, user["token"], row["device_id"]),
                )
                conn.commit()
                changed = True
                break
    finally:
        conn.close()
    # обновляем Remnawave независимо — для RW-юзеров это единственный источник
    remnawave_update_device_model(username, platform, model)
    return changed


def _build_rw_only_profile(username: str, remna_user: dict, conn: sqlite3.Connection) -> dict:
    """профиль для pure-RW пользователей без записи в users (bot.db)."""
    servers = server_preferences(conn, username)
    pending_request = pending_plan_request(conn, username)
    conn.close()
    limit = int(remna_user.get("deviceLimit") or DEFAULT_DEVICES)
    expire_raw = str(remna_user.get("expireAt") or "")
    paid_until = expire_raw[:10] if expire_raw else ""
    used = int(remna_user.get("usedTrafficBytes") or 0)
    lifetime = int(remna_user.get("lifetimeUsedTrafficBytes") or used)
    traffic = {
        "up": 0,
        "down": used,
        "total": lifetime,
        "label": format_bytes(used),
    }
    short_uuid = str(remna_user.get("shortUuid") or "")
    devices = [
        {
            "id": item.get("hwid", ""),
            "name": remnawave_device_name(item),
            "platform": display_platform(item.get("platform", "")),
            "platformVersion": item.get("osVersion") or "",
            "appName": "Happ",
            "appVersion": "",
            "firstSeen": fmt_dt(item.get("createdAt", "")),
            "lastSeen": fmt_dt(item.get("updatedAt", "")),
            "clientIp": "",
            "icon": platform_icon(item.get("platform", "")),
        }
        for item in remnawave_devices(username)
    ]
    return {
        "username": username,
        "keyId": key_id(short_uuid) if short_uuid else "",
        "status": "active",
        # shortUuid используется как токен — vpn-bot обработает через remnawave_user_by_short_uuid
        "subscriptionUrl": f"{SUBSCRIPTION_BASE.rstrip('/')}/{short_uuid}" if short_uuid else "",
        "deviceLimit": limit,
        "baseDevices": DEFAULT_DEVICES,
        "basePrice": BASE_PRICE_RUB,
        "extraDevicePrice": EXTRA_DEVICE_PRICE_RUB,
        "monthlyPrice": price_for(limit),
        "freeAccess": False,
        "paidUntil": paid_until,
        "paidUntilText": fmt_ru_date(paid_until),
        "subscriptionStatus": subscription_status(paid_until),
        "paymentRemindersEnabled": False,
        "pendingPlanRequest": pending_request,
        "traffic": traffic,
        "devices": devices,
        "servers": servers,
        "invite": invite_state_for_username(username),
    }


def build_profile(username: str) -> dict:
    conn = db()
    user = get_user_by_username(conn, username)
    if not user:
        # pure RW user — записи в users нет, но есть в Remnawave
        remna_user = remnawave_user(username)
        if not remna_user:
            conn.close()
            raise KeyError("user not found")
        return _build_rw_only_profile(username, remna_user, conn)
    profile = ensure_profile(conn, username)
    remna_user = remnawave_user(username)
    devices_raw = []
    if not remna_user:
        devices_raw = conn.execute(
            """
            SELECT device_id, first_seen, last_seen, client_ip, app_name, app_version,
                   platform, platform_version, device_name
            FROM user_devices
            WHERE token=? AND device_id LIKE 'hwid:%'
            ORDER BY last_seen DESC
            """,
            (user["token"],),
        ).fetchall()
    servers = server_preferences(conn, username)
    pending_request = pending_plan_request(conn, username)
    conn.close()

    # deduplicate by device_id: первое вхождение = последний last_seen (ORDER BY last_seen DESC)
    _seen: dict[str, dict] = {}
    for row in devices_raw:
        did = row["device_id"]
        if did not in _seen:
            _seen[did] = {"row": row, "first_seen": row["first_seen"]}
        elif row["first_seen"] and (
            not _seen[did]["first_seen"] or row["first_seen"] < _seen[did]["first_seen"]
        ):
            _seen[did]["first_seen"] = row["first_seen"]
    devices_deduped = list(_seen.values())

    limit = int(profile["device_limit"])
    paid_until = profile["paid_until"] or ""
    traffic = user_traffic(username)
    devices = [
        {
            "id": item["row"]["device_id"],
            "name": real_device_name(item["row"]),
            "platform": display_platform(item["row"]["platform"]),
            "platformVersion": item["row"]["platform_version"] or "",
            "appName": item["row"]["app_name"] or "",
            "appVersion": item["row"]["app_version"] or "",
            "firstSeen": fmt_dt(item["first_seen"]),
            "lastSeen": fmt_dt(item["row"]["last_seen"]),
            "clientIp": item["row"]["client_ip"],
            "icon": platform_icon(item["row"]["platform"]),
        }
        for item in devices_deduped
    ]
    if remna_user:
        limit = int(remna_user.get("deviceLimit") or DEFAULT_DEVICES)
        expire_raw = str(remna_user.get("expireAt") or "")
        paid_until = expire_raw[:10] if expire_raw else paid_until
        used = int(remna_user.get("usedTrafficBytes") or 0)
        lifetime = int(remna_user.get("lifetimeUsedTrafficBytes") or used)
        traffic = {
            "up": 0,
            "down": used,
            "total": lifetime,
            "label": format_bytes(used),
        }
        devices = [
            {
                "id": item.get("hwid", ""),
                "name": remnawave_device_name(item),
                "platform": display_platform(item.get("platform", "")),
                "platformVersion": item.get("osVersion") or "",
                "appName": "Happ",
                "appVersion": "",
                "firstSeen": fmt_dt(item.get("createdAt", "")),
                "lastSeen": fmt_dt(item.get("updatedAt", "")),
                "clientIp": "",
                "icon": platform_icon(item.get("platform", "")),
            }
            for item in remnawave_devices(username)
        ]
    return {
        "username": username,
        "keyId": key_id(user["token"]),
        "status": "active",
        "subscriptionUrl": f"{SUBSCRIPTION_BASE.rstrip('/')}/{user['token']}",
        "deviceLimit": limit,
        "baseDevices": DEFAULT_DEVICES,
        "basePrice": BASE_PRICE_RUB,
        "extraDevicePrice": EXTRA_DEVICE_PRICE_RUB,
        "monthlyPrice": price_for(limit),
        "freeAccess": bool(profile["free_access"]),
        "paidUntil": paid_until,
        "paidUntilText": fmt_ru_date(paid_until),
        "subscriptionStatus": subscription_status(paid_until),
        "paymentRemindersEnabled": bool(profile["payment_reminders_enabled"]),
        "pendingPlanRequest": pending_request,
        "traffic": traffic,
        "devices": devices,
        "servers": servers,
        "invite": invite_state_for_username(username),
    }


def validate_init_data(init_data: str) -> dict:
    if not TOKEN:
        raise ValueError("bot token is not configured")
    parsed = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    data = dict(parsed)
    received = data.pop("hash", "")
    if not received:
        raise ValueError("hash missing")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    actual = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(actual, received):
        raise ValueError("bad initData hash")
    auth_date_raw = data.get("auth_date", "")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise ValueError("bad auth_date") from exc
    age = int(time.time()) - auth_date
    if age < 0 or age > INIT_DATA_TTL_SECONDS:
        raise ValueError("initData expired")
    user = json.loads(data.get("user", "{}"))
    if not user.get("id"):
        raise ValueError("user missing")
    return user


def api_user(headers: dict[str, str]) -> tuple[int, str]:
    init_data = headers.get("X-Telegram-Init-Data", "")
    tg_user = validate_init_data(init_data)
    tg_id = int(tg_user["id"])
    conn = db()
    username = get_client_link_username(conn, tg_id)
    conn.close()
    if not username:
        raise PermissionError("telegram account is not linked")
    return tg_id, username


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "goida-client/0.1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/me":
            return self.handle_me()
        if path == "/api/invites":
            return self.handle_invites_get()
        if path == "/" or not path.startswith("/api/"):
            return self.serve_static()
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/api/devices/unbind":
            return self.handle_unbind()
        if self.path == "/api/plan/request":
            return self.handle_plan_request()
        if self.path == "/api/servers/toggle":
            return self.handle_server_toggle()
        if self.path == "/api/payment-reminders":
            return self.handle_payment_reminders()
        if self.path == "/api/subscription/reset":
            return self.handle_subscription_reset()
        if self.path == "/api/device-model":
            return self.handle_device_model()
        if self.path == "/api/invites/personal":
            return self.handle_invites_personal()
        if self.path == "/api/invites/revoke":
            return self.handle_invites_revoke()
        if self.path == "/internal/notify":
            return self.handle_internal_notify()
        self.send_json(404, {"error": "not found"})

    def handle_me(self) -> None:
        try:
            _, username = api_user(self.headers)
            self.send_json(200, build_profile(username))
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except Exception as exc:
            self.send_json(401, {"error": str(exc)})

    def handle_unbind(self) -> None:
        try:
            _, username = api_user(self.headers)
            data = self.read_json()
            device_id = str(data.get("deviceId", ""))
            profile = build_profile(username)
            if remnawave_user(username):
                remnawave_delete_device(username, device_id)
            else:
                conn = db()
                user = get_user_by_username(conn, username)
                conn.execute("DELETE FROM user_devices WHERE token=? AND device_id=?", (user["token"], device_id))
                conn.commit()
                conn.close()
            self.send_json(200, {"ok": True, "previousDeviceCount": len(profile["devices"])})
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(401, {"error": str(exc)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def handle_server_toggle(self) -> None:
        try:
            _, username = api_user(self.headers)
            data = self.read_json()
            server_key = str(data.get("serverKey", ""))
            enabled = bool(data.get("enabled"))
            set_server_preference(username, server_key, enabled)
            self.send_json(200, {"ok": True, "servers": build_profile(username)["servers"]})
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(401, {"error": str(exc)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def handle_payment_reminders(self) -> None:
        try:
            _, username = api_user(self.headers)
            data = self.read_json()
            enabled = bool(data.get("enabled"))
            set_payment_reminders(username, enabled)
            self.send_json(200, {"ok": True, "paymentRemindersEnabled": enabled})
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(401, {"error": str(exc)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def handle_subscription_reset(self) -> None:
        try:
            _, username = api_user(self.headers)
            self.send_json(200, {"ok": True, "profile": reset_subscription_token(username)})
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def handle_device_model(self) -> None:
        try:
            _, username = api_user(self.headers)
            data = self.read_json()
            changed = update_current_device_model(
                username,
                str(data.get("model", "")),
                str(data.get("platform", "")),
                str(data.get("platformVersion", "")),
            )
            self.send_json(200, {"ok": True, "changed": changed})
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(401, {"error": str(exc)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def handle_plan_request(self) -> None:
        try:
            tg_id, username = api_user(self.headers)
            if invite_state_for_username(username)["paymentLocked"]:
                return self.send_json(403, {"error": "payment_locked"})
            data = self.read_json()
            new_limit = max(DEFAULT_DEVICES, min(MAX_DEVICES, int(data.get("deviceLimit", DEFAULT_DEVICES))))
            profile = build_profile(username)
            old_limit = int(profile["deviceLimit"])
            conn = db()
            cur = conn.execute(
                """
                INSERT INTO client_plan_requests
                    (username, old_limit, new_limit, old_price, new_price, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'new', ?)
                """,
                (username, old_limit, new_limit, price_for(old_limit), price_for(new_limit), now_iso()),
            )
            request_id = int(cur.lastrowid)
            conn.commit()
            conn.close()
            if TOKEN:
                send_message(
                    OWNER_ID,
                    "заявка на смену тарифа\n"
                    f"user: {html.escape(username)}\n"
                    f"tg: <code>{tg_id}</code>\n"
                    f"{old_limit} {devices_word(old_limit)} / {price_for(old_limit)}₽ → "
                    f"{new_limit} {devices_word(new_limit)} / {price_for(new_limit)}₽",
                    plan_request_markup(request_id),
                )
            self.send_json(200, {"ok": True, "pendingPlanRequest": build_profile(username)["pendingPlanRequest"]})
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(401, {"error": str(exc)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def handle_invites_get(self) -> None:
        try:
            _, username = api_user(self.headers)
            self.send_json(200, build_invites_payload(username))
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except Exception as exc:
            self.send_json(401, {"error": str(exc)})

    def handle_invites_personal(self) -> None:
        try:
            _, username = api_user(self.headers)
            result = create_personal_invite(username)
            if result is None:
                return self.send_json(403, {"error": "not_eligible"})
            self.send_json(200, result)
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(401, {"error": str(exc)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def handle_invites_revoke(self) -> None:
        try:
            _, username = api_user(self.headers)
            data = self.read_json()
            invite_id = int(data.get("id", 0))
            ok = revoke_personal_invite(username, invite_id)
            if not ok:
                return self.send_json(400, {"error": "cannot_revoke"})
            self.send_json(200, {"ok": True})
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(401, {"error": str(exc)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def handle_internal_notify(self) -> None:
        client_ip = self.headers.get("X-Real-IP", self.client_address[0])
        if not NOTIFY_TOKEN or self.headers.get("Authorization") != f"Bearer {NOTIFY_TOKEN}":
            return self.send_json(401, {"error": "unauthorized"})
        if client_ip not in ("127.0.0.1", "::1"):
            return self.send_json(403, {"error": "forbidden"})
        try:
            data = self.read_json()
            tg_id = int(data.get("tgId", 0))
            text = str(data.get("text", ""))[:4000]
            if tg_id and text:
                send_message(tg_id, text)
            self.send_json(200, {"ok": True})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode())

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        file_path = (WEB_ROOT / rel).resolve()
        if WEB_ROOT.resolve() not in file_path.parents and file_path != WEB_ROOT.resolve():
            self.send_error(403)
            return
        if not file_path.exists() or file_path.is_dir():
            file_path = WEB_ROOT / "index.html"
        body = file_path.read_bytes()
        ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store" if file_path.name == "index.html" else "public, max-age=300")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def tg_api(method: str, payload: dict) -> dict:
    if not TOKEN:
        raise RuntimeError("CLIENT_BOT_TOKEN is not configured")
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=35) as resp:
        return json.loads(resp.read())


def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        tg_api("sendMessage", payload)
    except Exception as exc:
        print(f"send_message error: {exc}", file=sys.stderr)


def edit_message(chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> None:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        tg_api("editMessageText", payload)
    except Exception as exc:
        print(f"edit_message error: {exc}", file=sys.stderr)


def answer_callback(callback_id: str, text: str = "") -> None:
    try:
        tg_api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
    except Exception as exc:
        print(f"answer_callback error: {exc}", file=sys.stderr)


def plan_request_markup(request_id: int) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "принять", "callback_data": f"plan:approve:{request_id}", "style": "success"},
            {"text": "отклонить", "callback_data": f"plan:reject:{request_id}", "style": "danger"},
        ]]
    }


def webapp_markup() -> dict:
    return {"inline_keyboard": [[{"text": "открыть goida vpn", "web_app": {"url": WEBAPP_URL}}]]}


def admin_main_markup() -> dict:
    return {"inline_keyboard": [
        [
            {"text": "👥 пользователи", "callback_data": "adm:users:0"},
            {"text": "📢 рассылка", "callback_data": "adm:broadcast_ask"},
        ],
        [
            {"text": "🔔 напоминания", "callback_data": "adm:remind"},
        ],
    ]}


def admin_users_markup(page: int = 0) -> tuple[str, dict]:
    conn = db()
    rows = conn.execute("SELECT name FROM users ORDER BY name").fetchall()
    usernames = [row["name"] for row in rows]
    linked = {
        row["username"]
        for row in conn.execute("SELECT username FROM client_tg_links")
    }
    conn.close()

    total = len(usernames)
    pages = max(1, (total + PAGE_SIZE_USERS - 1) // PAGE_SIZE_USERS)
    page = max(0, min(page, pages - 1))
    chunk = usernames[page * PAGE_SIZE_USERS:(page + 1) * PAGE_SIZE_USERS]

    buttons = [
        [{"text": f"{'🟢' if u in linked else '⚪'} {u}", "callback_data": f"adm:ucard:{u}:{page}"}]
        for u in chunk
    ]

    nav = []
    if page > 0:
        nav.append({"text": "←", "callback_data": f"adm:users:{page - 1}"})
    nav.append({"text": f"{page + 1}/{pages}", "callback_data": "adm:noop"})
    if page < pages - 1:
        nav.append({"text": "→", "callback_data": f"adm:users:{page + 1}"})
    if len(nav) > 1:
        buttons.append(nav)

    buttons.append([{"text": "↩ меню", "callback_data": "adm:main"}])
    text = f"👥 пользователи — {page + 1}/{pages} ({total} всего)"
    return text, {"inline_keyboard": buttons}


def _parse_ucb(data: str, prefix: str) -> tuple[str, int]:
    rest = data[len(prefix):]
    username, _, page_str = rest.rpartition(":")
    return username, int(page_str) if page_str.isdigit() else 0


def _user_card(username: str, page: int = 0) -> tuple[str, dict]:
    conn = db()
    tg_row = conn.execute(
        "SELECT tg_id, tg_username FROM client_tg_links WHERE username=?", (username,)
    ).fetchone()
    conn.close()

    tg_id_val = int(tg_row["tg_id"]) if tg_row else None
    tg_uname = (tg_row["tg_username"] if tg_row else "") or ""

    try:
        profile = build_profile(username)
    except KeyError:
        text = f"пользователь <b>{html.escape(username)}</b> не найден"
        markup = {"inline_keyboard": [[{"text": "← список", "callback_data": f"adm:users:{page}"}]]}
        return text, markup

    paid = profile["paidUntil"] or "не указано"
    free = bool(profile["freeAccess"])
    limit = int(profile["deviceLimit"])
    status = "🟢" if tg_id_val else "⚪"

    if tg_id_val:
        tg_line = (f"@{tg_uname} · " if tg_uname else "") + f"<code>{tg_id_val}</code>"
    else:
        tg_line = "не привязан"

    text = (
        f"{status} <b>{html.escape(username)}</b>{' · бесплатно' if free else ''}\n"
        f"tg: {tg_line}\n"
        f"оплачено до: {paid}\n"
        f"устройства: {len(profile['devices'])}/{limit} {devices_word(limit)}\n"
        f"тариф: {profile['monthlyPrice']}₽/мес"
    )
    if not tg_id_val:
        text += f"\n\n🔗 ссылка приглашения:\n<code>{invite_link_for(username)}</code>"

    can_dec = limit > DEFAULT_DEVICES
    can_inc = limit < MAX_DEVICES
    rows: list[list[dict]] = []
    if tg_id_val:
        rows.append([{"text": "🔴 отвязать", "callback_data": f"adm:detach:{username}:{page}", "style": "danger"}])
    rows.append([
        {"text": "+1 мес", "callback_data": f"adm:m1:{username}:{page}"},
        {"text": "+3 мес", "callback_data": f"adm:m3:{username}:{page}"},
        {"text": "📅 дата", "callback_data": f"adm:date:{username}:{page}"},
    ])
    rows.append([
        {"text": "−" if can_dec else "·", "callback_data": f"adm:dd:{username}:{page}" if can_dec else "adm:noop"},
        {"text": f"{limit} устр.", "callback_data": "adm:noop"},
        {"text": "+" if can_inc else "·", "callback_data": f"adm:di:{username}:{page}" if can_inc else "adm:noop"},
    ])
    rows.append([{"text": f"{'✅' if free else '☐'} бесплатно", "callback_data": f"adm:free:{username}:{page}"}])
    rows.append([
        {"text": "← список", "callback_data": f"adm:users:{page}"},
        {"text": "🔄", "callback_data": f"adm:ucard:{username}:{page}"},
    ])
    return text, {"inline_keyboard": rows}


def handle_admin_callback(callback_id: str, chat_id: int, message_id: int, tg_id: int, data: str) -> None:
    answer_callback(callback_id)

    if data == "main":
        pending_input.pop(tg_id, None)
        edit_message(chat_id, message_id, "панель администратора", admin_main_markup())

    elif data == "noop":
        pass

    elif data.startswith("users:"):
        page = int(data[6:]) if data[6:].isdigit() else 0
        text, markup = admin_users_markup(page)
        edit_message(chat_id, message_id, text, markup)

    elif data.startswith("ucard:"):
        username, page = _parse_ucb(data, "ucard:")
        text, markup = _user_card(username, page)
        edit_message(chat_id, message_id, text, markup)

    elif data.startswith("detach:"):
        username, page = _parse_ucb(data, "detach:")
        detach_user(username)
        text, markup = _user_card(username, page)
        edit_message(chat_id, message_id, text, markup)

    elif data == "broadcast_ask":
        pending_input[tg_id] = {"action": "broadcast", "msg_id": message_id}
        edit_message(chat_id, message_id, "введи текст рассылки:")

    elif data == "bc_send":
        state = pending_input.pop(tg_id, {})
        bc_text = state.get("text", "")
        if not bc_text:
            edit_message(chat_id, message_id, "текст не найден", admin_main_markup())
            return
        sent = broadcast(bc_text)
        edit_message(chat_id, message_id, f"рассылка отправлена: {sent}", admin_main_markup())

    elif data == "remind":
        count = send_due_reminders(force=True)
        edit_message(chat_id, message_id, f"напоминания отправлены: {count}", admin_main_markup())

    elif data.startswith(("m1:", "m3:")):
        months = 1 if data.startswith("m1:") else 3
        username, page = _parse_ucb(data, "m1:" if months == 1 else "m3:")
        conn = db()
        profile = ensure_profile(conn, username)
        base = today_msk()
        if profile["paid_until"]:
            try:
                base = max(base, datetime.strptime(profile["paid_until"], "%Y-%m-%d").date())
            except Exception:
                pass
        conn.close()
        set_paid_until(username, add_months(base, months).isoformat())
        text, markup = _user_card(username, page)
        edit_message(chat_id, message_id, text, markup)

    elif data.startswith("date:"):
        username, page = _parse_ucb(data, "date:")
        pending_input[tg_id] = {"action": "paid_date", "username": username, "page": page, "msg_id": message_id}
        edit_message(chat_id, message_id, f"введи дату для <b>{html.escape(username)}</b> (YYYY-MM-DD):")

    elif data.startswith(("di:", "dd:")):
        inc = data.startswith("di:")
        username, page = _parse_ucb(data, "di:" if inc else "dd:")
        conn = db()
        profile = ensure_profile(conn, username)
        current = int(profile["device_limit"])
        conn.close()
        new_limit = min(MAX_DEVICES, current + 1) if inc else max(DEFAULT_DEVICES, current - 1)
        set_device_limit(username, new_limit)
        text, markup = _user_card(username, page)
        edit_message(chat_id, message_id, text, markup)

    elif data.startswith("free:"):
        username, page = _parse_ucb(data, "free:")
        conn = db()
        profile = ensure_profile(conn, username)
        current = bool(profile["free_access"])
        conn.close()
        set_free_access(username, not current)
        text, markup = _user_card(username, page)
        edit_message(chat_id, message_id, text, markup)


def handle_admin_input(chat_id: int, tg_id: int, text: str) -> None:
    state = pending_input.pop(tg_id, {})
    action = state.get("action", "")
    msg_id = state.get("msg_id", 0)

    if action == "broadcast":
        pending_input[tg_id] = {"action": "bc_confirm", "text": text, "msg_id": msg_id}
        preview = f"<b>предпросмотр рассылки:</b>\n\n{html.escape(text)}"
        markup = {"inline_keyboard": [[
            {"text": "✅ отправить", "callback_data": "adm:bc_send"},
            {"text": "❌ отмена", "callback_data": "adm:main"},
        ]]}
        edit_message(chat_id, msg_id, preview, markup)

    elif action == "paid_date":
        username = state.get("username", "")
        page = state.get("page", 0)
        try:
            datetime.strptime(text.strip(), "%Y-%m-%d")
            set_paid_until(username, text.strip())
            card_text, markup = _user_card(username, page)
            edit_message(chat_id, msg_id, card_text, markup)
        except ValueError:
            pending_input[tg_id] = state
            send_message(chat_id, "неверный формат — нужно YYYY-MM-DD:")


def _dedup_check(conn: sqlite3.Connection, from_id: int, inviter_username: str) -> str | None:
    """проверяет, можно ли активировать инвайт для этого tg_id. возвращает текст отказа или None."""
    existing = get_username_for_tg(conn, from_id)
    if not existing:
        return None
    if existing == inviter_username:
        invite_id = any_invite_id_for_tg(conn, from_id)
        if invite_id:
            log_invite_event(invite_id, "dedup_hit", {"reason": "self_link"})
        return "это твоя ссылка."
    first_inviter = first_inviter_for_tg(conn, from_id)
    invite_id = any_invite_id_for_tg(conn, from_id)
    if invite_id:
        log_invite_event(invite_id, "dedup_hit", {"reason": "already_invited"})
    if first_inviter:
        return f"ты уже был приглашён {html.escape(first_inviter)}."
    return "ты уже привязан."


def handle_ref_deeplink(chat_id: int, from_id: int, tg_uname: str, code: str) -> None:
    conn = db()
    row = conn.execute("SELECT username FROM client_invite_ref_codes WHERE code=?", (code,)).fetchone()
    if not row:
        conn.close()
        send_message(chat_id, "ссылка недействительна.")
        return
    inviter_username = row["username"]

    dedup_msg = _dedup_check(conn, from_id, inviter_username)
    if dedup_msg is not None:
        conn.close()
        send_message(chat_id, dedup_msg, webapp_markup())
        return

    friend_username = generate_friend_username(conn, from_id, tg_uname)
    conn.close()

    result = call_vpnbot_internal(
        "/internal/invites/create-user",
        {"username": friend_username, "deviceLimit": DEFAULT_DEVICES, "expireDays": 7},
    )
    if not result or not result.get("ok"):
        send_message(chat_id, "не получилось создать аккаунт, попробуй ещё раз позже.")
        return
    sub_url = result.get("subUrl", "")

    now = now_iso()
    trial_ends = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    conn = db()
    cur = conn.execute(
        """
        INSERT INTO client_invites
            (type, inviter_username, token, created_at, tg_linked_at,
             activated_username, activated_tg_id, activated_tg_username, trial_ends_at, status)
        VALUES ('ref', ?, ?, ?, ?, ?, ?, ?, ?, 'trial')
        """,
        (inviter_username, code, now, now, friend_username, from_id, tg_uname, trial_ends),
    )
    invite_id = int(cur.lastrowid)
    conn.execute(
        "INSERT OR REPLACE INTO client_tg_links (tg_id, username, tg_username, created_at) VALUES (?, ?, ?, ?)",
        (from_id, friend_username, tg_uname, now),
    )
    conn.commit()
    conn.close()

    log_invite_event(invite_id, "activate", {})
    log_invite_event(invite_id, "tg_link", {})

    call_vpnbot_internal("/internal/invites/notify-owner", {"inviteId": invite_id})

    conn = db()
    inviter_tg_id = tg_id_for_username(conn, inviter_username)
    conn.close()
    if inviter_tg_id:
        send_message(inviter_tg_id, "у тебя новый друг по приглашению! заявка на рассмотрении у владельца.")

    send_message(
        chat_id,
        f"добро пожаловать! ссылка подписки:\n<code>{html.escape(sub_url)}</code>",
        webapp_markup(),
    )


def handle_link_deeplink(chat_id: int, from_id: int, tg_uname: str, token: str) -> None:
    conn = db()
    row = conn.execute("SELECT * FROM client_invites WHERE token=? AND type='personal'", (token,)).fetchone()
    if not row:
        conn.close()
        send_message(chat_id, "ссылка недействительна.")
        return

    if row["status"] in ("expired", "revoked", "banned"):
        conn.close()
        send_message(chat_id, "это приглашение больше не активно.")
        return

    if row["first_fetch_at"]:
        try:
            first_fetch = datetime.fromisoformat(row["first_fetch_at"])
            if datetime.now(timezone.utc) > first_fetch + timedelta(days=7):
                conn.close()
                send_message(chat_id, "это приглашение больше не активно.")
                return
        except Exception:
            pass

    dedup_msg = _dedup_check(conn, from_id, row["inviter_username"])
    if dedup_msg is not None:
        conn.close()
        send_message(chat_id, dedup_msg, webapp_markup())
        return

    invite_id = int(row["id"])
    now = now_iso()
    trial_ends = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    cur = conn.execute(
        """
        UPDATE client_invites
        SET status='trial', tg_linked_at=?, activated_tg_id=?, activated_tg_username=?, trial_ends_at=?
        WHERE id=? AND status IN ('created','opened','tg_pending')
        """,
        (now, from_id, tg_uname, trial_ends, invite_id),
    )
    if cur.rowcount == 0:
        fresh = conn.execute("SELECT status FROM client_invites WHERE id=?", (invite_id,)).fetchone()
        conn.commit()
        conn.close()
        status = fresh["status"] if fresh else ""
        if status in ("trial", "approved", "paid"):
            send_message(chat_id, "это приглашение уже активировано кем-то другим.")
        else:
            send_message(chat_id, "это приглашение больше не активно.")
        return
    conn.commit()
    activated_username = row["activated_username"]
    conn.close()

    call_vpnbot_internal(
        "/internal/invites/set-remna",
        {"username": activated_username, "deviceLimit": DEFAULT_DEVICES, "expireDays": 7, "expireNow": False},
    )

    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO client_tg_links (tg_id, username, tg_username, created_at) VALUES (?, ?, ?, ?)",
        (from_id, activated_username, tg_uname, now),
    )
    conn.commit()
    conn.close()

    log_invite_event(invite_id, "tg_link", {})

    call_vpnbot_internal("/internal/invites/notify-owner", {"inviteId": invite_id})

    conn = db()
    inviter_tg_id = tg_id_for_username(conn, row["inviter_username"])
    user = get_user_by_username(conn, activated_username)
    conn.close()
    if inviter_tg_id:
        send_message(inviter_tg_id, "у тебя новый друг по приглашению! заявка на рассмотрении у владельца.")

    sub_url = f"{SUBSCRIPTION_BASE.rstrip('/')}/{user['token']}" if user else ""
    send_message(
        chat_id,
        f"добро пожаловать! полный триал открыт. ссылка подписки:\n<code>{html.escape(sub_url)}</code>",
        webapp_markup(),
    )


def handle_update(update: dict) -> None:
    if update.get("callback_query"):
        handle_callback(update["callback_query"])
        return
    message = update.get("message") or {}
    if not message:
        return
    chat_id = int((message.get("chat") or {}).get("id", 0))
    from_id = int((message.get("from") or {}).get("id", 0))
    text = (message.get("text") or "").strip()
    if not text:
        return
    parts = text.split()
    cmd = parts[0].split("@")[0] if text.startswith("/") else ""

    if cmd == "/start":
        payload = parts[1] if len(parts) > 1 else ""
        if from_id == OWNER_ID and not payload:
            send_message(chat_id, "goida vpn", webapp_markup())
            return
        if not payload:
            send_message(chat_id, "нет доступа. если ты клиент goida vpn — попроси ссылку у @bozhenkas.")
            return
        tg_uname_early = (message.get("from") or {}).get("username", "")
        if payload.startswith("ref_"):
            handle_ref_deeplink(chat_id, from_id, tg_uname_early, payload[len("ref_"):])
            return
        if payload.startswith("link_"):
            handle_link_deeplink(chat_id, from_id, tg_uname_early, payload[len("link_"):])
            return
        username = resolve_invite_token(payload)
        if not username:
            send_message(chat_id, "ссылка недействительна.")
            return
        conn_check = db()
        existing = get_username_for_tg(conn_check, from_id)
        conn_check.close()
        if existing == username:
            send_message(chat_id, f"ты уже привязан как <b>{html.escape(username)}</b>.", webapp_markup())
            return
        if existing:
            send_message(chat_id, "твой аккаунт уже привязан к другому пользователю.")
            return
        tg_uname = (message.get("from") or {}).get("username", "")
        link_user(from_id, username, tg_uname)
        return

    if from_id != OWNER_ID:
        return  # молчим — только инвайт-ссылка даёт доступ

    if cmd == "/admin":
        pending_input.pop(from_id, None)
        send_message(chat_id, "панель администратора", admin_main_markup())
        return

    if from_id in pending_input:
        handle_admin_input(chat_id, from_id, text)
        return

    send_message(chat_id, "используй /admin")


def handle_callback(query: dict) -> None:
    user = query.get("from") or {}
    user_id = int(user.get("id", 0))
    callback_id = query.get("id", "")
    data = query.get("data", "")
    message = query.get("message") or {}
    chat_id = int((message.get("chat") or {}).get("id", 0))
    message_id = int(message.get("message_id", 0))

    if data.startswith("plan:"):
        if user_id != OWNER_ID:
            answer_callback(callback_id, "только админ")
            return
        if data.startswith("plan:approve:"):
            handle_plan_decision(callback_id, chat_id, message_id, int(data.rsplit(":", 1)[1]), "approved")
        elif data.startswith("plan:reject:"):
            handle_plan_decision(callback_id, chat_id, message_id, int(data.rsplit(":", 1)[1]), "rejected")
        return

    if data.startswith("adm:"):
        if user_id != OWNER_ID:
            answer_callback(callback_id, "только админ")
            return
        # bc_send needs pending_input written by handle_admin_input, pass text through pending_input
        adm_data = data[4:]
        if adm_data == "bc_send":
            state = pending_input.get(user_id, {})
            if state.get("action") == "bc_confirm":
                pending_input.pop(user_id, None)
                bc_text = state.get("text", "")
                answer_callback(callback_id)
                sent = broadcast(bc_text) if bc_text else 0
                edit_message(chat_id, message_id, f"рассылка отправлена: {sent}", admin_main_markup())
            else:
                answer_callback(callback_id, "текст не найден")
            return
        handle_admin_callback(callback_id, chat_id, message_id, user_id, adm_data)
        return

    answer_callback(callback_id)


def handle_plan_decision(callback_id: str, chat_id: int, message_id: int, request_id: int, status: str) -> None:
    conn = db()
    row = conn.execute("SELECT * FROM client_plan_requests WHERE id=?", (request_id,)).fetchone()
    if not row:
        conn.close()
        answer_callback(callback_id, "заявка не найдена")
        return
    if row["status"] != "new":
        conn.close()
        answer_callback(callback_id, "уже обработано")
        return
    username = row["username"]
    if status == "approved":
        conn.execute(
            "UPDATE client_profiles SET device_limit=?, updated_at=? WHERE username=?",
            (row["new_limit"], now_iso(), username),
        )
    conn.execute("UPDATE client_plan_requests SET status=? WHERE id=?", (status, request_id))
    conn.commit()
    tg_id = tg_id_for_username(conn, username)
    conn.close()

    verdict = "принята" if status == "approved" else "отклонена"
    text = (
        f"заявка на смену тарифа #{request_id} {verdict}\n"
        f"user: {html.escape(username)}\n"
        f"{row['old_limit']} {devices_word(row['old_limit'])} / {row['old_price']}₽ → "
        f"{row['new_limit']} {devices_word(row['new_limit'])} / {row['new_price']}₽"
    )
    edit_message(chat_id, message_id, text)
    answer_callback(callback_id, verdict)
    if tg_id:
        if status == "approved":
            send_message(tg_id, f"тариф обновлён: {row['new_limit']} {devices_word(row['new_limit'])}, {row['new_price']}₽/мес.")
        else:
            send_message(tg_id, "заявка на смену тарифа отклонена.")


def link_user(tg_id: int, username: str, tg_username: str = "") -> None:
    conn = db()
    user = get_user_by_username(conn, username)
    if not user:
        # разрешаем линковку если юзер есть в Remnawave (pure-RW или pending /readdrw)
        rw = remnawave_user(username)
        if not rw:
            conn.close()
            send_message(tg_id, "ссылка недействительна.")
            return
    conn.execute(
        "INSERT OR REPLACE INTO client_tg_links (tg_id, username, tg_username, created_at) VALUES (?, ?, ?, ?)",
        (tg_id, username, tg_username, now_iso()),
    )
    conn.commit()
    conn.close()
    send_message(tg_id, f"добро пожаловать, <b>{html.escape(username)}</b>!", webapp_markup())


def admin_link_user(username: str, tg_id: int, tg_username: str = "") -> None:
    conn = db()
    user = get_user_by_username(conn, username)
    if not user:
        # разрешаем для RW-пользователей
        rw = remnawave_user(username)
        if not rw:
            conn.close()
            raise ValueError("user not found")
    conn.execute(
        "INSERT OR REPLACE INTO client_tg_links (tg_id, username, tg_username, created_at) VALUES (?, ?, ?, ?)",
        (tg_id, username, tg_username, now_iso()),
    )
    conn.commit()
    conn.close()


def recipient_ids(conn: sqlite3.Connection) -> set[int]:
    ids = {int(row["tg_id"]) for row in conn.execute("SELECT tg_id FROM client_tg_links")}
    cols = table_columns(conn, "users")
    if "tg_id" in cols:
        for row in conn.execute("SELECT tg_id FROM users WHERE tg_id IS NOT NULL AND tg_id != ''"):
            try:
                ids.add(int(row["tg_id"]))
            except Exception:
                pass
    return ids


def broadcast(text: str) -> int:
    conn = db()
    ids = recipient_ids(conn)
    conn.close()
    sent = 0
    for tg_id in ids:
        send_message(tg_id, text)
        sent += 1
        time.sleep(0.05)
    return sent


def usernames_due_today(conn: sqlite3.Connection) -> list[str]:
    target = (today_msk() + timedelta(days=1)).isoformat()
    rows = conn.execute("""
        SELECT u.name
        FROM users u
        JOIN client_profiles p ON p.username = u.name
        LEFT JOIN client_reminders r ON r.username = u.name AND r.remind_date = ?
        WHERE p.paid_until = ? AND r.username IS NULL
          AND p.payment_reminders_enabled = 1
    """, (target, target)).fetchall()
    return [row["name"] for row in rows]


def tg_id_for_username(conn: sqlite3.Connection, username: str) -> int | None:
    row = conn.execute("SELECT tg_id FROM client_tg_links WHERE username=?", (username,)).fetchone()
    if row:
        return int(row["tg_id"])
    cols = table_columns(conn, "users")
    if "tg_id" in cols:
        row = conn.execute("SELECT tg_id FROM users WHERE name=?", (username,)).fetchone()
        if row and row["tg_id"]:
            return int(row["tg_id"])
    return None


def send_due_reminders(force: bool = False) -> int:
    conn = db()
    reminder_date = (today_msk() + timedelta(days=1)).isoformat()
    names = usernames_due_today(conn)
    sent = 0
    for username in names:
        tg_id = tg_id_for_username(conn, username)
        if not tg_id:
            continue
        profile = ensure_profile(conn, username)
        send_message(
            tg_id,
            "напоминание об оплате goida vpn\n\n"
            f"завтра заканчивается подписка. тариф: {price_for(profile['device_limit'])}₽/мес.",
            webapp_markup(),
        )
        conn.execute(
            "INSERT OR REPLACE INTO client_reminders (username, remind_date, sent_at) VALUES (?, ?, ?)",
            (username, reminder_date, now_iso()),
        )
        sent += 1
    conn.commit()
    conn.close()
    return sent


def sweep_expired_personal_invites() -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    conn = db()
    rows = conn.execute(
        "SELECT id FROM client_invites WHERE status='tg_pending' AND first_fetch_at != '' AND first_fetch_at < ?",
        (cutoff,),
    ).fetchall()
    for row in rows:
        conn.execute("UPDATE client_invites SET status='expired' WHERE id=?", (row["id"],))
    conn.commit()
    conn.close()
    for row in rows:
        log_invite_event(int(row["id"]), "expire", {})
    return len(rows)


def reminder_loop() -> None:
    last = ""
    while True:
        current = today_msk().isoformat()
        if current != last:
            send_due_reminders()
            last = current
        try:
            sweep_expired_personal_invites()
        except Exception as exc:
            print(f"sweep_expired_personal_invites error: {exc}", file=sys.stderr)
        time.sleep(900)


def polling_loop() -> None:
    offset = 0
    while True:
        try:
            data = tg_api("getUpdates", {"timeout": 30, "offset": offset, "allowed_updates": ["message", "callback_query"]})
            for update in data.get("result", []):
                offset = max(offset, int(update["update_id"]) + 1)
                handle_update(update)
        except urllib.error.HTTPError as exc:
            print(f"poll http error: {exc}", file=sys.stderr)
            time.sleep(5)
        except Exception as exc:
            print(f"poll error: {exc}", file=sys.stderr)
            time.sleep(3)


def main() -> None:
    if not TOKEN:
        print("CLIENT_BOT_TOKEN не найден", file=sys.stderr)
        sys.exit(1)
    init_db()
    get_bot_username()
    if BOT_USERNAME:
        set_bot_setting("client_bot_username", BOT_USERNAME)
    Thread(target=reminder_loop, daemon=True).start()
    Thread(target=polling_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", WEB_PORT), ApiHandler)
    print(f"client api/web listening on http://127.0.0.1:{WEB_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
