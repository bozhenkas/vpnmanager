import json
import json
#!/usr/bin/env python3
"""
vpn-bot.py — телеграм-бот для управления VPN-кластером goida.fun

функционал:
  /start, /help — справка
  /users — список пользователей (inline-кнопки)
  /adduser <name> — добавить пользователя (создаёт клиентов в 3X-UI)
  /xray — статус xray и observatory
  /ping — проверка бота

inline-кнопки:
  user info → трафик, дата, ссылка подписки
  редактировать → ручная правка подписки
  перевыпустить → парсит ссылки заново из 3X-UI
  удалить → удаляет пользователя и клиентов из 3X-UI

подписки:
  HTTP-сервер на localhost:9090 отдаёт файлы по /subscribe/<token>
  nginx проксирует ru.goida.fun/subscribe/ → localhost:9090
"""

import json
import hashlib
import html
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import time
import uuid
import base64
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from pathlib import Path

from subscription import (
    DEFAULT_DESCRIPTION as NEXT_DEFAULT_DESCRIPTION,
    DEFAULT_SUPPORT_URL as NEXT_SUPPORT_URL,
    HAPP_ROUTING_LINE as NEXT_HAPP_ROUTING_LINE,
    SubscriptionEngine,
    deleted_sub_content as next_deleted_sub_content,
    unsupported_client_content,
)

# === конфиг ===

OWNER_ID = 294057781
XUI_DB = "/etc/x-ui/x-ui.db"
BOT_DB = "/root/vpn-bot/bot.db"
SUBS_DIR = "/root/vpn-bot/subscriptions"
DOMAIN = "ru.goida.fun"
IP_LIMIT = 4
SERVER_IPS = {"83.147.255.98", "77.110.108.57", "89.22.230.5"}
SUB_PORT = 9090  # внутренний порт для подписок
SUB_DESC_KEY = "subscribe_next_description"
LEGACY_SUB_KEY_PREFIX = "legacy_sub:"
SUB_STUB_LOGO = Path(__file__).with_name("goida.svg")

# инбаунды на RU-сервере
INBOUNDS = {
    "smart":   {"id": 5,  "port": 10003, "path": "/smart",    "tag": "inbound-10003", "prefix": "",        "remark_suffix": ""},
    "smart-pro": {"id": 16, "port": 10005, "path": "/smart-pro", "tag": "inbound-10005", "prefix": "",    "remark_suffix": "⚡"},
    "se":      {"id": 4,  "port": 10002, "path": "/se",       "tag": "inbound-10002", "prefix": "swe-",    "remark_suffix": "🇸🇪"},
    "fi":      {"id": 1,  "port": 10001, "path": "/fi",       "tag": "inbound-10001", "prefix": "fin-",    "remark_suffix": "🇫🇮"},
    "zapret":  {"id": 6,  "port": 10004, "path": "/direct",     "tag": "inbound-10004", "prefix": "zapret-","remark_suffix": " (youtube/discord)"},
}

# страны для hydra-инбаундов в подписочных названиях
HYDRA_COUNTRY_NAMES = {
    "usa":  "США",
    "pol":  "Польша",
    "tur":  "Турция",
    "nl":   "Нидерланды",
    "de":   "Германия",
    "fiws": "Финляндия",
}

# hydra — сторонние серверы из подписки whitestore.
# id/port/path/tag читаются динамически из x-ui.db по фиксированному port → key маппингу;
# prefix/flag/label/country остаются статикой (нужны для UI и email convention).
HYDRA_META = {
    10011: {"key": "usa",  "prefix": "usa-", "flag": "🇺🇸", "label": "usa"},
    10012: {"key": "pol",  "prefix": "pol-", "flag": "🇵🇱", "label": "poland"},
    10013: {"key": "tur",  "prefix": "tur-", "flag": "🇹🇷", "label": "turkey"},
    10014: {"key": "nl",   "prefix": "nl-",  "flag": "🇳🇱", "label": "netherlands"},
    10015: {"key": "de",   "prefix": "de-",  "flag": "🇩🇪", "label": "germany"},
    10016: {"key": "fiws", "prefix": "fi2-", "flag": "🇫🇮", "label": "finland-ws"},
}


def _load_hydra_inbounds() -> dict:
    """читает hydra-инбаунды из x-ui.db, возвращает HYDRA_INBOUNDS-dict.
    Берёт enable=1 инбаунды по port из HYDRA_META, оверлеит метаданные.
    Если инбаунд не найден / disabled — ключа не будет в словаре."""
    out: dict = {}
    try:
        conn = sqlite3.connect(XUI_DB, timeout=30)
        rows = conn.execute(
            "SELECT id, port, tag, stream_settings, enable FROM inbounds "
            "WHERE port IN (10011,10012,10013,10014,10015,10016)"
        ).fetchall()
        conn.close()
    except Exception:
        rows = []
    for ib_id, port, tag, stream_str, enable in rows:
        if not enable:
            continue
        meta = HYDRA_META.get(port)
        if not meta:
            continue
        try:
            path = json.loads(stream_str).get("wsSettings", {}).get("path", f"/{port}")
        except Exception:
            path = f"/{port}"
        out[meta["key"]] = {
            "id": ib_id,
            "port": port,
            "path": path,
            "tag": tag,
            "prefix": meta["prefix"],
            "flag": meta["flag"],
            "label": meta["label"],
        }
    return out


# заполнится из БД при старте + лениво при обращении
HYDRA_INBOUNDS: dict = {}

WL_FILE          = "/opt/sub-updater/whitelist_links.txt"
WL_REGISTRY_FILE = "/opt/wl-registry/wl-list.txt"
HYSTERIA_LINK = "hysteria2://a5ab16e3a57158eec010e65eaa010dd5@77.110.108.57:8443/?sni=fin.goida.fun#hysteria2%F0%9F%87%AB%F0%9F%87%AE"
import base64 as _b64, json as _json
_happ_profile = {
    "Name": "goida.fun — RU Direct",
    "GlobalProxy": "true",
    "RemoteDNSType": "DoH",
    "RemoteDNSDomain": "https://cloudflare-dns.com/dns-query",
    "RemoteDNSIP": "1.1.1.1",
    "DomesticDNSType": "DoH",
    "DomesticDNSDomain": "https://dns.yandex.ru/dns-query",
    "DomesticDNSIP": "77.88.8.8",
    "DirectSites": ["geosite:category-ru"],
    "DirectIp": [
        "geoip:ru",
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "169.254.0.0/16", "224.0.0.0/4", "255.255.255.255/32"
    ],
    "DomainStrategy": "IPIfNonMatch",
    "FakeDNS": "false"
}
HAPP_ROUTING_LINE = "happ://routing/onadd/" + _b64.b64encode(_json.dumps(_happ_profile, ensure_ascii=False).encode()).decode()


DOTENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def load_token() -> str:
    if os.path.exists(DOTENV_PATH):
        with open(DOTENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith("BOT_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("BOT_TOKEN не найден", file=sys.stderr)
        sys.exit(1)
    return token


# === бот-база данных ===

def init_bot_db():
    os.makedirs(os.path.dirname(BOT_DB), exist_ok=True)
    os.makedirs(SUBS_DIR, exist_ok=True)
    conn = sqlite3.connect(BOT_DB, timeout=30)
    # WAL — снижает write-lock конфликты с другими процессами (sub-updater и т.п.)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            name TEXT PRIMARY KEY,
            token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            custom_sub TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_ips (
            token TEXT NOT NULL,
            ip TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            user_agent TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (token, ip)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_devices (
            token TEXT NOT NULL,
            device_id TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            client_ip TEXT NOT NULL DEFAULT '',
            user_agent TEXT NOT NULL DEFAULT '',
            app_name TEXT NOT NULL DEFAULT '',
            app_version TEXT NOT NULL DEFAULT '',
            platform TEXT NOT NULL DEFAULT '',
            platform_version TEXT NOT NULL DEFAULT '',
            device_name TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'subscription',
            PRIMARY KEY (token, device_id)
        )
    """)
    # «надгробие» — токены удалённых юзеров остаются живыми, отдают stub-инбаунд
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deleted_subs (
            token TEXT PRIMARY KEY,
            deleted_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)",
        (SUB_DESC_KEY, NEXT_DEFAULT_DESCRIPTION),
    )
    conn.commit()
    # миграция: добавляем user_agent если нет
    try:
        conn.execute("ALTER TABLE user_ips ADD COLUMN user_agent TEXT NOT NULL DEFAULT ''")
        conn.commit()
    except Exception:
        pass  # колонка уже есть
    conn.close()


def get_all_users() -> list[dict]:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    rows = conn.execute("SELECT name, token, created_at FROM users ORDER BY created_at").fetchall()
    conn.close()
    return [{"name": r[0], "token": r[1], "created_at": r[2]} for r in rows]


def get_user(name: str) -> dict | None:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    row = conn.execute("SELECT name, token, created_at, custom_sub FROM users WHERE name=?", (name,)).fetchone()
    conn.close()
    if row:
        return {"name": row[0], "token": row[1], "created_at": row[2], "custom_sub": row[3]}
    return None


def get_user_by_token(token: str) -> dict | None:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    row = conn.execute(
        "SELECT name, token, created_at, custom_sub FROM users WHERE token=?", (token,)
    ).fetchone()
    conn.close()
    if row:
        return {"name": row[0], "token": row[1], "created_at": row[2], "custom_sub": row[3]}
    return None


def add_user_db(name: str) -> str:
    """добавляет пользователя в бот-бд, возвращает token"""
    token = secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.execute("INSERT INTO users (name, token, created_at) VALUES (?, ?, ?)",
                 (name, token, created))
    conn.commit()
    conn.close()
    return token


def delete_user_db(name: str):
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.execute("DELETE FROM users WHERE name=?", (name,))
    conn.commit()
    conn.close()


def set_custom_sub(name: str, content: str):
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.execute("UPDATE users SET custom_sub=? WHERE name=?", (content, name))
    conn.commit()
    conn.close()


def mark_deleted_sub(token: str):
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.execute("INSERT OR REPLACE INTO deleted_subs (token, deleted_at) VALUES (?, ?)",
                 (token, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def is_deleted_sub(token: str) -> bool:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    row = conn.execute("SELECT 1 FROM deleted_subs WHERE token=?", (token,)).fetchone()
    conn.close()
    return bool(row)


def deleted_sub_content() -> str:
    msg1 = urllib.parse.quote("⛔ пользователь удалён")
    msg2 = urllib.parse.quote("обратитесь к @bozhenkas")
    stub = "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443/?type=tcp&security=none"
    return "\n".join([
        "#profile-title: ❌ пользователь удалён",
        f"{stub}#{msg1}",
        f"{stub}#{msg2}",
    ])


def get_bot_setting(key: str, default: str = "") -> str:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    row = conn.execute("SELECT value FROM bot_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default


def set_bot_setting(key: str, value: str):
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.execute(
        "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()
    conn.close()


def legacy_sub_get(username: str) -> bool:
    return get_bot_setting(LEGACY_SUB_KEY_PREFIX + username, "0") == "1"


def legacy_sub_set(username: str, enable: bool):
    set_bot_setting(LEGACY_SUB_KEY_PREFIX + username, "1" if enable else "0")


def get_subscribe_next_description() -> str:
    return get_bot_setting(SUB_DESC_KEY, NEXT_DEFAULT_DESCRIPTION)


# === 3X-UI управление ===

def xui_get_inbound(inbound_id: int) -> dict | None:
    conn = sqlite3.connect(XUI_DB, timeout=30)
    row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return None


def xui_add_client(inbound_id: int, email: str, client_uuid: str):
    """добавляет клиента в inbound 3X-UI"""
    conn = sqlite3.connect(XUI_DB, timeout=30)
    row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"inbound {inbound_id} не найден")

    settings = json.loads(row[0])
    clients = settings.get("clients", [])

    # проверяем нет ли уже такого email
    if any(c.get("email") == email for c in clients):
        conn.close()
        return  # уже есть

    clients.append({
        "id": client_uuid,
        "flow": "",
        "email": email,
        "limitIp": 4,
        "totalGB": 0,
        "expiryTime": 0,
        "enable": True,
        "tgId": "",
        "subId": "",
        "comment": "",
        "reset": 0
    })

    settings["clients"] = clients
    conn.execute("UPDATE inbounds SET settings=? WHERE id=?",
                 (json.dumps(settings), inbound_id))
    # создаём запись трафика — без неё 3x-ui не показывает статистику
    conn.execute(
        "INSERT OR IGNORE INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset) VALUES (?, 1, ?, 0, 0, 0, 0, 0)",
        (inbound_id, email)
    )
    conn.commit()
    conn.close()


def xui_remove_client(inbound_id: int, email: str):
    """удаляет клиента из inbound 3X-UI"""
    conn = sqlite3.connect(XUI_DB, timeout=30)
    row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()
    if not row:
        conn.close()
        return

    settings = json.loads(row[0])
    clients = settings.get("clients", [])
    settings["clients"] = [c for c in clients if c.get("email") != email]

    conn.execute("UPDATE inbounds SET settings=? WHERE id=?",
                 (json.dumps(settings), inbound_id))
    conn.commit()
    conn.close()


def xui_find_client(inbound_id: int, email: str) -> dict | None:
    """находит клиента по email в inbound"""
    settings = xui_get_inbound(inbound_id)
    if not settings:
        return None
    for c in settings.get("clients", []):
        if c.get("email") == email:
            return c
    return None


def xui_get_traffic(email: str) -> dict:
    """получает трафик клиента из client_traffics"""
    conn = sqlite3.connect(XUI_DB, timeout=30)
    rows = conn.execute(
        "SELECT inbound_id, up, down FROM client_traffics WHERE email LIKE ?",
        (f"%{email}%",)
    ).fetchall()
    conn.close()

    total_up = sum(r[1] for r in rows)
    total_down = sum(r[2] for r in rows)
    per_inbound = {str(r[0]): {"up": r[1], "down": r[2]} for r in rows}

    return {"total_up": total_up, "total_down": total_down, "per_inbound": per_inbound}


def xui_get_inbound_runtime(inbound_id: int) -> dict | None:
    """читает settings + stream_settings для нового shadow subscription engine"""
    conn = sqlite3.connect(XUI_DB, timeout=30)
    row = conn.execute(
        "SELECT settings, stream_settings, enable FROM inbounds WHERE id=?", (inbound_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    settings, stream_settings, enable = row
    return {
        "settings": json.loads(settings or "{}"),
        "stream_settings": json.loads(stream_settings or "{}"),
        "enable": bool(enable),
    }


def xui_restart():
    """рестарт xray через x-ui"""
    subprocess.run(["x-ui", "restart"], capture_output=True, timeout=15)


def format_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    elif b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f} MB"
    else:
        return f"{b / 1024 ** 3:.2f} GB"


# === генерация ссылок подписки ===

def generate_vless_link(client_uuid: str, inbound_key: str, username: str) -> str:
    """генерирует vless ссылку для клиента"""
    ib = INBOUNDS[inbound_key]
    path = urllib.parse.quote(ib["path"])

    if inbound_key == "smart":
        remark = f"smart-{username} 🇸🇨"
    elif inbound_key == "smart-pro":
        remark = f"smart-pro-{username}⚡"
    elif inbound_key == "zapret":
        remark = "ru-zapret (discord/youtube) 🇷🇺"
    elif inbound_key == "fi":
        remark = f"fin-{username} 🇫🇮"
    else:
        remark = f"swe-{username} 🇸🇪"

    remark_encoded = urllib.parse.quote(remark)

    
    transport_type = "ws"
    xhttp_params = ""
    return (
        f"vless://{client_uuid}@{DOMAIN}:443/"
        f"?type={transport_type}&security=tls&sni={DOMAIN}"
        f"&path={path}&host={DOMAIN}{xhttp_params}"
        f"#{remark_encoded}"
    )



HAPP_ROUTING_LINE = (
    "happ://routing/onadd/"
    "eyJOYW1lIjoiZ29pZGEuZnVuIOKAlCBTbWFydCIsIkdsb2JhbFByb3h5IjoidHJ1ZSIsIlJlbW90ZUROU1R5cGUiOiJE"
    "b0giLCJSZW1vdGVETlNEb21haW4iOiJodHRwczovL2Nsb3VkZmxhcmUtZG5zLmNvbS9kbnMtcXVlcnkiLCJSZW1vdGVE"
    "TlNJUCI6IjEuMS4xLjEiLCJEb21lc3RpY0ROU1R5cGUiOiJEb0giLCJEb21lc3RpY0ROU0RvbWFpbiI6Imh0dHBzOi8v"
    "ZG5zLnlhbmRleC5ydS9kbnMtcXVlcnkiLCJEb21lc3RpY0ROU0lQIjoiNzcuODguOC44IiwiRGlyZWN0U2l0ZXMiOlsi"
    "Z2Vvc2l0ZTpjYXRlZ29yeS1ydSIsImRvbWFpbjpzYmVyYmFuay5ydSIsImRvbWFpbjpzYnJmLnJ1IiwiZG9tYWluOnNi"
    "ZXIucnUiLCJkb21haW46dGlua29mZi5ydSIsImRvbWFpbjp0YmFuay5ydSIsImRvbWFpbjp2dGIucnUiLCJkb21haW46"
    "YWxmYWJhbmsucnUiLCJkb21haW46cmFpZmZlaXNlbi5ydSIsImRvbWFpbjpnYXpwcm9tYmFuay5ydSIsImRvbWFpbjpn"
    "b3N1c2x1Z2kucnUiLCJkb21haW46ZXNpYS5nb3N1c2x1Z2kucnUiLCJkb21haW46bmFsb2cucnUiLCJkb21haW46bW9z"
    "LnJ1IiwiZG9tYWluOmdvdi5zcGIucnUiLCJkb21haW46d2lsZGJlcnJpZXMucnUiLCJkb21haW46d2JjZG4ucnUiLCJk"
    "b21haW46d2IucnUiLCJkb21haW46d2JiYXNrZXQucnUiLCJkb21haW46d2J4LnJ1IiwiZG9tYWluOndic3RhdGljLm5l"
    "dCIsImRvbWFpbjpvem9uLnJ1IiwiZG9tYWluOm96b251c2VyY29udGVudC5jb20iLCJkb21haW46YXZpdG8ucnUiLCJk"
    "b21haW46YXZpdG8uc3QiLCJkb21haW46bGFtb2RhLnJ1IiwiZG9tYWluOnNiZXJtZWdhbWFya2V0LnJ1IiwiZG9tYWlu"
    "OnNiZXJtYXJrZXQucnUiLCJkb21haW46c2Jlcm1hcmtldC5jb20iLCJkb21haW46bWFnbml0LnJ1IiwiZG9tYWluOm1h"
    "Z25pdG1hcmtldC5ydSIsImRvbWFpbjpkaXh5LnJ1IiwiZG9tYWluOnB5YXRlcm9jaGthLnJ1IiwiZG9tYWluOnBlcmVr"
    "cmVzdG9rLnJ1IiwiZG9tYWluOmxlbWFuYXByby5ydSIsImRvbWFpbjp2c2VpbnN0cnVtZW50eS5ydSIsImRvbWFpbjp2"
    "a3VzdmlsbC5ydSIsImRvbWFpbjpsZW50YS5jb20iLCJkb21haW46dmsuY29tIiwiZG9tYWluOnZrLnJ1IiwiZG9tYWlu"
    "Om9rLnJ1IiwiZG9tYWluOmhoLnJ1IiwiZG9tYWluOmhlYWRodW50ZXIucnUiLCJkb21haW46Y2lhbi5ydSIsImRvbWFp"
    "bjoyZ2lzLnJ1IiwiZG9tYWluOjJnaXMuY29tIiwiZG9tYWluOml2aS5ydSIsImRvbWFpbjpva2tvLnR2IiwiZG9tYWlu"
    "OndpbmsucnUiLCJkb21haW46bW9yZS50diIsImRvbWFpbjpsaXRyZXMucnUiLCJkb21haW46Z2lzbWV0ZW8ucnUiLCJk"
    "b21haW46cmFtYmxlci5ydSIsImRvbWFpbjp0dXR1LnJ1IiwiZG9tYWluOmZ1bnBheS5jb20iLCJkb21haW46bWFuZ2Fs"
    "aWIub3JnIl0sIkRpcmVjdElQIjpbImdlb2lwOnJ1IiwiMTAuMC4wLjAvOCIsIjE3Mi4xNi4wLjAvMTIiLCIxOTIuMTY4"
    "LjAuMC8xNiIsIjE2OS4yNTQuMC4wLzE2IiwiMjI0LjAuMC4wLzQiLCIyNTUuMjU1LjI1NS4yNTUvMzIiXSwiRG9tYWlu"
    "U3RyYXRlZ3kiOiJJUElmTm9uTWF0Y2giLCJGYWtlRE5TIjoiZmFsc2UifQ=="
)

try:
    _happ_payload = _json.loads(_b64.b64decode(HAPP_ROUTING_LINE.rsplit("/", 1)[1]).decode())
    if "DirectIP" in _happ_payload:
        _happ_payload["DirectIp"] = _happ_payload.pop("DirectIP")
        HAPP_ROUTING_LINE = (
            "happ://routing/onadd/"
            + _b64.b64encode(
                _json.dumps(_happ_payload, ensure_ascii=False, separators=(",", ":")).encode()
            ).decode()
        )
except Exception:
    pass

def generate_subscription(username: str) -> str:
    """генерирует содержимое подписки из текущих клиентов в 3X-UI"""
    lines = [HAPP_ROUTING_LINE, "#profile-title: goida :)"]

    for key, ib in INBOUNDS.items():
        if key == "smart-pro" and username != "bozhenkas":
            continue
        if key in ("smart", "smart-pro"):
            email = username
        elif ib.get("prefix"):
            email = f"{ib['prefix']}{username}"
        else:
            email = username

        client = xui_find_client(ib["id"], email)
        if client:
            link = generate_vless_link(client["id"], key, username)
            lines.append(link)


    # добавляем hydra если включён
    hydra_links = generate_hydra_links(username)
    lines.extend(hydra_links)

    # добавляем whitelist серверы если включены
    if wl_get_status(username):
        lines.extend(generate_wl_links())

    # дописываем доп.ссылки из custom_sub (только append, не подменяет)
    user_row = get_user(username)
    if user_row and user_row.get("custom_sub"):
        for extra in user_row["custom_sub"].split("\n"):
            extra = extra.strip()
            if extra:
                lines.append(extra)

    return "\n".join(lines)


def hydra_get_status(username: str) -> bool:
    """проверяет включён ли hydra для пользователя (по наличию enable=true клиента в nl-инбаунде)"""
    conn = sqlite3.connect(XUI_DB, timeout=30)
    row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (HYDRA_INBOUNDS["nl"]["id"],)).fetchone()
    conn.close()
    if not row:
        return False
    email = f"nl-{username}"
    for c in json.loads(row[0]).get("clients", []):
        if c.get("email") == email:
            return c.get("enable", False)
    return False


def hydra_set(username: str, enable: bool):
    """включает или выключает hydra для пользователя во всех hydra-инбаундах"""
    conn = sqlite3.connect(XUI_DB, timeout=30)
    for key, ib in HYDRA_INBOUNDS.items():
        row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (ib["id"],)).fetchone()
        if not row:
            continue
        s = json.loads(row[0])
        clients = s.get("clients", [])
        email = f"{ib['prefix']}{username}"
        found = False
        for c in clients:
            if c.get("email") == email:
                c["enable"] = enable
                found = True
                break
        if not found and enable:
            # клиента нет — создаём
            clients.append({
                "id": str(uuid.uuid4()),
                "flow": "",
                "email": email,
                "limitIp": 0,
                "totalGB": 0,
                "expiryTime": 0,
                "enable": True,
                "tgId": "",
                "subId": "",
                "comment": "",
                "reset": 0
            })
            # создаём запись трафика
            conn.execute(
                "INSERT OR IGNORE INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset) VALUES (?, 1, ?, 0, 0, 0, 0, 0)",
                (ib["id"], email)
            )
        s["clients"] = clients
        conn.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(s), ib["id"]))
    conn.commit()
    conn.close()
    xui_restart()


def wl_get_status(username: str) -> bool:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    row = conn.execute("SELECT wl FROM users WHERE name=?", (username,)).fetchone()
    conn.close()
    return bool(row[0]) if row and row[0] else False


def wl_set(username: str, enable: bool):
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.execute("UPDATE users SET wl=? WHERE name=?", (1 if enable else 0, username))
    conn.commit()
    conn.close()


def generate_wl_links() -> list:
    try:
        with open(WL_FILE) as f:
            return [l.strip() for l in f.readlines() if l.strip()]
    except FileNotFoundError:
        return []


def hysteria_get_status(username: str) -> bool:
    """проверяет включена ли hysteria для пользователя (поле в bot.db)"""
    conn = sqlite3.connect(BOT_DB, timeout=30)
    row = conn.execute("SELECT hysteria FROM users WHERE name=?", (username,)).fetchone()
    conn.close()
    if row and row[0]:
        return bool(row[0])
    return False


def hysteria_set(username: str, enable: bool):
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.execute("UPDATE users SET hysteria=? WHERE name=?", (1 if enable else 0, username))
    conn.commit()
    conn.close()


def generate_hydra_links(username: str) -> list:
    """генерирует vless-ссылки для hydra если клиент включён"""
    links = []
    conn = sqlite3.connect(XUI_DB, timeout=30)
    for key, ib in HYDRA_INBOUNDS.items():
        row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (ib["id"],)).fetchone()
        if not row:
            continue
        email = f"{ib['prefix']}{username}"
        for c in json.loads(row[0]).get("clients", []):
            if c.get("email") == email and c.get("enable", False):
                params = f"type=ws&security=tls&sni={DOMAIN}&path={ib['path']}&host={DOMAIN}"
                country = HYDRA_COUNTRY_NAMES.get(key, key)
                remark = urllib.parse.quote(f"{country} (hydra) {ib['flag']}")
                links.append(f"vless://{c['id']}@{DOMAIN}:443/?{params}#{remark}")
                break
    conn.close()
    return links


def hydra_get_status(username: str) -> bool:
    """проверяет включён ли hydra для пользователя (по наличию enable=true клиента в nl-инбаунде)"""
    conn = sqlite3.connect(XUI_DB, timeout=30)
    row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (HYDRA_INBOUNDS["nl"]["id"],)).fetchone()
    conn.close()
    if not row:
        return False
    email = f"nl-{username}"
    for c in json.loads(row[0]).get("clients", []):
        if c.get("email") == email:
            return c.get("enable", False)
    return False


def hydra_set(username: str, enable: bool):
    """включает или выключает hydra для пользователя во всех hydra-инбаундах"""
    conn = sqlite3.connect(XUI_DB, timeout=30)
    for key, ib in HYDRA_INBOUNDS.items():
        row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (ib["id"],)).fetchone()
        if not row:
            continue
        s = json.loads(row[0])
        clients = s.get("clients", [])
        email = f"{ib['prefix']}{username}"
        found = False
        for c in clients:
            if c.get("email") == email:
                c["enable"] = enable
                found = True
                break
        if not found and enable:
            # клиента нет — создаём
            clients.append({
                "id": str(uuid.uuid4()),
                "flow": "",
                "email": email,
                "limitIp": 0,
                "totalGB": 0,
                "expiryTime": 0,
                "enable": True,
                "tgId": "",
                "subId": "",
                "comment": "",
                "reset": 0
            })
            # создаём запись трафика
            conn.execute(
                "INSERT OR IGNORE INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset) VALUES (?, 1, ?, 0, 0, 0, 0, 0)",
                (ib["id"], email)
            )
        s["clients"] = clients
        conn.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(s), ib["id"]))
    conn.commit()
    conn.close()
    xui_restart()


def save_subscription(username: str, content: str | None = None):
    """сохраняет файл подписки"""
    user = get_user(username)
    if not user:
        return

    if content is None:
        content = generate_subscription(username)

    sub_path = os.path.join(SUBS_DIR, user["token"])
    with open(sub_path, "w") as f:
        f.write(content)


def get_subscription_content(username: str) -> str | None:
    """читает текущее содержимое подписки"""
    user = get_user(username)
    if not user:
        return None

    sub_path = os.path.join(SUBS_DIR, user["token"])
    if os.path.exists(sub_path):
        with open(sub_path) as f:
            return f.read()

    # если файла нет — генерируем
    content = generate_subscription(username)
    save_subscription(username, content)
    return content


# === HTTP-сервер подписок ===


# === проверка IP лимита ===


def parse_device_metadata(device_id: str, client_ip: str, user_agent: str) -> dict:
    ua = (user_agent or "").strip()
    app_name = ""
    app_version = ""
    platform = ""
    platform_version = ""
    device_name = ""

    if ua.startswith("Happ/"):
        parts = ua.split("/")
        app_name = "Happ"
        if len(parts) > 1:
            platform = parts[1].strip()
        if len(parts) > 2:
            app_version = parts[2].strip()
        if len(parts) > 3:
            device_name = parts[3].strip()
    elif ua:
        first = ua.split()[0]
        if "/" in first:
            app_name, app_version = first.split("/", 1)
        else:
            app_name = first[:40]

    lower = ua.lower()
    if not platform:
        if "iphone" in lower or "ipad" in lower or "ios" in lower:
            platform = "iOS"
        elif "android" in lower:
            platform = "Android"
        elif "windows" in lower or "v2rayn" in lower:
            platform = "Windows"
        elif "mac os" in lower or "macintosh" in lower or "darwin" in lower:
            platform = "macOS"
        elif "linux" in lower or "x11" in lower:
            platform = "Linux"

    version_patterns = [
        r"(?:iOS|CPU iPhone OS|CPU OS)\s*([0-9_\.]+)",
        r"Android\s*([0-9\.]+)",
        r"Mac OS X\s*([0-9_\.]+)",
        r"Windows NT\s*([0-9\.]+)",
    ]
    for pattern in version_patterns:
        match = re.search(pattern, ua, re.I)
        if match:
            platform_version = match.group(1).replace("_", ".")
            break

    known_devices = [
        "iPhone", "iPad", "Macintosh", "Windows", "Android",
        "Happ", "v2rayN", "Clash", "Streisand", "Nekoray", "sing-box",
    ]
    if not device_name:
        for name in known_devices:
            if name.lower() in lower:
                device_name = name
                break
    if not device_name:
        device_name = device_id.replace("hwid:", "").replace("ip:", "")

    return {
        "client_ip": client_ip or "",
        "user_agent": ua[:300],
        "app_name": app_name[:60],
        "app_version": app_version[:60],
        "platform": platform[:60],
        "platform_version": platform_version[:60],
        "device_name": device_name[:120],
        "source": "subscription",
    }


def upsert_device_record(token: str, device_id: str, client_ip: str, user_agent: str):
    if not token or not device_id:
        return
    now = datetime.now(timezone.utc).isoformat()
    meta = parse_device_metadata(device_id, client_ip, user_agent)
    conn = sqlite3.connect(BOT_DB, timeout=30)
    row = conn.execute(
        "SELECT 1 FROM user_devices WHERE token=? AND device_id=?",
        (token, device_id),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE user_devices
            SET last_seen=?, client_ip=?, user_agent=?, app_name=?, app_version=?,
                platform=?, platform_version=?, device_name=?, source=?
            WHERE token=? AND device_id=?
            """,
            (
                now, meta["client_ip"], meta["user_agent"], meta["app_name"],
                meta["app_version"], meta["platform"], meta["platform_version"],
                meta["device_name"], meta["source"], token, device_id,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO user_devices (
                token, device_id, first_seen, last_seen, client_ip, user_agent,
                app_name, app_version, platform, platform_version, device_name, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token, device_id, now, now, meta["client_ip"], meta["user_agent"],
                meta["app_name"], meta["app_version"], meta["platform"],
                meta["platform_version"], meta["device_name"], meta["source"],
            ),
        )
    conn.commit()
    conn.close()


def get_device_id(ip: str, ua: str) -> str:
    return SubscriptionEngine.device_id(ip, ua)

def check_ip_limit(token: str, client_ip: str, content: str, user_agent: str = "") -> str:

    """
    отслеживает уникальные устройства по IP+UA.
    серверные IP игнорируются.
    если уникальных IP больше лимита — возвращает заглушку.
    """
    # игнорируем обращения с серверов впн
    if client_ip in SERVER_IPS:
        return content

    now = datetime.now(timezone.utc).isoformat()
    ua = user_agent[:200] if user_agent else ""
    device_id = get_device_id(client_ip, user_agent)
    if not device_id:
        return unsupported_client_content()

    try:
        conn = sqlite3.connect(BOT_DB, timeout=30)

        existing = conn.execute(
            "SELECT ip FROM user_ips WHERE token=? AND ip=?", (token, device_id)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE user_ips SET last_seen=?, user_agent=? WHERE token=? AND ip=?",
                (now, ua, token, device_id)
            )
        else:
            conn.execute(
                "INSERT INTO user_ips (token, ip, first_seen, last_seen, user_agent) VALUES (?, ?, ?, ?, ?)",
                (token, device_id, now, now, ua)
            )

        conn.commit()
        upsert_device_record(token, device_id, client_ip, user_agent)

        # лимит смотрим из 3X-UI для конкретного пользователя
        user_limit = IP_LIMIT
        try:
            xui_conn = sqlite3.connect(XUI_DB, timeout=30)
            xui_row = xui_conn.execute(
                "SELECT settings FROM inbounds WHERE id=?", (INBOUNDS["smart"]["id"],)
            ).fetchone()
            xui_conn.close()
            if xui_row:
                token_to_email = sqlite3.connect(BOT_DB, timeout=30).execute(
                    "SELECT name FROM users WHERE token=?", (token,)
                ).fetchone()
                if token_to_email:
                    uname = token_to_email[0]
                    for c in json.loads(xui_row[0]).get("clients", []):
                        if c.get("email") == uname:
                            user_limit = c.get("limitIp", IP_LIMIT)
                            break
        except Exception:
            pass

        count = conn.execute(
            "SELECT COUNT(*) FROM user_ips WHERE token=?", (token,)
        ).fetchone()[0]

        conn.close()

        if user_limit != 0 and count > user_limit:
            stub_remark = urllib.parse.quote(f"⚠️ лимит: {user_limit} устройства. если ошибка — @bozhenkas")
            stub = f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443/?type=tcp&security=none#{stub_remark}"
            return f"#profile-title: goida :) — лимит превышен\n{stub}"

    except Exception:
        pass

    return content


def read_wl_links() -> list[str]:
    try:
        with open(WL_FILE) as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []


def build_next_engine() -> SubscriptionEngine:
    inbounds = {}
    for key, ib in INBOUNDS.items():
        copy_ib = dict(ib)
        runtime = xui_get_inbound_runtime(ib["id"])
        if runtime:
            copy_ib["stream_settings"] = runtime["stream_settings"]
            copy_ib["enable"] = runtime["enable"]
        inbounds[key] = copy_ib

    hydra_inbounds = {}
    for key, ib in HYDRA_INBOUNDS.items():
        copy_ib = dict(ib)
        runtime = xui_get_inbound_runtime(ib["id"])
        if runtime:
            copy_ib["stream_settings"] = runtime["stream_settings"]
            copy_ib["enable"] = runtime["enable"]
        hydra_inbounds[key] = copy_ib

    return SubscriptionEngine(
        domain=DOMAIN,
        inbounds=inbounds,
        hydra_inbounds=hydra_inbounds,
        hydra_country_names=HYDRA_COUNTRY_NAMES,
        hysteria_link="",
        server_ips=SERVER_IPS,
        default_ip_limit=IP_LIMIT,
    )


def email_for_inbound(username: str, key: str, inbound: dict) -> str:
    if key in ("smart", "smart-pro"):
        return username
    if inbound.get("prefix"):
        return f"{inbound['prefix']}{username}"
    return username


def get_next_clients(username: str) -> tuple[dict, dict]:
    clients_by_key = {}
    for key, ib in INBOUNDS.items():
        if key == "smart-pro" and username != "bozhenkas":
            continue
        client = xui_find_client(ib["id"], email_for_inbound(username, key, ib))
        if client:
            clients_by_key[key] = client

    hydra_clients_by_key = {}
    for key, ib in HYDRA_INBOUNDS.items():
        client = xui_find_client(ib["id"], f"{ib['prefix']}{username}")
        if client:
            hydra_clients_by_key[key] = client
    return clients_by_key, hydra_clients_by_key


def get_next_traffic(username: str) -> dict:
    emails = set()
    for key, ib in INBOUNDS.items():
        if key == "smart-pro" and username != "bozhenkas":
            continue
        emails.add(email_for_inbound(username, key, ib))
    for ib in HYDRA_INBOUNDS.values():
        emails.add(f"{ib['prefix']}{username}")

    if not emails:
        return {"up": 0, "down": 0, "total": 0, "expire": 0}

    conn = sqlite3.connect(XUI_DB, timeout=30)
    placeholders = ",".join("?" for _ in emails)
    rows = conn.execute(
        f"SELECT up, down, total, expiry_time FROM client_traffics WHERE email IN ({placeholders})",
        tuple(emails),
    ).fetchall()
    conn.close()
    up = sum(int(row[0] or 0) for row in rows)
    down = sum(int(row[1] or 0) for row in rows)
    totals = [int(row[2] or 0) for row in rows if int(row[2] or 0) > 0]
    expires = [int(row[3] or 0) for row in rows if int(row[3] or 0) > 0]
    return {
        "up": up,
        "down": down,
        "total": sum(totals) if totals else 0,
        "expire": max(expires) // 1000 if expires else 0,
    }


def get_next_user_limit(username: str) -> int:
    client = xui_find_client(INBOUNDS["smart"]["id"], username)
    if client:
        return int(client.get("limitIp", IP_LIMIT) or 0)
    return IP_LIMIT


def get_known_devices(token: str) -> set[str]:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    rows = conn.execute("SELECT ip FROM user_ips WHERE token=?", (token,)).fetchall()
    conn.close()
    return {row[0] for row in rows}


def remember_device(token: str, device_id: str, client_ip: str, user_agent: str):
    if not device_id:
        return
    now = datetime.now(timezone.utc).isoformat()
    ua = user_agent[:200] if user_agent else ""
    conn = sqlite3.connect(BOT_DB, timeout=30)
    existing = conn.execute(
        "SELECT ip FROM user_ips WHERE token=? AND ip=?", (token, device_id)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE user_ips SET last_seen=?, user_agent=? WHERE token=? AND ip=?",
            (now, ua, token, device_id),
        )
    else:
        conn.execute(
            "INSERT INTO user_ips (token, ip, first_seen, last_seen, user_agent) VALUES (?, ?, ?, ?, ?)",
            (token, device_id, now, now, ua),
        )
    conn.commit()
    conn.close()
    upsert_device_record(token, device_id, client_ip, user_agent)


def send_text(handler: BaseHTTPRequestHandler, status: int, body: str, headers: dict[str, str]):
    handler.send_response(status)
    for key, value in headers.items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body.encode())


def get_user_devices(token: str) -> list[dict]:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    rows = conn.execute(
        """
        SELECT device_id, first_seen, last_seen, client_ip, user_agent,
               app_name, app_version, platform, platform_version, device_name, source
        FROM user_devices
        WHERE token=?
        ORDER BY last_seen DESC
        """,
        (token,),
    ).fetchall()
    conn.close()
    return [
        {
            "device_id": r[0], "first_seen": r[1], "last_seen": r[2],
            "client_ip": r[3], "user_agent": r[4], "app_name": r[5],
            "app_version": r[6], "platform": r[7], "platform_version": r[8],
            "device_name": r[9], "source": r[10],
        }
        for r in rows
    ]


def fmt_dt_short(value: str) -> str:
    if not value:
        return "н/д"
    return value[:16].replace("T", " ")


def format_device_line(device: dict, index: int) -> str:
    title = device.get("device_name") or device.get("device_id") or "unknown"
    platform = device.get("platform") or "н/д"
    platform_version = device.get("platform_version") or ""
    app = device.get("app_name") or "client"
    app_version = device.get("app_version") or ""
    app_label = f"{app} {app_version}".strip()
    platform_label = f"{platform} {platform_version}".strip()
    return (
        f"{index}. <b>{html.escape(title)}</b>\n"
        f"   {html.escape(platform_label)} · {html.escape(app_label)}\n"
        f"   IP: <code>{html.escape(device.get('client_ip') or 'н/д')}</code>\n"
        f"   first: <i>{fmt_dt_short(device.get('first_seen', ''))}</i>\n"
        f"   last: <i>{fmt_dt_short(device.get('last_seen', ''))}</i>\n"
        f"   id: <code>{html.escape(device.get('device_id') or '')}</code>"
    )


def handle_subscribe_next(
    handler: BaseHTTPRequestHandler,
    token: str,
    kind: str,
    public_path: str = "subscribe-next",
    require_hwid: bool = True,
):
    user = get_user_by_token(token)
    engine = build_next_engine()
    description = get_subscribe_next_description()

    if not user:
        if is_deleted_sub(token):
            body = SubscriptionEngine.encode_body(next_deleted_sub_content())
            send_text(handler, 200, body, engine.normal_headers(
                body,
                routing=NEXT_HAPP_ROUTING_LINE,
                description=description,
                support_url=NEXT_SUPPORT_URL,
            ))
            return
        send_text(handler, 404, "404", {"Content-Type": "text/plain; charset=utf-8"})
        return

    username = user["name"]
    clients_by_key, hydra_clients_by_key = get_next_clients(username)
    plain = engine.generate_plain(
        username=username,
        clients_by_key=clients_by_key,
        hydra_clients_by_key=hydra_clients_by_key,
        custom_sub=user.get("custom_sub") or "",
        hysteria_enabled=False,
        wl_enabled=wl_get_status(username),
        wl_links=read_wl_links(),
        description=description,
        support_url=NEXT_SUPPORT_URL,
        include_happ_metadata=(public_path != "subscribe-old"),
    )

    client_ip = handler.headers.get("X-Real-IP", handler.client_address[0])
    ua = handler.headers.get("User-Agent", "")
    if require_hwid:
        plain, device_id = engine.guard_ip_limit(
            token=token,
            client_ip=client_ip,
            user_agent=ua,
            content=plain,
            device_rows=get_known_devices(token),
            user_limit=get_next_user_limit(username),
        )
        if device_id and client_ip not in SERVER_IPS:
            remember_device(token, device_id, client_ip, ua)

    traffic = get_next_traffic(username)
    headers = engine.normal_headers(
        plain,
        routing=NEXT_HAPP_ROUTING_LINE,
        description=description,
        support_url=NEXT_SUPPORT_URL,
        upload=traffic["up"],
        download=traffic["down"],
        total=traffic["total"],
        expire=traffic["expire"],
    )
    if public_path == "subscribe-old":
        headers = engine.legacy_headers()
    if kind == "clash":
        send_text(handler, 200, engine.generate_clash_unsupported(), {**headers, "Content-Type": "text/yaml; charset=utf-8"})
        return
    if kind == "json":
        send_text(
            handler,
            200,
            engine.generate_json_profile(plain, ru_direct=(public_path in ("subscribe-next", "subscribe-old"))),
            {**headers, "Content-Type": "application/json; charset=utf-8"},
        )
        return
    if is_browser_subscription_request(handler):
        subscription_url = f"https://{DOMAIN}/{public_path}/{urllib.parse.quote(token, safe='')}"
        html_body = engine.browser_stub_html(
            logo_svg=read_subscription_stub_logo(),
            support_url=NEXT_SUPPORT_URL,
            subscription_url=subscription_url,
        )
        send_text(handler, 200, html_body, {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow",
        })
        return
    encoded = SubscriptionEngine.encode_body(plain)
    send_text(handler, 200, encoded, headers)


def is_browser_subscription_request(handler: BaseHTTPRequestHandler) -> bool:
    accept = (handler.headers.get("Accept") or "").lower()
    sec_dest = (handler.headers.get("Sec-Fetch-Dest") or "").lower()
    ua = (handler.headers.get("User-Agent") or "").lower()
    if "text/html" in accept:
        return True
    if sec_dest == "document":
        return True
    if "mozilla/" in ua and not any(client in ua for client in ("happ", "clash", "v2ray", "sing-box", "hiddify")):
        return True
    return False


def read_subscription_stub_logo() -> str:
    try:
        return SUB_STUB_LOGO.read_text(encoding="utf-8")
    except Exception:
        return ""


class SubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parts = self.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "subscribe-next":
            token = parts[1]
            kind = parts[2] if len(parts) >= 3 else "plain"
            if kind not in ("plain", "json", "clash"):
                self.send_response(404)
                self.end_headers()
                return
            handle_subscribe_next(self, token, kind)
            return

        if len(parts) >= 2 and parts[0] == "subscribe-old":
            token = parts[1]
            kind = parts[2] if len(parts) >= 3 else "plain"
            if kind != "plain":
                self.send_response(404)
                self.end_headers()
                return
            user = get_user_by_token(token)
            if not user or not legacy_sub_get(user["name"]):
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("404".encode())
                return
            handle_subscribe_next(self, token, kind, public_path="subscribe-old", require_hwid=False)
            return

        if len(parts) >= 2 and parts[0] == "subscribe":
            token = parts[1]
            kind = parts[2] if len(parts) >= 3 else "plain"
            if kind not in ("plain", "json", "clash"):
                self.send_response(404)
                self.end_headers()
                return
            handle_subscribe_next(self, token, kind, public_path="subscribe")
            return

        self.send_response(404)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body><h1>404</h1></body></html>")


def start_sub_server():
    server = HTTPServer(("127.0.0.1", SUB_PORT), SubHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"subscription server on 127.0.0.1:{SUB_PORT}", file=sys.stderr)


# === telegram api ===

class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"
        self.offset = 0

    def api(self, method: str, data: dict = None) -> dict:
        url = f"{self.base}/{method}"
        if data:
            payload = json.dumps(data).encode()
            req = urllib.request.Request(url, data=payload,
                                         headers={"Content-Type": "application/json"})
        else:
            req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception as e:
            print(f"tg api error: {e}", file=sys.stderr)
            return {}

    def get_updates(self) -> list:
        result = self.api("getUpdates", {
            "offset": self.offset,
            "timeout": 30,
            "allowed_updates": ["message", "callback_query"]
        })
        updates = result.get("result", [])
        if updates:
            self.offset = updates[-1]["update_id"] + 1
        return updates

    def send(self, chat_id: int, text: str, reply_markup: dict = None,
             parse_mode: str = "HTML") -> dict:
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self.api("sendMessage", data)

    def edit(self, chat_id: int, message_id: int, text: str,
             reply_markup: dict = None, parse_mode: str = "HTML") -> dict:
        data = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self.api("editMessageText", data)

    def answer_callback(self, callback_id: str, text: str = ""):
        self.api("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": text,
        })

    def download_file(self, file_id: str) -> bytes | None:
        """скачивает файл из telegram по file_id"""
        result = self.api("getFile", {"file_id": file_id})
        fp = result.get("result", {}).get("file_path")
        if not fp:
            return None
        url = f"https://api.telegram.org/file/bot{self.token}/{fp}"
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return resp.read()
        except Exception as e:
            print(f"download_file error: {e}", file=sys.stderr)
            return None


# === обработчики ===

# state для редактирования подписок
edit_state: dict = {}   # chat_id → {"user": name, "message_id": int}
edit_buffer: dict = {}  # chat_id → {"user": name, "parts": [...], "timer": Thread}
rename_state: dict = {}  # chat_id → {"user": name, "message_id": int}
addwl_state: dict = {}   # chat_id → {"stage": "waiting"} | {"entries": [{"vless", "parsed"}]}
addwl_buffer: dict = {}  # chat_id → {"parts": [str], "timer": Thread} — буфер для многочастного JSON
subdesc_state: dict = {}  # chat_id → {"stage": "waiting"}


def xui_rename_client(old_name: str, new_name: str):
    """переименовывает клиентов во всех инбаундах 3X-UI"""
    conn = sqlite3.connect(XUI_DB, timeout=30)
    all_inbounds = list(INBOUNDS.items()) + list(HYDRA_INBOUNDS.items())
    for key, ib in all_inbounds:
        row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (ib["id"],)).fetchone()
        if not row:
            continue
        s = json.loads(row[0])
        changed = False
        # email для этого инбаунда — единый паттерн через prefix (для INBOUNDS и HYDRA_INBOUNDS)
        prefix = ib.get("prefix", "")
        old_email, new_email = f"{prefix}{old_name}", f"{prefix}{new_name}"
        for c in s.get("clients", []):
            if c.get("email") == old_email:
                c["email"] = new_email
                changed = True
                break
        if changed:
            conn.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(s), ib["id"]))
            # переименовываем в client_traffics
            conn.execute("UPDATE client_traffics SET email=? WHERE email=? AND inbound_id=?",
                         (new_email, old_email, ib["id"]))
    conn.commit()
    conn.close()


def cmd_help(bot: TelegramBot, chat_id: int):
    bot.send(chat_id,
        "🌐 <b>vpn bot — goida.fun</b>\n\n"
        "/users — список пользователей\n"
        "/adduser &lt;name&gt; — добавить пользователя\n"
        "/devices [name] — устройства\n"
        "/subdesc — описание shadow-подписки\n"
        "/xray — статус Xray\n"
        "/ping — проверка бота"
    )


def cmd_subdesc(bot: TelegramBot, chat_id: int):
    desc = get_subscribe_next_description()
    text = (
        "📝 <b>описание /subscribe-next</b>\n\n"
        f"<blockquote expandable>{html.escape(desc)}</blockquote>\n\n"
        "отправьте новый текст следующим сообщением.\n"
        "чтобы сбросить дефолт — отправьте <code>-</code>."
    )
    subdesc_state[chat_id] = {"stage": "waiting"}
    bot.send(chat_id, text)


PAGE_SIZE = 5


def build_users_keyboard(users: list, page: int) -> tuple:
    """строит клавиатуру пользователей с пагинацией, возвращает (text, markup)"""
    total = len(users)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    chunk = users[start:start + PAGE_SIZE]

    # сводка
    hydra_count = sum(1 for u in users if hydra_get_status(u["name"]))
    legacy_count = sum(1 for u in users if legacy_sub_get(u["name"]))
    wl_count = sum(1 for u in users if wl_get_status(u["name"]))
    text = (
        f"👥 <b>пользователи</b> — {total} чел.\n🌍 hydra: {hydra_count}  old-sub: {legacy_count}  wl: {wl_count}"
    )

    buttons = []
    for u in chunk:
        buttons.append([{"text": u["name"], "callback_data": f"user:{u['name']}"}])

    # навигация
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append({"text": "⇽", "callback_data": f"userspage:{page - 1}"})
        nav.append({"text": f"{page + 1}/{total_pages}", "callback_data": "userspage:noop"})
        if page < total_pages - 1:
            nav.append({"text": "⇾", "callback_data": f"userspage:{page + 1}"})
        buttons.append(nav)

    return text, {"inline_keyboard": buttons}


def cmd_users(bot: TelegramBot, chat_id: int, page: int = 0):
    users = get_all_users()
    if not users:
        bot.send(chat_id, "пользователей нет. /adduser &lt;name&gt; для добавления")
        return
    text, markup = build_users_keyboard(users, page)
    bot.send(chat_id, text, markup)


def send_devices(bot: TelegramBot, chat_id: int, username: str, message_id: int | None = None):
    user = get_user(username)
    if not user:
        text = f"пользователь {html.escape(username)} не найден"
        if message_id:
            bot.edit(chat_id, message_id, text)
        else:
            bot.send(chat_id, text)
        return
    devices = get_user_devices(user["token"])
    if devices:
        body = "\n\n".join(format_device_line(d, i + 1) for i, d in enumerate(devices))
    else:
        body = "устройств пока нет — они появятся после обновления /subscribe-next"
    text = f"📱 <b>устройства {html.escape(username)}</b> — {len(devices)}\n\n{body}"
    markup = {"inline_keyboard": [[
        {"text": "сбросить устройства", "callback_data": f"confirm_resetip:{username}", "style": "danger"},
        {"text": "← карточка", "callback_data": f"user:{username}"},
    ]]}
    if message_id:
        bot.edit(chat_id, message_id, text, markup)
    else:
        bot.send(chat_id, text, markup)


def cmd_devices(bot: TelegramBot, chat_id: int, arg: str):
    if arg:
        send_devices(bot, chat_id, arg)
        return
    users = get_all_users()
    rows = []
    for user in users:
        devices = get_user_devices(user["token"])
        if devices:
            last_seen = devices[0]["last_seen"]
            rows.append((user["name"], len(devices), last_seen))
    if not rows:
        bot.send(chat_id, "📱 устройств пока нет")
        return
    lines = [
        f"<b>{html.escape(name)}</b>: {count} · last <i>{fmt_dt_short(last_seen)}</i>"
        for name, count, last_seen in rows
    ]
    bot.send(chat_id, "📱 <b>устройства по пользователям</b>\n\n" + "\n".join(lines))


def cmd_adduser(bot: TelegramBot, chat_id: int, name: str):
    if not name:
        bot.send(chat_id, "использование: /adduser &lt;name&gt;")
        return

    # проверяем нет ли уже
    if get_user(name):
        bot.send(chat_id, f"пользователь <b>{name}</b> уже существует")
        return

    bot.send(chat_id, f"⏳ создаю пользователя <b>{name}</b>...")

    try:
        # генерируем uuid для каждого инбаунда (свой uuid на инбаунд)
        uuids = {key: str(uuid.uuid4()) for key in INBOUNDS}

        # добавляем клиентов в 3X-UI
        for key, ib in INBOUNDS.items():
            # smart-pro — только для bozhenkas (как в generate_subscription)
            if key == "smart-pro" and name != "bozhenkas":
                continue
            # email = prefix + name (smart/smart-pro имеют prefix='')
            prefix = ib.get("prefix", "")
            email = f"{prefix}{name}"

            xui_add_client(ib["id"], email, uuids[key])

        # рестарт xray для применения
        xui_restart()
        time.sleep(1)

        # добавляем в бот-бд
        token = add_user_db(name)

        # генерируем и сохраняем подписку
        save_subscription(name)

        sub_url = f"https://{DOMAIN}/subscribe/{token}"

        bot.send(chat_id,
            f"✅ пользователь <b>{name}</b> создан\n\n"
            f"📎 подписка:\n<code>{sub_url}</code>\n\n"
            f"клиенты добавлены во все инбаунды, xray перезапущен.",
            {"inline_keyboard": [[
                {"text": "👥 перейти к пользователям", "callback_data": "back:users"}
            ]]}
        )

    except Exception as e:
        bot.send(chat_id, f"❌ ошибка: {e}")


def show_user_info(bot: TelegramBot, chat_id: int, message_id: int, username: str):
    user = get_user(username)
    if not user:
        bot.edit(chat_id, message_id, f"пользователь {username} не найден")
        return

    # трафик
    traffic_parts = []
    total_up = 0
    total_down = 0

    for key, ib in INBOUNDS.items():
        if key == "smart-pro" and username != "bozhenkas":
            continue
        prefix = ib.get("prefix", "")
        email = f"{prefix}{username}"

        tr = xui_get_traffic(email)
        up = tr["total_up"]
        down = tr["total_down"]
        total_up += up
        total_down += down

        if up > 0 or down > 0:
            flag = {"fi": "🇫🇮", "se": "🇸🇪", "smart": "🔀", "smart-pro": "⚡", "zapret": "🇷🇺"}.get(key, "")
            traffic_parts.append(f"  {flag} {key}: ↑{format_bytes(up)} ↓{format_bytes(down)}")

    sub_url = f"https://{DOMAIN}/subscribe/{user['token']}"
    created = user["created_at"][:10]

    text = (
        f"👤 <b>{username}</b>\n\n"
        f"📅 создан: {created}\n"
        f"📊 трафик: ↑{format_bytes(total_up)} ↓{format_bytes(total_down)}\n"
    )
    if traffic_parts:
        text += "\n".join(traffic_parts) + "\n"

    # статус и трафик hydra
    hydra_on_display = hydra_get_status(username)
    if hydra_on_display:
        hydra_traffic_parts = []
        conn_xui_tr = sqlite3.connect(XUI_DB, timeout=30)
        for key, ib in HYDRA_INBOUNDS.items():
            email = f"{ib['prefix']}{username}"
            row = conn_xui_tr.execute(
                "SELECT up, down FROM client_traffics WHERE email=? AND inbound_id=?",
                (email, ib["id"])
            ).fetchone()
            if row and (row[0] > 0 or row[1] > 0):
                hydra_traffic_parts.append(f"  {ib['flag']} {key}: ↑{format_bytes(row[0])} ↓{format_bytes(row[1])}")
        conn_xui_tr.close()
        hydra_status = "🌍 hydra: вкл"
        text += f"\n{hydra_status}"
        if hydra_traffic_parts:
            text += "\n<blockquote expandable>" + "\n".join(hydra_traffic_parts) + "</blockquote>"

    text += f"\n📎 подписка:\n<code>{sub_url}</code>"
    if legacy_sub_get(username):
        old_url = f"https://{DOMAIN}/subscribe-old/{user['token']}"
        text += f"\n\n🧩 v2ray-old:\n<code>{old_url}</code>"

    # ip и лимит
    conn_ips = sqlite3.connect(BOT_DB, timeout=30)
    ip_rows = conn_ips.execute(
        "SELECT ip, last_seen, user_agent FROM user_ips WHERE token=? ORDER BY last_seen DESC",
        (user["token"],)
    ).fetchall()
    conn_ips.close()
    ip_count = len(ip_rows)

    # лимит у пользователя в 3X-UI (смотрим по smart-инбаунду)
    conn_xui = sqlite3.connect(XUI_DB, timeout=30)
    xui_row = conn_xui.execute(
        "SELECT settings FROM inbounds WHERE id=?", (INBOUNDS["smart"]["id"],)
    ).fetchone()
    conn_xui.close()
    current_limit = 4
    if xui_row:
        for c in json.loads(xui_row[0]).get("clients", []):
            if c.get("email") == username:
                current_limit = c.get("limitIp", 4)
                break

    devices = get_user_devices(user["token"])
    if devices:
        device_lines = "\n\n".join(format_device_line(d, i + 1) for i, d in enumerate(devices[:8]))
        text += f"\n\n📱 устройств: {len(devices)}\n<blockquote expandable>{device_lines}</blockquote>"
    elif ip_count > 0:
        ip_lines = "\n".join(
            f"{row[0]}  <i>{row[1][:16]}</i>  <code>{(row[2] or '')[:40]}</code>" for row in ip_rows
        )
        limit_str = "∞" if current_limit == 0 else str(current_limit)
        text += f"\n\n📱 устройств (IP): {ip_count}/{limit_str}\n<blockquote expandable>{ip_lines}</blockquote>"

    hydra_on = hydra_get_status(username)
    legacy_on = legacy_sub_get(username)
    limit_on = current_limit == 0  # 0 = лимит выкл

    def btn(text, cb, style=None):
        b = {"text": text, "callback_data": cb}
        if style:
            b["style"] = style
        return b

    buttons = [
        [
            btn("редактировать", f"edit:{username}"),
            btn("перевыпустить", f"confirm_regen:{username}"),
        ],
        [
            btn("сбросить IP", f"confirm_resetip:{username}"),
            btn("лимит: " + ("выкл" if limit_on else "вкл"), f"togglelimit:{username}", "primary" if limit_on else "success"),
        ],
        [
            btn("hydra: " + ("вкл" if hydra_on else "выкл"), f"togglehydra:{username}", "success" if hydra_on else None),
            btn("v2ray-old: " + ("вкл" if legacy_on else "выкл"), f"togglelegacy:{username}", "success" if legacy_on else None),
        ],
        [
            btn("wl: " + ("вкл" if wl_get_status(username) else "выкл"), f"togglewl:{username}", "success" if wl_get_status(username) else None),
        ],
        [
            btn("устройства", f"devices:{username}"),
        ],
        [
            btn("удалить", f"delete:{username}", "danger"),
        ],
        [
            btn("назад  ↜", "back:users"),
        ],
    ]

    bot.edit(chat_id, message_id, text, {"inline_keyboard": buttons})


def parse_vless_wl(url: str) -> dict | None:
    """парсит vless:// ссылку и возвращает поля для whitelist-сервера"""
    import re
    m = re.match(r"vless://([^@]+)@([^:]+):(\d+)\?([^#]*)(?:#(.*))?", url)
    if not m:
        return None
    uid, host, port, params_str, name = m.groups()
    p = dict(urllib.parse.parse_qsl(params_str))
    return {
        "uuid": uid, "host": host, "port": int(port),
        "sni": p.get("sni", ""), "pbk": p.get("pbk", ""),
        "sid": p.get("sid", ""), "flow": p.get("flow", ""),
        "fp": p.get("fp", "chrome"),
        "name": urllib.parse.unquote(name or ""),
    }


def wl_registry_add(vless_url: str):
    """добавляет vless строку в централизованный реестр WL"""
    os.makedirs(os.path.dirname(WL_REGISTRY_FILE), exist_ok=True)
    existing = []
    try:
        with open(WL_REGISTRY_FILE) as f:
            existing = [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        pass
    if vless_url not in existing:
        existing.append(vless_url)
        with open(WL_REGISTRY_FILE, "w") as f:
            f.write("\n".join(existing) + "\n")


def _wl_obj_to_vless(o: dict) -> str | None:
    """собирает vless:// из dict {host, port, uuid, sni, pbk, sid, flow?, fp?, name?}"""
    try:
        uid = o["uuid"]
        host = o["host"]
        port = int(o["port"])
        params = {
            "type": "tcp",
            "security": "reality",
            "encryption": "none",
            "pbk": o.get("pbk", ""),
            "sid": o.get("sid", ""),
            "sni": o.get("sni", ""),
            "fp": o.get("fp", "chrome"),
        }
        if o.get("flow"):
            params["flow"] = o["flow"]
        qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items() if v != "")
        name = urllib.parse.quote(o.get("name", "wl"))
        return f"vless://{uid}@{host}:{port}?{qs}#{name}"
    except (KeyError, ValueError, TypeError):
        return None


def parse_wl_blob(blob: str) -> list[dict]:
    """парсит blob → [{vless, parsed}]. Поддерживает JSON-массив строк/объектов либо построчный текст."""
    blob = (blob or "").strip()
    entries: list[dict] = []
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        data = None
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str) and item.strip().startswith("vless://"):
                v = item.strip()
                p = parse_vless_wl(v)
                if p:
                    entries.append({"vless": v, "parsed": p})
            elif isinstance(item, dict):
                v = _wl_obj_to_vless(item)
                if v:
                    p = parse_vless_wl(v)
                    if p:
                        entries.append({"vless": v, "parsed": p})
        return entries
    for line in blob.splitlines():
        line = line.strip()
        if line.startswith("vless://"):
            p = parse_vless_wl(line)
            if p:
                entries.append({"vless": line, "parsed": p})
    return entries


def send_wl_confirm(bot, chat_id, entries):
    """показывает подтверждение списка whitelist-серверов"""
    n = len(entries)
    preview = []
    for e in entries[:5]:
        p = e["parsed"]
        preview.append(f"• <code>{p['host']}:{p['port']}</code> sni=<code>{p['sni']}</code>")
    if n > 5:
        preview.append(f"<i>... и ещё {n-5}</i>")
    label = "сервер" if n == 1 else ("сервера" if 2 <= n <= 4 else "серверов")
    confirm = f"➕ <b>добавить {n} whitelist-{label}?</b>\n\n" + "\n".join(preview)
    buttons = [[
        {"text": f"добавить ({n})", "callback_data": "addwl_confirm", "style": "success"},
        {"text": "отмена  ↜", "callback_data": "addwl_cancel", "style": "danger"},
    ]]
    bot.send(chat_id, confirm, {"inline_keyboard": buttons})


def handle_callback(bot: TelegramBot, cb: dict):
    cb_id = cb["id"]
    data = cb.get("data", "")
    chat_id = cb["message"]["chat"]["id"]
    msg_id = cb["message"]["message_id"]
    user_id = cb["from"]["id"]

    if user_id != OWNER_ID:
        bot.answer_callback(cb_id, "⛔")
        return

    bot.answer_callback(cb_id)

    # любой callback сбрасывает активный edit-state/буфер (отмена ↜)
    # state выставляется заново в самой ветке "edit:" если нужно
    if chat_id in edit_buffer:
        buf = edit_buffer.pop(chat_id, None)
        if buf and buf.get("timer"):
            try:
                buf["timer"].cancel()
            except Exception:
                pass
    if chat_id in edit_state:
        edit_state.pop(chat_id, None)

    if data.startswith("user:"):
        username = data[5:]
        show_user_info(bot, chat_id, msg_id, username)

    elif data.startswith("devices:"):
        username = data[8:]
        send_devices(bot, chat_id, username, msg_id)

    elif data == "back:users":
        users = get_all_users()
        text, markup = build_users_keyboard(users, 0)
        bot.edit(chat_id, msg_id, text, markup)

    elif data.startswith("userspage:"):
        page_str = data[10:]
        if page_str == "noop":
            bot.answer_callback(cb_id, "ты на этой странице 🙃")
            return
        users = get_all_users()
        text, markup = build_users_keyboard(users, int(page_str))
        bot.edit(chat_id, msg_id, text, markup)

    elif data.startswith("edit:"):
        username = data[5:]
        u = get_user(username)
        if u:
            extra = (u.get("custom_sub") or "").strip()
            extra_view = extra if extra else "(пусто)"
            text = (
                f"✏️ редактирование <b>доп.ссылок</b> подписки <b>{username}</b>\n\n"
                f"эти строки дописываются <i>в конец</i> базовой подписки.\n"
                f"текущее содержимое:\n"
                f"<blockquote expandable><code>{extra_view}</code></blockquote>\n"
                f"отправьте новые доп.ссылки сообщением (одна на строку).\n"
                f"чтобы очистить — отправьте <code>-</code>."
            )
            buttons = [
                [{"text": "сменить имя профиля", "callback_data": f"rename:{username}"}],
                [{"text": "отмена  ↜", "callback_data": f"user:{username}"}],
            ]
            bot.edit(chat_id, msg_id, text, {"inline_keyboard": buttons})
            edit_state[chat_id] = {"user": username, "message_id": msg_id}
        else:
            bot.edit(chat_id, msg_id, f"подписка {username} не найдена")

    elif data.startswith("confirm_regen:"):
        username = data[14:]
        buttons = [
            [
                {"text": "перевыпустить", "callback_data": f"regen:{username}", "style": "danger"},
                {"text": "отмена  ↜", "callback_data": f"user:{username}", "style": "primary"},
            ]
        ]
        bot.edit(chat_id, msg_id, f"перевыпустить подписку <b>{username}</b>?", {"inline_keyboard": buttons})

    elif data.startswith("regen:"):
        username = data[6:]
        save_subscription(username)
        bot.answer_callback(cb_id, "✅ перевыпущено")
        show_user_info(bot, chat_id, msg_id, username)

    elif data.startswith("confirm_resetip:"):
        username = data[16:]
        buttons = [
            [
                {"text": "сбросить IP", "callback_data": f"resetip:{username}", "style": "danger"},
                {"text": "отмена  ↜", "callback_data": f"user:{username}", "style": "primary"},
            ]
        ]
        bot.edit(chat_id, msg_id, f"сбросить все IP для <b>{username}</b>?", {"inline_keyboard": buttons})

    elif data.startswith("resetip:"):
        username = data[8:]
        user = get_user(username)
        if user:
            conn_b = sqlite3.connect(BOT_DB, timeout=30)
            conn_b.execute("DELETE FROM user_ips WHERE token=?", (user["token"],))
            conn_b.execute("DELETE FROM user_devices WHERE token=?", (user["token"],))
            conn_b.commit()
            conn_b.close()
        bot.answer_callback(cb_id, "✅ IP сброшены")
        show_user_info(bot, chat_id, msg_id, username)

    elif data.startswith("rename:"):
        username = data[7:]
        bot.edit(chat_id, msg_id,
            f"введите новое имя для <b>{username}</b>:\n<i>латиница, без пробелов</i>",
            {"inline_keyboard": [[{"text": "отмена  ↜", "callback_data": f"user:{username}"}]]}
        )
        rename_state[chat_id] = {"user": username, "message_id": msg_id}

    elif data.startswith("togglewl:"):
        username = data[9:]
        user = get_user(username)
        if user:
            currently_on = wl_get_status(username)
            wl_set(username, not currently_on)
            save_subscription(username)
            status = "включён" if not currently_on else "выключен"
            bot.answer_callback(cb_id, f"whitelist {status}")
        show_user_info(bot, chat_id, msg_id, username)

    elif data.startswith("togglelegacy:"):
        username = data[13:]
        user = get_user(username)
        if user:
            currently_on = legacy_sub_get(username)
            legacy_sub_set(username, not currently_on)
            status = "включена" if not currently_on else "выключена"
            bot.answer_callback(cb_id, f"v2ray-old {status}")
        show_user_info(bot, chat_id, msg_id, username)

    elif data.startswith("togglehydra:"):
        username = data[12:]
        user = get_user(username)
        if user:
            currently_on = hydra_get_status(username)
            hydra_set(username, not currently_on)
            save_subscription(username)
            status = "включён" if not currently_on else "выключен"
            bot.answer_callback(cb_id, f"🌍 hydra {status}")
        show_user_info(bot, chat_id, msg_id, username)

    elif data == "domain:close":
        # Убираем кнопки, оставляем текст сообщения
        orig_text = cb["message"].get("text", "")
        bot.edit(chat_id, msg_id, orig_text)
        return

    elif data.startswith("domainrule:"):
        parts = data.split(":")
        domain = parts[1]
        rule = parts[2]
        set_domain_rule(domain, rule)
        current = get_domain_rule(domain)
        ip = get_domain_ip(domain)
        rule_labels = {'direct': 'direct (RU)', 'home': 'home (домашний IP)', 'foreign': 'foreign (FIN/SWE)', 'auto': 'auto (по geoip)'}
        msg_text = (
            f"🌐 <b>{domain}</b>\n"
            f"IP: <code>{ip}</code>\n"
            f"Правило: <b>{rule_labels.get(current, current)}</b>"
        )
        markup = build_domain_keyboard(domain, current)
        bot.edit(chat_id, msg_id, msg_text, markup)
        bot.answer_callback(cb_id, "✅ применено")
        return

    elif data.startswith("togglelimit:"):
        username = data[12:]
        user = get_user(username)
        if user:
            conn_xui = sqlite3.connect(XUI_DB, timeout=30)
            for ib in INBOUNDS.values():
                row = conn_xui.execute(
                    "SELECT settings FROM inbounds WHERE id=?", (ib["id"],)
                ).fetchone()
                if not row:
                    continue
                s = json.loads(row[0])
                changed = False
                for c in s.get("clients", []):
                    # email клиента зависит от инбаунда
                    email_map = {
                        INBOUNDS["fi"]["id"]: f"fin-{username}",
                        INBOUNDS["se"]["id"]: f"swe-{username}",
                        INBOUNDS["smart"]["id"]: username,
                    }
                    if c.get("email") == email_map.get(ib["id"]):
                        c["limitIp"] = 0 if c.get("limitIp", 4) != 0 else 4
                        changed = True
                if changed:
                    conn_xui.execute(
                        "UPDATE inbounds SET settings=? WHERE id=?",
                        (json.dumps(s), ib["id"])
                    )
            conn_xui.commit()
            conn_xui.close()
            xui_restart()
        bot.answer_callback(cb_id, "✅ лимит изменён")
        show_user_info(bot, chat_id, msg_id, username)

    elif data.startswith("delete:"):
        username = data[7:]
        buttons = [
            [
                {"text": "удалить", "callback_data": f"confirm_del:{username}", "style": "danger"},
                {"text": "отмена  ↜", "callback_data": f"user:{username}", "style": "primary"},
            ]
        ]
        bot.edit(chat_id, msg_id,
                 f"удалить пользователя <b>{username}</b>?\n"
                 f"клиенты будут удалены из всех инбаундов.",
                 {"inline_keyboard": buttons})

    elif data == "addwl_confirm":
        state = addwl_state.pop(chat_id, None)
        if not state or "entries" not in state:
            bot.edit(chat_id, msg_id, "❌ сессия истекла, повторите /addwl")
            return
        entries = state["entries"]
        existing = set()
        try:
            with open(WL_REGISTRY_FILE) as f:
                existing = {l.strip() for l in f if l.strip()}
        except FileNotFoundError:
            pass
        added = []
        for e in entries:
            v = e["vless"]
            if v not in existing:
                added.append(v)
                existing.add(v)
        skipped = len(entries) - len(added)
        if added:
            try:
                os.makedirs(os.path.dirname(WL_REGISTRY_FILE), exist_ok=True)
                with open(WL_REGISTRY_FILE, "a") as f:
                    for v in added:
                        f.write(v + "\n")
            except Exception as e:
                bot.edit(chat_id, msg_id, f"❌ ошибка записи: {e}")
                return
        summary = f"✅ добавлено: {len(added)}"
        if skipped:
            summary += f"\nдубли: {skipped}"
        bot.edit(chat_id, msg_id, summary)

    elif data == "addwl_cancel":
        addwl_state.pop(chat_id, None)
        bot.edit(chat_id, msg_id, "отменено")

    elif data.startswith("confirm_del:"):
        username = data[12:]
        try:
            # удаляем клиентов из 3X-UI
            for key, ib in INBOUNDS.items():
                if key == "smart-pro" and username != "bozhenkas":
                    continue
                prefix = ib.get("prefix", "")
                email = f"{prefix}{username}"
                xui_remove_client(ib["id"], email)

            xui_restart()

            # «надгробие»: токен живёт, отдаёт stub-инбаунд «пользователь удалён»
            user = get_user(username)
            if user:
                mark_deleted_sub(user["token"])
                sub_path = os.path.join(SUBS_DIR, user["token"])
                try:
                    with open(sub_path, "w") as f:
                        f.write(deleted_sub_content())
                except Exception:
                    pass

            # удаляем из бот-бд
            delete_user_db(username)

            # возвращаемся к списку с полноценной клавиатурой пагинации
            users = get_all_users()
            if users:
                text, markup = build_users_keyboard(users, 0)
                bot.edit(chat_id, msg_id,
                         f"✅ пользователь <b>{username}</b> удалён\n\n{text}", markup)
            else:
                bot.edit(chat_id, msg_id,
                         f"✅ пользователь <b>{username}</b> удалён\n\nпользователей больше нет.")

        except Exception as e:
            bot.edit(chat_id, msg_id, f"❌ ошибка удаления: {e}")


def cmd_xray(bot: TelegramBot, chat_id: int):
    """статус xray через /debug/vars"""
    try:
        lines = ["📡 <b>xray status</b>\n"]

        req = urllib.request.Request("http://127.0.0.1:11111/debug/vars")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())

        # observatory — основные ноды
        obs = data.get("observatory", {})
        for tag in ["proxy-fi", "proxy-se"]:
            name = {"proxy-fi": "🇫🇮 finland", "proxy-se": "🇸🇪 sweden"}.get(tag, tag)
            info = obs.get(tag, {})
            alive = "🟢" if info.get("alive") else "🔴"
            delay = info.get("delay", 0)
            lines.append(f"{alive} {name}: {delay}ms")

        # hydra ноды — проверяем через nc/connect
        import socket
        hydra_nodes = {
            "usa": ("77.110.109.52", 443, "🇺🇸"),
            "pol": ("188.255.163.44", 443, "🇵🇱"),
            "tur": ("try.north-1winter.cv", 443, "🇹🇷"),
            "nl":  ("nl.north-1winter.cv", 443, "🇳🇱"),
            "de":  ("138.124.51.103", 443, "🇩🇪"),
            "fiws": ("178.17.61.56", 443, "🇫🇮²"),
        }
        hydra_lines = []
        for key, (host, port, flag) in hydra_nodes.items():
            try:
                t0 = __import__("time").time()
                s = socket.create_connection((host, port), timeout=3)
                s.close()
                ms = int((__import__("time").time() - t0) * 1000)
                hydra_lines.append(f"🟢 {flag} {key}: {ms}ms")
            except Exception:
                hydra_lines.append(f"🔴 {flag} {key}: timeout")

        if hydra_lines:
            lines.append("")
            lines.append("<b>hydra:</b>")
            lines.extend(hydra_lines)

        # трафик по инбаундам
        stats = data.get("stats", {})
        inbound_stats = stats.get("inbound", {})
        lines.append("")
        conn = sqlite3.connect(XUI_DB, timeout=30)

        all_inbounds = list(INBOUNDS.items()) + list(HYDRA_INBOUNDS.items())
        flags = {
            "fi": "🇫🇮", "se": "🇸🇪", "smart": "🔀", "smart-pro": "⚡",
            "usa": "🇺🇸", "pol": "🇵🇱", "tur": "🇹🇷", "nl": "🇳🇱", "de": "🇩🇪", "fiws": "🇫🇮²"
        }
        for key, ib in all_inbounds:
            tag = ib["tag"]
            st = inbound_stats.get(tag, {})
            up = st.get("uplink", 0)
            down = st.get("downlink", 0)
            if up == 0 and down == 0:
                continue
            flag = flags.get(key, "")
            row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (ib["id"],)).fetchone()
            count = len(json.loads(row[0]).get("clients", [])) if row else 0
            lines.append(f"{flag} <b>{key}</b> ({count} кл.): ↑{format_bytes(up)} ↓{format_bytes(down)}")
        conn.close()

        # активные пользователи — группируем по базовому имени
        user_stats = stats.get("user", {})
        prefixes = ["fin-", "swe-", "usa-", "pol-", "tur-", "nl-", "de-", "fi2-"]

        def base_name(email):
            for p in prefixes:
                if email.startswith(p):
                    return email[len(p):]
            return email

        active_users = set(
            base_name(e) for e, s in user_stats.items()
            if s.get("downlink", 0) > 0 or s.get("uplink", 0) > 0
        )
        # берём общее число из bot.db
        bot_conn = sqlite3.connect(BOT_DB, timeout=30)
        total_users = bot_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        bot_conn.close()
        lines.append(f"\n👤 активных: {len(active_users)}/{total_users}")

        bot.send(chat_id, "\n".join(lines))

    except Exception as e:
        bot.send(chat_id, f"⚙️ <b>xray status</b>\n\n❌ ошибка: {e}")

def parse_domain(text: str) -> str | None:
    """Извлекает домен из URL или текста"""
    import re
    text = text.strip().lower()
    # убираем протокол
    text = re.sub(r'^https?://', '', text)
    # убираем путь, query, fragment
    text = re.split(r'[/?#]', text)[0]
    # убираем порт
    text = text.split(':')[0]
    # проверяем что похоже на домен
    if re.match(r'^[a-z0-9]([a-z0-9\-\.]*[a-z0-9])?$', text) and '.' in text:
        return text
    return None


def get_domain_rule(domain: str) -> str:
    """Возвращает текущее правило для домена: direct/home/foreign/auto"""
    import sqlite3, json
    db = sqlite3.connect(XUI_DB, timeout=30)
    row = db.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
    db.close()
    cfg = json.loads(row[0])
    for r in cfg['routing']['rules']:
        if 'inbound-10003' not in r.get('inboundTag', []):
            continue
        domains = r.get('domain', [])
        if f'domain:{domain}' in domains:
            tag = r.get('outboundTag') or r.get('balancerTag', '')
            if tag == 'home-mac-exit':
                return 'home'
            elif tag == 'balancer-smart':
                return 'foreign'
            elif 'direct' in tag:
                return 'direct'
    return 'auto'


def get_domain_ip(domain: str) -> str:
    """Резолвит IP домена"""
    import socket
    try:
        return socket.gethostbyname(domain)
    except Exception:
        return 'н/д'


def set_domain_rule(domain: str, rule: str):
    """Устанавливает правило для домена: direct/home/foreign"""
    import sqlite3, json, subprocess
    db = sqlite3.connect(XUI_DB, timeout=30)
    row = db.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
    cfg = json.loads(row[0])
    rules = cfg['routing']['rules']

    # Удаляем домен из всех правил inbound-10003
    for r in rules:
        if 'inbound-10003' not in r.get('inboundTag', []):
            continue
        domains = r.get('domain', [])
        key = f'domain:{domain}'
        if key in domains:
            domains.remove(key)

    # Определяем target
    if rule == 'home':
        target_tag = 'home-mac-exit'
        target_type = 'outboundTag'
    elif rule == 'foreign':
        target_tag = 'balancer-smart'
        target_type = 'balancerTag'
    else:  # direct
        target_tag = 'direct'
        target_type = 'outboundTag'

    # Добавляем в нужное правило
    added = False
    for r in rules:
        if 'inbound-10003' not in r.get('inboundTag', []):
            continue
        rt = r.get('outboundTag') or r.get('balancerTag', '')
        if rt == target_tag and 'geosite' not in str(r.get('domain', [])) and 'ext:' not in str(r.get('domain', [])):
            r.setdefault('domain', []).append(f'domain:{domain}')
            added = True
            break

    # Если подходящего правила нет — создаём новое
    if not added:
        new_rule = {
            "type": "field",
            "inboundTag": ["inbound-10003", "inbound-10005"],
            target_type: target_tag,
            "domain": [f"domain:{domain}"]
        }
        # Вставляем перед первым правилом с category-ru
        for i, r in enumerate(rules):
            if 'inbound-10003' in r.get('inboundTag', []) and 'geosite:category-ru' in str(r.get('domain', [])):
                rules.insert(i, new_rule)
                break

    db.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
               (json.dumps(cfg, ensure_ascii=False),))
    db.commit()
    db.close()

    with open('/usr/local/x-ui/bin/config.json', 'w') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    # Обновляем GitHub список если правило home
    if rule == 'home':
        update_github_vpn_block(domain, add=True)
    elif rule != 'home':
        update_github_vpn_block(domain, add=False)

    subprocess.run(['kill', '-SIGHUP', str(__import__('os').popen('pgrep xray-linux').read().strip())])


def update_github_vpn_block(domain: str, add: bool):
    """Добавляет или удаляет домен из GitHub репозитория"""
    import subprocess
    repo_dir = '/root/vpn-block-repo'
    domains_file = f'{repo_dir}/domains.lst'

    try:
        # Клонируем если нет
        if not __import__('os').path.exists(repo_dir):
            subprocess.run(['git', 'clone',
                'https://REMOVED_TOKEN@github.com/bozhenkas/russia-vpn-block-domains.git',
                repo_dir], capture_output=True)

        with open(domains_file) as f:
            lines = f.readlines()

        domains = [l.strip() for l in lines if l.strip() and not l.startswith('#')]

        if add and domain not in domains:
            # Добавляем перед первым комментарием следующей секции или в конец
            lines.append(domain + '\n')
        elif not add and domain in domains:
            lines = [l for l in lines if l.strip() != domain]

        with open(domains_file, 'w') as f:
            f.writelines(lines)

        subprocess.run(['git', '-C', repo_dir, 'add', 'domains.lst'], capture_output=True)
        action = 'add' if add else 'remove'
        subprocess.run(['git', '-C', repo_dir, 'commit', '-m', f'{action}: {domain}'],
                      capture_output=True)
        subprocess.run(['git', '-C', repo_dir, 'push'], capture_output=True)
    except Exception as e:
        print(f"github update error: {e}")


def build_domain_keyboard(domain: str, current_rule: str) -> dict:
    """Строит клавиатуру для управления правилом домена"""
    # для auto определяем фактическое правило для подсветки
    highlight = current_rule
    if current_rule == 'auto':
        highlight = 'direct'  # по умолчанию auto = direct (через geoip:ru)

    def btn(label, rule):
        b = {"text": label, "callback_data": f"domainrule:{domain}:{rule}"}
        if rule == highlight:
            b["style"] = "success"
        return b

    return {"inline_keyboard": [
        [btn('direct', 'direct'), btn('home', 'home'), btn('foreign', 'foreign')],
        [{"text": "закрыть", "callback_data": "domain:close", "style": "danger"}]
    ]}

def handle_message(bot: TelegramBot, msg: dict):
    text = (msg.get("text") or "").strip()
    document = msg.get("document")
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")

    if user_id != OWNER_ID:
        return

    if chat_id in subdesc_state:
        subdesc_state.pop(chat_id, None)
        if not text:
            bot.send(chat_id, "❌ нужен текст описания")
            return
        if text == "-":
            text = NEXT_DEFAULT_DESCRIPTION
        set_bot_setting(SUB_DESC_KEY, text)
        bot.send(chat_id, "✅ описание /subscribe-next обновлено")
        return

    # /addwl: document с JSON
    if chat_id in addwl_state and document:
        addwl_state.pop(chat_id, None)
        if document.get("file_size", 0) > 1_000_000:
            bot.send(chat_id, "❌ файл слишком большой (>1MB)")
            return
        data = bot.download_file(document["file_id"])
        if not data:
            bot.send(chat_id, "❌ не удалось скачать файл")
            return
        blob = data.decode("utf-8", errors="replace")
        entries = parse_wl_blob(blob)
        if not entries:
            bot.send(chat_id, "❌ не нашли vless:// записей в файле")
            return
        addwl_state[chat_id] = {"entries": entries}
        send_wl_confirm(bot, chat_id, entries)
        return

    # /addwl: ждём vless строку или JSON-текст (буфер 3с для многочастных)
    if chat_id in addwl_state and addwl_state[chat_id].get("stage") == "waiting":
        if not text:
            return
        import threading
        if chat_id in addwl_buffer:
            buf = addwl_buffer[chat_id]
            buf["timer"].cancel()
            buf["parts"].append(text)
        else:
            buf = {"parts": [text]}
            addwl_buffer[chat_id] = buf

        def flush(cid):
            buf = addwl_buffer.pop(cid, None)
            if not buf or cid not in addwl_state:
                return
            addwl_state.pop(cid, None)
            blob = "\n".join(buf["parts"])
            entries = parse_wl_blob(blob)
            if not entries:
                bot.send(cid, "❌ не нашли vless:// записей")
                return
            addwl_state[cid] = {"entries": entries}
            send_wl_confirm(bot, cid, entries)

        timer = threading.Timer(3.0, flush, args=[chat_id])
        buf["timer"] = timer
        timer.start()
        return

    # проверяем state переименования
    if chat_id in rename_state:
        state = rename_state.pop(chat_id)
        old_name = state["user"]
        new_name = text.strip()
        if not new_name.replace("-", "").replace("_", "").isalnum():
            bot.send(chat_id, "❌ имя должно быть латиницей без пробелов")
            rename_state[chat_id] = state
            return
        if get_user(new_name):
            bot.send(chat_id, f"❌ пользователь <b>{new_name}</b> уже существует")
            rename_state[chat_id] = state
            return
        # переименовываем в 3X-UI
        xui_rename_client(old_name, new_name)
        # переименовываем в bot.db
        conn_b = sqlite3.connect(BOT_DB, timeout=30)
        conn_b.execute("UPDATE users SET name=? WHERE name=?", (new_name, old_name))
        conn_b.commit()
        conn_b.close()
        # переименовываем файл подписки
        old_token = get_user(new_name)["token"] if get_user(new_name) else None
        # регенерируем подписку
        save_subscription(new_name)
        xui_restart()
        bot.send(chat_id, f"✅ переименован: <b>{old_name}</b> → <b>{new_name}</b>")
        return

    # проверяем state редактирования
    if chat_id in edit_state or chat_id in edit_buffer:
        import threading

        # если уже есть буфер — добавляем часть и сбрасываем таймер
        if chat_id in edit_buffer:
            buf = edit_buffer[chat_id]
            buf["timer"].cancel()
            buf["parts"].append(text)
        else:
            state = edit_state.pop(chat_id)
            buf = {"user": state["user"], "parts": [text]}
            edit_buffer[chat_id] = buf

        def flush(cid):
            buf = edit_buffer.pop(cid, None)
            if not buf:
                return
            username = buf["user"]
            full_text = "\n".join(buf["parts"]).strip()
            # "-" → очистить custom_sub
            if full_text == "-":
                full_text = ""
            set_custom_sub(username, full_text)
            # перегенерируем подписку с новыми custom_sub-доп.ссылками
            save_subscription(username)
            label = "очищены" if not full_text else "обновлены"
            bot.send(cid, f"✅ доп.ссылки <b>{username}</b> {label}", {
                "inline_keyboard": [[
                    {"text": "← карточка", "callback_data": f"user:{username}"},
                    {"text": "к списку  ↜", "callback_data": "back:users"},
                ]]
            })

        timer = threading.Timer(3.0, flush, args=[chat_id])
        buf["timer"] = timer
        timer.start()
        return

    # команды
    if text in ("/start", "/help"):
        cmd_help(bot, chat_id)

    elif text == "/users":
        cmd_users(bot, chat_id, 0)

    elif text.startswith("/devices"):
        parts = text.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        cmd_devices(bot, chat_id, arg)

    elif text == "/subdesc":
        cmd_subdesc(bot, chat_id)

    elif text.startswith("/adduser"):
        parts = text.split(maxsplit=1)
        name = parts[1].strip() if len(parts) > 1 else ""
        cmd_adduser(bot, chat_id, name)

    elif text == "/xray":
        cmd_xray(bot, chat_id)

    elif text == "/ping":
        bot.send(chat_id, "🏓 pong")

    elif text == "/addwl":
        addwl_state[chat_id] = {"stage": "waiting"}
        bot.send(chat_id,
            "➕ отправьте:\n"
            "• vless:// строку\n"
            "• JSON-файл (массив строк или объектов)\n"
            "• JSON-текст (если многочастный — склеится за 3с)"
        )

    elif text == "/restart":
        import subprocess
        bot.send(chat_id, "⏳ перезапускаю x-ui...")
        result = subprocess.run(["systemctl", "restart", "x-ui"], capture_output=True)
        if result.returncode == 0:
            bot.send(chat_id, "✅ x-ui перезапущен")
        else:
            bot.send(chat_id, f"❌ ошибка: {result.stderr.decode()}")

    else:
        # Обработка домена
        domain = parse_domain(text)
        if domain:
            current = get_domain_rule(domain)
            ip = get_domain_ip(domain)
            rule_labels = {'direct': 'direct (RU)', 'home': 'home (домашний IP)', 'foreign': 'foreign (FIN/SWE)', 'auto': 'auto (по geoip)'}
            msg_text = (
                f"🌐 <b>{domain}</b>\n"
                f"IP: <code>{ip}</code>\n"
                f"Правило: <b>{rule_labels.get(current, current)}</b>"
            )
            markup = build_domain_keyboard(domain, current)
            bot.send(chat_id, msg_text, markup)


# === main ===

def regenerate_all_subs():
    """перегенерирует файлы подписок для всех пользователей"""
    conn = sqlite3.connect(BOT_DB, timeout=30)
    rows = conn.execute("SELECT name FROM users ORDER BY created_at").fetchall()
    conn.close()
    count = 0
    for (name,) in rows:
        try:
            save_subscription(name)
            count += 1
        except Exception as e:
            print(f"[regen] {name}: {e}", file=sys.stderr)
    print(f"[regen] обновлено {count} подписок", file=sys.stderr)


def main():
    token = load_token()
    init_bot_db()
    # заполняем HYDRA_INBOUNDS из x-ui.db (динамически)
    global HYDRA_INBOUNDS
    HYDRA_INBOUNDS = _load_hydra_inbounds()
    print(f"hydra inbounds: {sorted(HYDRA_INBOUNDS.keys())}", file=sys.stderr)
    regenerate_all_subs()

    # запускаем HTTP-сервер подписок
    start_sub_server()

    # бот
    bot = TelegramBot(token)
    info = bot.api("getMe")
    if not info.get("ok"):
        print("не удалось подключиться к Telegram API", file=sys.stderr)
        sys.exit(1)

    username = info["result"]["username"]
    print(f"бот запущен: @{username}", file=sys.stderr)

    while True:
        try:
            updates = bot.get_updates()
            for upd in updates:
                if "callback_query" in upd:
                    handle_callback(bot, upd["callback_query"])
                elif "message" in upd and (upd["message"].get("text") or upd["message"].get("document")):
                    handle_message(bot, upd["message"])
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"ошибка: {e}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    main()
