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
import ssl
import time
import uuid
import base64
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from subscription import (
    DEFAULT_DESCRIPTION as NEXT_DEFAULT_DESCRIPTION,
    DEFAULT_SUPPORT_URL as NEXT_SUPPORT_URL,
    SubscriptionEngine,
    WL_JSON_PREFIX,
    deleted_sub_content as next_deleted_sub_content,
    limit_exceeded_content,
    unsupported_client_content,
    rename_remark,
    pick_line_by_remark,
    invite_temp_hour_content,
    invite_hour_expired_content,
    invite_expired_content,
    invite_banned_content,
    invite_trial_expired_content,
)
from manual_routes import (
    ensure_table as ensure_manual_routes_table,
    get_rule as get_manual_domain_rule,
    import_legacy_manual_rules,
    sync_remnawave as sync_remnawave_manual_routes,
    upsert_rule as upsert_manual_domain_rule,
)
from subscription.cluster_config import load_cluster_from_env as _load_cluster_cfg
from remnawave_client import (
    REMNA_BASE_SQUADS,
    REMNAWAVE_API_TOKEN_NAME,
    REMNAWAVE_API_URL,
    REMNAWAVE_DB_CONTAINER,
    REMNAWAVE_DEFAULT_DEVICE_LIMIT,
    REMNAWAVE_NEW_USER_EXPIRE,
    REMNAWAVE_PANEL_BASIC_AUTH,
    pg_quote,
    remnawave_active_hydra_squads,
    remnawave_api_request,
    remnawave_api_token,
    remnawave_create_user,
    remnawave_delete_user,
    remnawave_devices_by_username,
    remnawave_get_legacy_sub_token,
    remnawave_query,
    remnawave_restart_all_nodes,
    remnawave_set_device_limit,
    remnawave_user,
    remnawave_user_by_legacy_token,
    remnawave_user_by_short_uuid,
    remnawave_user_hydra_squads,
    remnawave_user_squads,
    remnawave_usernames,
    remnawave_vless_uuid,
)

_CLUSTER = _load_cluster_cfg()

# === конфиг ===

OWNER_ID = 294057781
XUI_DB = "/etc/x-ui/x-ui.db"
BOT_DB = "/root/vpn-bot/bot.db"
SUBS_DIR = "/root/vpn-bot/subscriptions"
DOMAIN = "ru.goida.fun"
# резервный вход: VPS ru-4 (194.117.80.*), P.15b — локальный xray TCP Reality :443 -> FIN (без RU).
# страховка на случай операторского блока подсети 83.147.255.* (Beeline и т.п.).
# трафик идёт под тем же vless_uuid → учитывается в основной подписке.
RESERVE_HOST = "reserve.goida.fun"   # A -> 31.77.169.26 (новый reserve VPS, HY2->FIN/SWE). по домену.
# P.15b: ru-4 принимает сохранённый Reality+gRPC профиль и идёт прямо в FIN.
RESERVE_REALITY_SNI = os.environ.get("RESERVE_REALITY_SNI", "web.max.ru")
RESERVE_REALITY_PBK = os.environ.get("RESERVE_REALITY_PBK", "Pm8yHbvRWJ-kEzVZswqDzafJTO5NDbE0_KNoduTqLWc")
RESERVE_REALITY_SID = os.environ.get("RESERVE_REALITY_SID", "7c6fc7670c6b3777")
RESERVE_UUID = os.environ.get("RESERVE_UUID", "2d08f735-d4d3-4ce5-ad0d-96de9d89fb13")  # shared uuid нового reserve VPS
# аварийно после миграции 2026-06-04: в подписке только резервный вход
SUBSCRIPTION_EMERGENCY_RESERVE_ONLY = False
# ── ИНЦИДЕНТ 2026-06-25: FIN/FRA/SWE недоступны у хостинг-провайдера ──
# Временно: убрать живые фикс-выходы (Финляндия/Франция/Швеция) и «Оптимальный Лайт»
# из подписки, повесить вместо них одну заглушку-плейсхолдер, а hydra сделать
# универсальной (все юзеры). «Оптимальный» балансит hydra (см. engine.py
# SMART_OVER_HYDRA). Откат: FOREIGN_EXITS_DOWN=0 в окружении vpn-bot + restart,
# плюс engine.py SMART_OVER_HYDRA=False. Полная инструкция:
# docs/incident-20260625-fin-fra-swe-provider-outage.md.
FOREIGN_EXITS_DOWN = os.environ.get("FOREIGN_EXITS_DOWN", "1").lower() in ("1", "true", "yes")
FOREIGN_UNAVAILABLE_REMARK = "🇭🇰 ⚠️🇫🇮🇸🇪🇫🇷 НА РЕМОНТЕ⚠️"
FRA_EXIT_DOWN = os.environ.get("FRA_EXIT_DOWN", "1").lower() in ("1", "true", "yes")
FRA_UNAVAILABLE_REMARK = "🇭🇰 ⚠️🇫🇷 НА РЕМОНТЕ⚠️"
FOREIGN_EXITS_DOWN_SETTING_KEY = "analyzer_foreign_exits_down"
FRA_EXIT_DOWN_SETTING_KEY = "analyzer_fra_exit_down"
IP_LIMIT = 4
DEVICE_LIMITS_TEMP_DISABLED = True
SERVER_IPS = _CLUSTER.server_ip_set() if _CLUSTER else {"45.91.54.152", "77.110.108.57", "89.22.230.5"}
SUB_PORT = 9090  # внутренний порт для подписок
NOTIFY_ALLOWED_IP = os.environ.get("NOTIFY_ALLOWED_IP", "78.107.88.21")
CLIENT_BOT_USERNAME = os.environ.get("CLIENT_BOT_USERNAME", "vpngoidabot")
CLIENT_BOT_WEB_PORT = os.environ.get("CLIENT_BOT_WEB_PORT", "9081")
SUB_DESC_KEY = "subscribe_next_description"
LEGACY_SUB_KEY_PREFIX = "legacy_sub:"
NATIVE_SUB_KEY_PREFIX = "native_sub:"
ADBLOCK_DNS_KEY_PREFIX = "adblock_dns:"  # per-user: жёсткий adblock-DNS (AdGuard Home на FIN)
SUB_STUB_LOGO = Path(__file__).with_name("goida.svg")

# DNS failover
CF_PRIMARY_IP = _CLUSTER.primary_cf_ip()  if _CLUSTER else "45.91.54.152"
CF_BACKUP_IP  = _CLUSTER.backup_cf_ip()   if _CLUSTER else "45.91.53.93"
# P.14 «Оптимальный Нео» (ex-«Оптимальный 2»): xhttp+reality на backup IP :7443
SMART2_PATH = "/smart2"
CF_DNS_DOMAIN = _CLUSTER.primary_domain() if _CLUSTER else "ru.goida.fun"
CF_API_BASE   = "https://api.cloudflare.com/client/v4"
XRAY_CONFIG_PATH = "/usr/local/x-ui/bin/config.json"

# rkn-checker
RKN_STATUS_KEY = "rkn_last_status"
EMERGENCY_INGRESS_MODE_KEY = "emergency_ingress_mode"

# sub-updater config
HYDRA_SUB_URL_KEY     = "hydra_sub_url"
HYDRA_SUB_UA_KEY      = "hydra_sub_ua"
HYDRA_SUB_URL_DEFAULT = "https://sub.whitestore.club/zPFyxgNrQGy2ekY7"
HYDRA_SUB_UA_DEFAULT  = "v2box_short"
SUB_UPDATER_CONFIG    = "/opt/sub-updater/config.env"

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
HYDRA_REMARK_TO_KEY = {
    "сша": "usa",
    "usa": "usa",
    "польша": "pol",
    "poland": "pol",
    "турция": "tur",
    "turkey": "tur",
    "türkiye": "tur",
    "нидерланды": "nl",
    "netherlands": "nl",
    "германия": "de",
    "germany": "de",
    "финляндия": "fiws",
    "finland": "fiws",
}
HYDRA_FLAG_TO_KEY = {
    "🇺🇸": "usa",
    "🇵🇱": "pol",
    "🇹🇷": "tur",
    "🇳🇱": "nl",
    "🇩🇪": "de",
    "🇫🇮": "fiws",
}
REMNA_HYDRA_REMARKS = {
    "HYDRA_USA_REMNA": ("usa", "США 🇺🇸 (сторонний)"),
    "HYDRA_POL_REMNA": ("pol", "Польша 🇵🇱 (сторонний)"),
    "HYDRA_TUR_REMNA": ("tur", "Турция 🇹🇷 (сторонний)"),
    "HYDRA_NL_REMNA": ("nl", "Нидерланды 🇳🇱 (сторонний)"),
    "HYDRA_DE_REMNA": ("de", "Германия 🇩🇪 (сторонний)"),
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
WL_CHECK_TIMEOUT = float(os.environ.get("WL_CHECK_TIMEOUT", "0.7"))
WL_CACHE_TTL = float(os.environ.get("WL_CACHE_TTL", "60"))
_wl_links_cache: dict = {"ts": 0.0, "links": []}
_wl_refresh_running = False
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
    "DirectSites": [],
    "DirectIp": [
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "169.254.0.0/16", "224.0.0.0/4", "255.255.255.255/32"
    ],
    "DomainStrategy": "IPIfNonMatch",
    "FakeDNS": "false"
}
HAPP_ROUTING_LINE = "happ://routing/onadd/" + _b64.b64encode(_json.dumps(_happ_profile, ensure_ascii=False).encode()).decode()


DOTENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _load_dotenv() -> None:
    """загружает .env в os.environ, не перезаписывает уже установленные переменные."""
    if not os.path.exists(DOTENV_PATH):
        return
    with open(DOTENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


_load_dotenv()

# после dotenv — иначе SMART2_* из .env не подхватятся
SMART2_HOST = os.environ.get("SMART2_HOST", CF_BACKUP_IP)
SMART2_PORT = int(os.environ.get("SMART2_PORT", "7443"))
SMART2_REALITY_SNI = os.environ.get("SMART2_REALITY_SNI", "ok.ru")
SMART2_REALITY_PBK = os.environ.get("SMART2_REALITY_PBK", "")
SMART2_REALITY_SID = os.environ.get("SMART2_REALITY_SID", "")
# временно убираем Neo (xhttp) из подписки — Happ iOS #5918
SMART2_IN_SUBSCRIPTION = os.environ.get("SMART2_IN_SUBSCRIPTION", "0").lower() in ("1", "true", "yes")
# P.16 «Оптимальный Лайт»: Hysteria2+salamander на backup IP
SMART_LITE_HOST = os.environ.get("SMART_LITE_HOST", CF_BACKUP_IP)
SMART_LITE_PORT = int(os.environ.get("SMART_LITE_PORT", "8443"))
SMART_LITE_SNI = os.environ.get("SMART_LITE_SNI", DOMAIN)
SMART_LITE_OBFS_PASSWORD = os.environ.get("SMART_LITE_OBFS_PASSWORD", "")


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
    ensure_manual_routes_table(conn)
    conn.commit()
    conn.close()
    if os.path.exists(XUI_DB):
        try:
            import_legacy_manual_rules(BOT_DB, XUI_DB)
        except Exception as exc:
            print(f"[manual-routes] legacy import failed: {exc}")
    conn = sqlite3.connect(BOT_DB, timeout=30)
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sub_requests_log (
            ts TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            client_type TEXT NOT NULL,
            hwid_present INTEGER NOT NULL,
            platform TEXT NOT NULL,
            app_version TEXT NOT NULL,
            ip_hash TEXT NOT NULL,
            response_type TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_requests_log_ts ON sub_requests_log(ts)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS client_profiles (
            username TEXT PRIMARY KEY,
            device_limit INTEGER NOT NULL DEFAULT 2,
            paid_until TEXT NOT NULL DEFAULT '',
            free_access INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            admin_tg_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT ''
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
    try:
        conn.execute("ALTER TABLE users ADD COLUMN paid_until TEXT NOT NULL DEFAULT ''")
        conn.commit()
    except Exception:
        pass  # колонка уже есть
    try:
        conn.execute("ALTER TABLE users ADD COLUMN hydra_enabled INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    except Exception:
        pass  # колонка уже есть
    conn.close()


def get_all_users() -> list[dict]:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    rows = conn.execute("SELECT name, token, created_at FROM users ORDER BY created_at").fetchall()
    conn.close()
    return [{"name": r[0], "token": r[1], "created_at": r[2]} for r in rows]


def user_hydra_enabled(username: str) -> bool:
    """источник правды для hydra на Remnawave: флаг в bot.db (команда /hydra)."""
    conn = sqlite3.connect(BOT_DB, timeout=30)
    try:
        row = conn.execute("SELECT hydra_enabled FROM users WHERE name=?", (username,)).fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return False
    conn.close()
    return bool(row and row[0])


def user_set_hydra_enabled(username: str, enabled: bool) -> None:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    try:
        conn.execute(
            "UPDATE users SET hydra_enabled=? WHERE name=?",
            (1 if enabled else 0, username),
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.close()


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
    for table in ("client_profiles", "client_server_prefs", "client_tg_links", "client_invite_tokens", "client_plan_requests"):
        try:
            conn.execute(f"DELETE FROM {table} WHERE username=?", (name,))
        except sqlite3.OperationalError:
            pass
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
    msg2 = urllib.parse.quote("обратитесь в @vpngoidabot")
    stub = "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443/?type=tcp&security=none"
    return "\n".join([
        "#profile-title: ❌ пользователь удалён",
        f"{stub}#{msg1}",
        f"{stub}#{msg2}",
    ])


def ensure_client_profile(conn: sqlite3.Connection, username: str) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS client_profiles (
            username TEXT PRIMARY KEY,
            device_limit INTEGER NOT NULL DEFAULT 2,
            paid_until TEXT NOT NULL DEFAULT '',
            free_access INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO client_profiles (username, device_limit, paid_until, updated_at) VALUES (?, 2, '', ?)",
        (username, datetime.now(timezone.utc).isoformat()),
    )
    return conn.execute("SELECT * FROM client_profiles WHERE username=?", (username,)).fetchone()


def log_admin_action(admin_tg_id: int, username: str, action: str, detail: str = "") -> None:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            admin_tg_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "INSERT INTO admin_log (created_at, admin_tg_id, username, action, detail) VALUES (?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), admin_tg_id, username, action, detail),
    )
    conn.commit()
    conn.close()


def get_admin_profile(username: str) -> dict:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    profile = ensure_client_profile(conn, username)
    paid_until = str(profile["paid_until"] or "")
    try:
        row = conn.execute("SELECT paid_until FROM users WHERE name=?", (username,)).fetchone()
        if row and row["paid_until"]:
            paid_until = row["paid_until"]
    except sqlite3.OperationalError:
        pass
    conn.close()
    return {
        "paid_until": paid_until,
        "device_limit": int(profile["device_limit"] or 2),
        "free_access": bool(profile["free_access"]),
    }


def set_user_paid_until(username: str, paid_until: str, admin_tg_id: int) -> None:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    ensure_client_profile(conn, username)
    try:
        conn.execute("UPDATE users SET paid_until=? WHERE name=?", (paid_until, username))
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE users ADD COLUMN paid_until TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE users SET paid_until=? WHERE name=?", (paid_until, username))
    conn.execute(
        "UPDATE client_profiles SET paid_until=?, updated_at=? WHERE username=?",
        (paid_until, datetime.now(timezone.utc).isoformat(), username),
    )
    conn.commit()
    conn.close()
    log_admin_action(admin_tg_id, username, "paid_until", paid_until)


def set_user_free_access(username: str, enabled: bool, admin_tg_id: int) -> None:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    ensure_client_profile(conn, username)
    conn.execute(
        "UPDATE client_profiles SET free_access=?, updated_at=? WHERE username=?",
        (1 if enabled else 0, datetime.now(timezone.utc).isoformat(), username),
    )
    conn.commit()
    conn.close()
    log_admin_action(admin_tg_id, username, "free_access", "on" if enabled else "off")


# === инвайты/рефералка ===

REFERRAL_REWARD_DAYS = 30


def log_invite_event(invite_id: int, event: str, meta: dict) -> None:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.execute(
        "INSERT INTO client_invite_events (invite_id, event, ts, meta) VALUES (?, ?, ?, ?)",
        (invite_id, event, datetime.now(timezone.utc).isoformat(), json.dumps(meta, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


def get_invite_for_username(username: str) -> dict | None:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM client_invites WHERE activated_username=? ORDER BY id DESC LIMIT 1",
        (username,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def can_invite(username: str) -> bool:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    profile = ensure_client_profile(conn, username)
    if bool(profile["free_access"]):
        conn.close()
        return True
    row = conn.execute(
        "SELECT 1 FROM admin_log WHERE username=? AND action='paid_until' LIMIT 1",
        (username,),
    ).fetchone()
    if row:
        conn.close()
        return True
    paid_until = str(profile["paid_until"] or "")
    if not paid_until:
        try:
            urow = conn.execute("SELECT paid_until FROM users WHERE name=?", (username,)).fetchone()
            if urow and urow["paid_until"]:
                paid_until = urow["paid_until"]
        except sqlite3.OperationalError:
            pass
    conn.close()
    return bool(paid_until)


def _parse_invite_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def decide_invite_mode(invite_row: dict, now: datetime) -> str:
    """чистая функция (без обращений к БД) — режим выдачи подписки для инвайт-юзера."""
    status = invite_row["status"]
    if status == "banned":
        return "banned"
    if status == "revoked":
        return "revoked"
    if status in ("approved", "paid"):
        return "normal"
    if status == "trial":
        trial_ends_at = invite_row.get("trial_ends_at") if isinstance(invite_row, dict) else invite_row["trial_ends_at"]
        if trial_ends_at and now > _parse_invite_dt(trial_ends_at):
            return "trial_expired"
        return "normal"
    if status in ("created", "opened", "tg_pending"):
        first_fetch = invite_row["first_fetch_at"]
        if not first_fetch:
            return "temp_hour"
        ff = _parse_invite_dt(first_fetch)
        if now < ff + timedelta(hours=1):
            return "temp_hour"
        if now < ff + timedelta(days=7):
            return "hour_expired"
        return "expired"
    return "normal"


def apply_invite_lazy_transition(conn: sqlite3.Connection, invite_row: dict, now: datetime) -> dict:
    """выполняет rowcount-проверенные status-переходы created→opened→tg_pending→expired.

    идемпотентна: повторный вызов с тем же invite_row/now не переоткрывает уже
    случившийся переход (guard в WHERE) и не шлёт события повторно.
    """
    invite_id = invite_row["id"]
    status = invite_row["status"]
    row = dict(invite_row)

    if status == "created":
        cur = conn.execute(
            "UPDATE client_invites SET first_fetch_at=?, status='opened' WHERE id=? AND status='created'",
            (now.isoformat(), invite_id),
        )
        conn.commit()
        if cur.rowcount:
            row["first_fetch_at"] = now.isoformat()
            row["status"] = "opened"
            try:
                log_invite_event(invite_id, "open", {})
            except Exception:
                pass
        else:
            fresh = conn.execute("SELECT * FROM client_invites WHERE id=?", (invite_id,)).fetchone()
            if fresh:
                row = dict(fresh)
        status = row["status"]

    if status == "opened":
        first_fetch = row["first_fetch_at"]
        if first_fetch:
            ff = _parse_invite_dt(first_fetch)
            if now >= ff + timedelta(hours=1):
                cur = conn.execute(
                    "UPDATE client_invites SET status='tg_pending' WHERE id=? AND status='opened'",
                    (invite_id,),
                )
                conn.commit()
                if cur.rowcount:
                    row["status"] = "tg_pending"
        status = row["status"]

    if status == "tg_pending":
        first_fetch = row["first_fetch_at"]
        if first_fetch:
            ff = _parse_invite_dt(first_fetch)
            if now >= ff + timedelta(days=7):
                cur = conn.execute(
                    "UPDATE client_invites SET status='expired' WHERE id=? AND status='tg_pending'",
                    (invite_id,),
                )
                conn.commit()
                if cur.rowcount:
                    row["status"] = "expired"
                    try:
                        log_invite_event(invite_id, "expire", {})
                    except Exception:
                        pass

    return row


INVITE_SMART_REMARK = "Оптимальный 🇸🇨"


def build_invite_mode_content(invite_row: dict, mode: str, username: str, client_ip: str, ua: str) -> str:
    invite_token = invite_row["token"]
    deep_link = f"https://t.me/{CLIENT_BOT_USERNAME}?start=link_{invite_token}"
    if mode == "temp_hour":
        real_line = pick_line_by_remark(remna_live_custom_sub(username).splitlines(), INVITE_SMART_REMARK)
        real_line = rename_remark(real_line, " [1 час]") if real_line else ""
        return invite_temp_hour_content(real_line or "", deep_link)
    if mode == "hour_expired":
        return invite_hour_expired_content(deep_link)
    if mode == "expired":
        return invite_expired_content()
    if mode == "banned":
        return invite_banned_content()
    if mode == "revoked":
        return invite_banned_content()
    if mode == "trial_expired":
        return invite_trial_expired_content()
    return ""


def reward_days(inviter_profile: dict, friend_profile: dict) -> int:
    """изолированная формула бонуса — владелец планирует пересмотреть привязку к тарифу друга."""
    return REFERRAL_REWARD_DAYS


def _notify_client_bot(tg_id: int, text: str) -> None:
    """best-effort нотификация через client-bot HTTP API — не должна ронять vpn-bot."""
    try:
        url = f"http://127.0.0.1:{CLIENT_BOT_WEB_PORT}/internal/notify"
        payload = json.dumps({"tgId": tg_id, "text": text}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.environ.get('NOTIFY_TOKEN', '')}",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5).close()
    except Exception as exc:
        print(f"[invites] notify client-bot failed: {exc}", file=sys.stderr)


def apply_referral_reward_if_due(friend_username: str, admin_tg_id: int) -> None:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM client_invites WHERE activated_username=? AND reward_applied_at='' "
        "AND status IN ('trial','approved','paid') ORDER BY id DESC LIMIT 1",
        (friend_username,),
    ).fetchone()
    conn.close()
    if not row:
        return

    invite = dict(row)
    inviter_username = invite["inviter_username"]
    inviter_profile = get_admin_profile(inviter_username)
    friend_profile = get_admin_profile(friend_username)
    reward = reward_days(inviter_profile, friend_profile)

    current = inviter_profile["paid_until"]
    base = datetime.utcnow().date()
    if current:
        try:
            base = max(base, datetime.strptime(current, "%Y-%m-%d").date())
        except ValueError:
            pass
    new_paid_until = (base + timedelta(days=reward)).isoformat()
    set_user_paid_until(inviter_username, new_paid_until, admin_tg_id)

    now_iso = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.execute(
        "UPDATE client_invites SET status='paid', reward_days=?, reward_applied_at=? WHERE id=?",
        (reward, now_iso, invite["id"]),
    )
    conn.commit()
    conn.close()

    try:
        log_invite_event(invite["id"], "reward", {"reward_days": reward, "new_paid_until": new_paid_until})
    except Exception:
        pass

    activated_tg_id = int(invite.get("activated_tg_id") or 0)
    if activated_tg_id:
        _notify_client_bot(activated_tg_id, f"твой друг оплатил, +{reward} дней")
    if _notify_bot:
        try:
            _notify_bot.send(
                OWNER_ID,
                f"🎁 начислен бонус <b>{inviter_username}</b>: +{reward} дней "
                f"(друг <b>{html.escape(friend_username)}</b> оплатил)\n"
                f"paid_until → <b>{new_paid_until}</b>",
            )
        except Exception:
            pass


def email_for_admin_limit(username: str, key: str, inbound: dict) -> str:
    if key in ("smart", "smart-pro"):
        return username
    if inbound.get("prefix"):
        return f"{inbound['prefix']}{username}"
    return username


def remnawave_username_by_token(token: str) -> str:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    try:
        row = conn.execute("SELECT name FROM users WHERE token=?", (token,)).fetchone()
    finally:
        conn.close()
    if not row:
        return ""
    username = row[0]
    return username if remnawave_user(username) else ""


def remnawave_set_hydra(username: str, enable: bool) -> bool:
    if not remnawave_user(username):
        return False
    user_set_hydra_enabled(username, enable)
    squads = remnawave_active_hydra_squads()
    if not squads:
        return False
    names_sql = ",".join(pg_quote(name) for name in squads)
    if enable:
        remnawave_query(
            "insert into internal_squad_members (internal_squad_uuid, user_id) "
            "select s.uuid, u.t_id from internal_squads s cross join users u "
            f"where u.username={pg_quote(username)} and s.name in ({names_sql}) "
            "on conflict do nothing;"
        )
    else:
        remnawave_query(
            "delete from internal_squad_members m using internal_squads s, users u "
            "where m.internal_squad_uuid=s.uuid and m.user_id=u.t_id "
            f"and u.username={pg_quote(username)} and s.name in ({names_sql});"
        )
    remnawave_refresh_custom_sub(username)
    remnawave_restart_all_nodes()
    return True


def remnawave_reserve_link(vless_uuid: str) -> str:
    # Старый формат должен продолжать работать без обновления подписки.
    encoded_remark = urllib.parse.quote("Резервный 🇰🇵 (мобильная связь)")
    return (
        f"vless://{RESERVE_UUID}@{RESERVE_HOST}:443/?type=grpc&security=reality&encryption=none"
        f"&serviceName=grpc&mode=gun"
        f"&sni={urllib.parse.quote(RESERVE_REALITY_SNI, safe='')}&fp=firefox"
        f"&pbk={urllib.parse.quote(RESERVE_REALITY_PBK, safe='')}"
        f"&sid={urllib.parse.quote(RESERVE_REALITY_SID, safe='')}"
        f"#{encoded_remark}"
    )


def remnawave_smart_lite_link(vless_uuid: str) -> str:
    """P.16 GOIDA_SMART_LITE: hysteria2+salamander на backup IP."""
    if not SMART_LITE_OBFS_PASSWORD:
        return ""
    encoded_remark = urllib.parse.quote("Оптимальный Лайт 🇸🇨")
    encoded_sni = urllib.parse.quote(SMART_LITE_SNI, safe="")
    encoded_obfs = urllib.parse.quote(SMART_LITE_OBFS_PASSWORD, safe="")
    return (
        f"hysteria2://{vless_uuid}@{SMART_LITE_HOST}:{SMART_LITE_PORT}/"
        f"?sni={encoded_sni}&obfs=salamander&obfs-password={encoded_obfs}"
        f"#{encoded_remark}"
    )


def remnawave_smart2_link(vless_uuid: str) -> str:
    """P.14 GOIDA_SMART2: vless+xhttp+reality на backup IP :7443, flow пустой."""
    if not SMART2_REALITY_PBK or not SMART2_REALITY_SID:
        return ""
    encoded_path = urllib.parse.quote(SMART2_PATH, safe="")
    encoded_remark = urllib.parse.quote("Оптимальный Нео 🇸🇨")
    encoded_sni = urllib.parse.quote(SMART2_REALITY_SNI, safe="")
    return (
        f"vless://{vless_uuid}@{SMART2_HOST}:{SMART2_PORT}/"
        f"?type=xhttp&security=reality&encryption=none"
        f"&sni={encoded_sni}&host={encoded_sni}"
        f"&fp=chrome&pbk={urllib.parse.quote(SMART2_REALITY_PBK, safe='')}"
        f"&sid={urllib.parse.quote(SMART2_REALITY_SID, safe='')}"
        f"&path={encoded_path}&mode=stream-one"
        f"#{encoded_remark}"
    )


def _is_broken_smart_lite_remark(remark: str) -> bool:
    text = urllib.parse.unquote(remark or "").strip().lower()
    return text.startswith("оптимальный лайт")


def _is_broken_smart_lite_line(line: str) -> bool:
    if "#" not in line:
        return False
    return _is_broken_smart_lite_remark(line.rsplit("#", 1)[1])


def strip_broken_smart_lite_from_subscription(body: str, allow_smart_lite: bool = False) -> str:
    """2026-06-26: Smart Lite broke; hide it even from Remnawave native subs."""
    if allow_smart_lite:
        return body
    stripped = body.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            filtered = [
                item for item in data
                if not _is_broken_smart_lite_remark(str((item or {}).get("remarks") or (item or {}).get("remark") or ""))
            ]
            return json.dumps(filtered, ensure_ascii=False)
        if isinstance(data, dict) and _is_broken_smart_lite_remark(str(data.get("remarks") or data.get("remark") or "")):
            return "[]"
        if data is not None:
            return body

    lines = body.splitlines()
    filtered = [line for line in lines if not _is_broken_smart_lite_line(line)]
    if len(filtered) != len(lines):
        return "\n".join(filtered)

    try:
        decoded = base64.b64decode(body + "==").decode("utf-8")
    except Exception:
        return body
    decoded_lines = decoded.splitlines()
    decoded_filtered = [line for line in decoded_lines if not _is_broken_smart_lite_line(line)]
    if len(decoded_filtered) == len(decoded_lines):
        return body
    return base64.b64encode("\n".join(decoded_filtered).encode()).decode()


def remnawave_subscription_links(username: str, vless_uuid: str, include_hydra: bool | None = None) -> str:
    foreign_exits_down = foreign_exits_down_get()
    fra_exit_down = fra_exit_down_get()
    if include_hydra is None:
        include_hydra = user_hydra_enabled(username)
    # инцидент 2026-06-25: hydra универсальна для всех (task6) — это живые выходы,
    # которыми «Оптимальный» заменяет мёртвые fin/fra/swe.
    if foreign_exits_down:
        include_hydra = True
    if SUBSCRIPTION_EMERGENCY_RESERVE_ONLY:
        return remnawave_reserve_link(vless_uuid)
    host = DOMAIN
    port = 443   # ingress на :443 (nginx обслуживает все пути) — устойчивее 7443
    # metadata (#profile-title/#profile-web-page-url) добавляет обёртка generate_plain/generate_subscription —
    # здесь только vless-ссылки, иначе title дублируется в итоговой подписке.
    base = []
    reserve_link = remnawave_reserve_link(vless_uuid)
    # Порядок фиксирован независимо от аварийного режима:
    # Оптимальный → Нео → Резервный → фикс-выходы. emergency больше НЕ
    # переставляет серверы (это путало юзеров — Лайт/Резервный всплывали наверх),
    # он влияет только на текст-баннер в get_subscribe_next_description().
    base.append(f"vless://{vless_uuid}@{host}:{port}/?type=ws&security=tls&encryption=none&sni={host}&host={host}&path=%2Fsmart#Оптимальный 🇸🇨")
    # 2026-06-26: «Оптимальный Лайт» broke, убран из подписки до отдельной починки.
    if SMART2_IN_SUBSCRIPTION:
        smart2 = remnawave_smart2_link(vless_uuid)
        if smart2:
            base.append(smart2)
    base.append(reserve_link)
    if foreign_exits_down:
        # task2: вместо живых Финляндия/Франция/Швеция — одна заглушка-плейсхолдер.
        # путь /unavailable не является WS-инбаундом → nginx 302 → клиент видит n/a,
        # что и сообщает «временно недоступны». Откат: FOREIGN_EXITS_DOWN=0.
        base.append(
            f"vless://{vless_uuid}@{host}:{port}/?type=ws&security=tls&encryption=none"
            f"&sni={host}&host={host}&path=%2Funavailable#{FOREIGN_UNAVAILABLE_REMARK}"
        )
    else:
        base.extend([
            f"vless://{vless_uuid}@{host}:{port}/?type=ws&security=tls&encryption=none&sni={host}&host={host}&path=%2Ffin#Финляндия 🇫🇮",
            f"vless://{vless_uuid}@{host}:{port}/?type=ws&security=tls&encryption=none&sni={host}&host={host}&path=%2Fswe#Швеция 🇸🇪",
        ])
        if fra_exit_down:
            base.append(
                f"vless://{vless_uuid}@{host}:{port}/?type=ws&security=tls&encryption=none"
                f"&sni={host}&host={host}&path=%2Ffrance-unavailable#{urllib.parse.quote(FRA_UNAVAILABLE_REMARK)}"
            )
        else:
            base.append(
                f"vless://{vless_uuid}@{host}:{port}/?type=ws&security=tls&encryption=none"
                f"&sni={host}&host={host}&path=%2Ffra#Франция 🇫🇷"
            )
    base.append(
        f"vless://{vless_uuid}@{host}:{port}/?type=ws&security=tls&encryption=none&sni={host}&host={host}&path=%2Fdirect#Русский (YouTube, Discord) 🇷🇺"
    )
    base = [line for line in base if line]
    if include_hydra:
        for squad in remnawave_user_hydra_squads(username):
            item = REMNA_HYDRA_REMARKS.get(squad)
            if not item:
                continue
            key, remark = item
            encoded_path = urllib.parse.quote(f"/hydra-{key}", safe="")
            encoded_remark = urllib.parse.quote(remark)
            base.append(
                f"vless://{vless_uuid}@{host}:{port}/?type=ws&security=tls&encryption=none"
                f"&sni={host}&host={host}&path={encoded_path}#{encoded_remark}"
            )
    return "\n".join(base)


def remna_live_custom_sub(username: str) -> str:
    """канонические remna-ссылки для подписки — только vpn-bot, не sync_remna_hydra."""
    vless_uuid = remnawave_vless_uuid(username)
    if not vless_uuid:
        return ""
    return remnawave_subscription_links(username, vless_uuid)


def custom_sub_is_legacy_poisoned(content: str, username: str = "") -> bool:
    """sync_remna_hydra и старые скрипты писали smart-{user} без reserve."""
    text = content or ""
    if "goida remnawave ws" in text:
        return True
    if username and f"smart-{username}" in text:
        return True
    if "reserve.goida.fun" not in text and "vless://" in text:
        return True
    return False


def remnawave_refresh_custom_sub(username: str) -> str:
    content = remna_live_custom_sub(username)
    if get_user(username) and content:
        set_custom_sub(username, content)
    return content


def create_client_invite_token(username: str) -> str:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS client_invite_tokens (
            username TEXT PRIMARY KEY,
            token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    row = conn.execute("SELECT token FROM client_invite_tokens WHERE username=?", (username,)).fetchone()
    if row:
        conn.close()
        return row[0]
    token = base64.urlsafe_b64encode(os.urandom(15)).decode().rstrip("=")
    conn.execute(
        "INSERT INTO client_invite_tokens (username, token, created_at) VALUES (?, ?, ?)",
        (username, token, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return token


def provision_invited_user(username: str, device_limit: int, expire_days: int) -> dict:
    """создаёт реального Remnawave+bot.db пользователя — общий путь для /adduser и инвайтов.

    возвращает {"token": <bot.db token>, "subUrl": ..., "inviteToken": <client-bot invite token>}
    """
    expire_at = (datetime.now(timezone.utc) + timedelta(days=int(expire_days))).strftime("%Y-%m-%d %H:%M:%S")
    remna = remnawave_create_user(username, device_limit=device_limit, expire_at=expire_at)
    vless_uuid = remna.get("vlessUuid") or remnawave_query(
        f"select vless_uuid from users where username={pg_quote(username)} limit 1;"
    ).strip()
    if not vless_uuid:
        raise RuntimeError("Remnawave user created, but vless_uuid is empty")

    token = add_user_db(username)
    set_custom_sub(username, remnawave_subscription_links(username, vless_uuid))
    profile_conn = sqlite3.connect(BOT_DB, timeout=30)
    ensure_client_profile(profile_conn, username)
    profile_conn.commit()
    profile_conn.close()
    invite_token = create_client_invite_token(username)
    sub_url = f"https://{DOMAIN}/subscribe/{token}"
    return {"token": token, "subUrl": sub_url, "inviteToken": invite_token}


def get_user_device_limit(username: str) -> int:
    remna = remnawave_user(username)
    if remna:
        return int(remna.get("deviceLimit") or 0)
    conn_xui = sqlite3.connect(XUI_DB, timeout=30)
    try:
        row = conn_xui.execute("SELECT settings FROM inbounds WHERE id=?", (INBOUNDS["smart"]["id"],)).fetchone()
        if row:
            for c in json.loads(row[0]).get("clients", []):
                if c.get("email") == username:
                    return int(c.get("limitIp", 4) or 0)
    finally:
        conn_xui.close()
    return 4


def set_user_device_limit(username: str, limit: int, admin_tg_id: int) -> None:
    limit = max(0, int(limit))
    remna_changed = remnawave_set_device_limit(username, limit)
    conn_xui = sqlite3.connect(XUI_DB, timeout=30)
    changed = False
    try:
        all_inbounds = list(INBOUNDS.items()) + list(HYDRA_INBOUNDS.items())
        for key, ib in all_inbounds:
            row = conn_xui.execute("SELECT settings FROM inbounds WHERE id=?", (ib["id"],)).fetchone()
            if not row:
                continue
            settings = json.loads(row[0])
            row_changed = False
            email = email_for_admin_limit(username, key, ib)
            for client in settings.get("clients", []):
                if client.get("email") == email:
                    client["limitIp"] = limit
                    row_changed = True
            if row_changed:
                conn_xui.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings), ib["id"]))
                changed = True
        if changed:
            conn_xui.commit()
    finally:
        conn_xui.close()
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    ensure_client_profile(conn, username)
    conn.execute(
        "UPDATE client_profiles SET device_limit=?, updated_at=? WHERE username=?",
        (limit, datetime.now(timezone.utc).isoformat(), username),
    )
    conn.commit()
    conn.close()
    if changed:
        xui_restart()
    if remna_changed:
        log_admin_action(admin_tg_id, username, "remnawave_device_limit", str(limit))
    log_admin_action(admin_tg_id, username, "device_limit", str(limit))


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


def native_sub_get(username: str) -> bool:
    return get_bot_setting(NATIVE_SUB_KEY_PREFIX + username, "0") == "1"




def adblock_dns_get(username: str) -> bool:
    return get_bot_setting(ADBLOCK_DNS_KEY_PREFIX + username, "0") == "1"




def emergency_ingress_get() -> bool:
    try:
        return get_bot_setting(EMERGENCY_INGRESS_MODE_KEY, "0") == "1"
    except sqlite3.Error:
        return False


def emergency_ingress_set(enable: bool) -> None:
    set_bot_setting(EMERGENCY_INGRESS_MODE_KEY, "1" if enable else "0")


def _bool_setting_get(key: str, default: bool) -> bool:
    try:
        raw = get_bot_setting(key, "")
    except sqlite3.Error:
        return default
    if raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _bool_setting_set(key: str, enable: bool) -> None:
    set_bot_setting(key, "1" if enable else "0")


def foreign_exits_down_get() -> bool:
    return _bool_setting_get(FOREIGN_EXITS_DOWN_SETTING_KEY, FOREIGN_EXITS_DOWN)


def foreign_exits_down_set(enable: bool) -> None:
    _bool_setting_set(FOREIGN_EXITS_DOWN_SETTING_KEY, enable)


def fra_exit_down_get() -> bool:
    return _bool_setting_get(FRA_EXIT_DOWN_SETTING_KEY, FRA_EXIT_DOWN)


def fra_exit_down_set(enable: bool) -> None:
    _bool_setting_set(FRA_EXIT_DOWN_SETTING_KEY, enable)


def get_subscribe_next_description() -> str:
    desc = get_bot_setting(SUB_DESC_KEY, NEXT_DEFAULT_DESCRIPTION)
    if emergency_ingress_get():
        notice = "аварийный режим входа: если WS-серверы n/a, подключайте «Резервный»."
        if notice not in desc:
            desc = f"{notice}\n{desc}" if desc else notice
    return desc


# === 3X-UI управление ===

def xui_get_inbound(inbound_id: int) -> dict | None:
    conn = sqlite3.connect(XUI_DB, timeout=30)
    row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return None




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
    conn.execute("DELETE FROM client_traffics WHERE inbound_id=? AND email=?", (inbound_id, email))
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


def write_sub_updater_config(url: str, ua: str):
    """сохраняет URL и UA в config.env для sub-updater и рестартит сервис"""
    os.makedirs(os.path.dirname(SUB_UPDATER_CONFIG), exist_ok=True)
    with open(SUB_UPDATER_CONFIG, "w") as f:
        f.write(f"SUB_URL={url}\n")
        f.write(f"SUB_UA={ua}\n")
    subprocess.run(["systemctl", "restart", "sub-updater"], capture_output=True, timeout=15)


# === Cloudflare DNS API ===

def _cf_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ.get('CF_TOKEN', '')}",
        "Content-Type": "application/json",
    }


def cf_get_dns_record() -> tuple[str, str]:
    """возвращает (current_ip, record_id) для CF_DNS_DOMAIN, или ('', '') при ошибке"""
    token = os.environ.get("CF_TOKEN", "")
    zone  = os.environ.get("CF_ZONE_ID", "")
    if not token or not zone:
        return "", ""
    url = f"{CF_API_BASE}/zones/{zone}/dns_records?type=A&name={CF_DNS_DOMAIN}"
    req = urllib.request.Request(url, headers=_cf_headers())
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        records = data.get("result", [])
        if records:
            return records[0]["content"], records[0]["id"]
    except Exception as e:
        print(f"cf_get_dns_record error: {e}", file=sys.stderr)
    return "", ""


def cf_set_dns_ip(record_id: str, new_ip: str) -> bool:
    """меняет A-запись CF_DNS_DOMAIN через Cloudflare API"""
    token = os.environ.get("CF_TOKEN", "")
    zone  = os.environ.get("CF_ZONE_ID", "")
    if not token or not zone:
        return False
    url  = f"{CF_API_BASE}/zones/{zone}/dns_records/{record_id}"
    body = json.dumps({"content": new_ip, "ttl": 60, "proxied": False}).encode()
    req  = urllib.request.Request(url, data=body, method="PATCH", headers=_cf_headers())
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("success", False)
    except Exception as e:
        print(f"cf_set_dns_ip error: {e}", file=sys.stderr)
    return False


# === hydra outbounds management (xrayTemplateConfig) ===

def get_hydra_outbound_status() -> list[tuple[str, bool]]:
    """возвращает [(tag, enabled)] по данным x-ui.db inbounds из HYDRA_META"""
    conn = sqlite3.connect(XUI_DB, timeout=30)
    result = []
    for port, meta in sorted(HYDRA_META.items()):
        row = conn.execute("SELECT enable FROM inbounds WHERE port=?", (port,)).fetchone()
        enabled = bool(row[0]) if row else False
        result.append((f"hydra-proxy-{meta['key']}", enabled))
    conn.close()
    return result


def set_hydra_outbound_enabled(tag: str, enabled: bool):
    """включает/выключает hydra inbound в x-ui.db (enable=0/1)"""
    key = tag.removeprefix("hydra-proxy-")
    port = next((p for p, m in HYDRA_META.items() if m["key"] == key), None)
    if port is None:
        return
    conn = sqlite3.connect(XUI_DB, timeout=30)
    conn.execute("UPDATE inbounds SET enable=? WHERE port=?", (1 if enabled else 0, port))
    conn.commit()
    conn.close()


def hydra_outbound_label(tag: str) -> str:
    key = tag.removeprefix("hydra-proxy-")
    for port, meta in HYDRA_META.items():
        if meta["key"] == key:
            country = HYDRA_COUNTRY_NAMES.get(key, key)
            return f"{meta['flag']} {country}"
    return tag


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
    remna_mode = bool(remnawave_user(username))
    lines = [f"#profile-title: goida {username}"]
    disabled_servers = get_client_disabled_servers(username)
    user_row = get_user(username)
    custom_sub = user_row.get("custom_sub") if user_row else ""
    if remna_mode:
        custom_sub = remnawave_refresh_custom_sub(username)
    legacy_disabled_servers = set(disabled_servers)
    if remna_mode or custom_sub_server_keys(custom_sub):
        legacy_disabled_servers.update({"smart", "smart2", "smart-lite", "fi", "se", "zapret", "direct", "smart-pro"})

    if not remna_mode:
        for key, ib in INBOUNDS.items():
            if key == "smart-pro" and username != "bozhenkas":
                continue
            if key in legacy_disabled_servers:
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
    hydra_links = [] if remna_mode else generate_hydra_links(username, disabled_servers)
    lines.extend(hydra_links)

    # добавляем whitelist серверы если включены
    if not remna_mode and wl_get_status(username):
        lines.extend(generate_wl_links())

    # дописываем доп.ссылки из custom_sub (только append, не подменяет)
    if custom_sub:
        custom_payload = filter_custom_sub_links(custom_sub, disabled_servers)
        if remna_mode:
            custom_payload = protocol_sub_links(custom_payload)
        for extra in custom_payload.split("\n"):
            extra = extra.strip()
            if extra:
                lines.append(extra)

    if remna_mode and wl_get_status(username):
        lines.extend(generate_wl_links())

    return "\n".join(lines)


def hydra_get_status(username: str) -> bool:
    """проверяет включён ли hydra для пользователя"""
    if remnawave_user(username):
        return user_hydra_enabled(username)
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
    if remnawave_user(username):
        remnawave_set_hydra(username, enable)
        return
    user_set_hydra_enabled(username, enable)
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
    return read_wl_links()


def normalize_wl_link(link: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(link)
        remark = urllib.parse.unquote(parsed.fragment or "").strip()
        match = re.match(r"whitelist\s*#?(\d+)", remark, re.I)
        if not match:
            return link
        clean_remark = urllib.parse.quote(f"Whitelist {match.group(1)} 🇷🇺")
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, clean_remark))
    except Exception:
        return link






def get_client_disabled_servers(username: str) -> set[str]:
    """читает выключенные пользователем серверы mini app. если таблицы нет — все включены."""
    try:
        conn = sqlite3.connect(BOT_DB, timeout=30)
        rows = conn.execute(
            "SELECT server_key FROM client_server_prefs WHERE username=? AND enabled=0",
            (username,),
        ).fetchall()
        conn.close()
        return {row[0] for row in rows}
    except Exception:
        return set()


_CUSTOM_SUB_PATH_KEYS = {
    "/smart": "smart",
    "/smart2": "smart2",
    "/fin": "fi",
    "/fi": "fi",
    "/fra": "fra",
    "/swe": "se",
    "/se": "se",
    "/direct": "zapret",
}


def custom_sub_server_key(line: str) -> str:
    """определяет server_key для remnawave/custom_sub ссылки.

    сначала по URL path (стабильно, не зависит от текста remark),
    затем fallback по remark/флагу (для legacy-ссылок и hydra).
    """
    if line.startswith(("hysteria2://", "hy2://")):
        try:
            remark = urllib.parse.unquote(urllib.parse.urlsplit(line).fragment or "").strip().lower()
        except Exception:
            remark = ""
        if "лайт" in remark or "lite" in remark:
            return "smart-lite"
        return ""
    if not line.startswith(("vless://", "vmess://", "trojan://", "ss://")):
        return ""
    try:
        split = urllib.parse.urlsplit(line)
    except Exception:
        return ""
    # 0) резерв определяем по адресу ru-4 (иначе path=%2Fsmart схлопнул бы его со smart-тумблером)
    if (split.hostname or "") in (RESERVE_HOST, "31.77.169.26", "194.117.80.94", "reserve.goida.fun"):
        return "reserve"
    # 1) по path из query (?path=%2Fsmart и т.п.)
    try:
        qs = urllib.parse.parse_qs(split.query)
        raw_path = (qs.get("path") or [""])[0]
        path = urllib.parse.unquote(raw_path).split("?")[0].rstrip("/").lower()
    except Exception:
        path = ""
    if path:
        if path in _CUSTOM_SUB_PATH_KEYS:
            return _CUSTOM_SUB_PATH_KEYS[path]
        hydra_path = re.match(r"/hydra-([a-z0-9]+)", path)
        if hydra_path:
            return f"hydra:{hydra_path.group(1)}"
    # 2) fallback по remark
    try:
        remark = urllib.parse.unquote(split.fragment or "").strip().lower()
    except Exception:
        return ""
    if remark.startswith("оптимальный лайт"):
        return "smart-lite"
    if remark.startswith("оптимальный 2") or remark.startswith("оптимальный нео"):
        return "smart2"
    if remark.startswith("smart-") or remark.startswith("оптимальный"):
        return "smart"
    if remark.startswith("fin-") or remark.startswith("финляндия"):
        return "fi"
    if remark.startswith("fra-") or remark.startswith("франция"):
        return "fra"
    if remark.startswith("swe-") or remark.startswith("швеция"):
        return "se"
    if remark.startswith("ru-zapret") or remark.startswith("русский"):
        return "zapret"
    hydra_match = re.match(r"hydra-([a-z0-9]+)-", remark)
    if hydra_match:
        return f"hydra:{hydra_match.group(1)}"
    flag_match = re.search(r"([\U0001F1E6-\U0001F1FF]{2})", remark)
    if flag_match and flag_match.group(1) in HYDRA_FLAG_TO_KEY:
        return f"hydra:{HYDRA_FLAG_TO_KEY[flag_match.group(1)]}"
    normalized = re.sub(r"\s+", " ", re.sub(r"[\U0001F1E6-\U0001F1FF]", "", remark)).strip()
    if normalized in HYDRA_REMARK_TO_KEY:
        return f"hydra:{HYDRA_REMARK_TO_KEY[normalized]}"
    return ""


def custom_sub_server_keys(custom_sub: str) -> set[str]:
    return {key for key in (custom_sub_server_key(line) for line in (custom_sub or "").splitlines()) if key}


def filter_custom_sub_links(custom_sub: str, disabled_servers: set[str]) -> str:
    """фильтрует remnawave/custom_sub ссылки теми же тумблерами mini app."""
    lines: list[str] = []
    for raw in (custom_sub or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        key = custom_sub_server_key(line)
        if key and key in disabled_servers:
            continue
        lines.append(line)
    return "\n".join(lines)


def protocol_sub_links(custom_sub: str) -> str:
    """оставляет только реальные proxy-ссылки, без metadata/comment строк."""
    prefixes = ("vless://", "vmess://", "trojan://", "ss://", "hysteria2://", "hy2://")
    return "\n".join(
        line.strip()
        for line in (custom_sub or "").splitlines()
        if line.strip().startswith(prefixes)
    )


def generate_hydra_links(username: str, disabled_servers: set[str] | None = None) -> list:
    """генерирует vless-ссылки для hydra если клиент включён"""
    links = []
    disabled_servers = disabled_servers or set()
    conn = sqlite3.connect(XUI_DB, timeout=30)
    for key, ib in HYDRA_INBOUNDS.items():
        if f"hydra:{key}" in disabled_servers:
            continue
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
    """проверяет включён ли hydra для пользователя"""
    if remnawave_user(username):
        return user_hydra_enabled(username)
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


def hydra_get_status_batch(username: str, remna_usernames: set[str]) -> bool:
    """то же, что hydra_get_status, но без docker exec на каждого юзера —
    remna_usernames получен одним запросом заранее (см. remnawave_usernames())."""
    if username in remna_usernames:
        return user_hydra_enabled(username)
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
    if remnawave_user(username):
        remnawave_set_hydra(username, enable)
        return
    user_set_hydra_enabled(username, enable)
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
        if len(parts) > 2 and parts[1].strip().lower() in {"ios", "android", "windows", "macos", "macos catalyst", "apple tv", "android tv"}:
            platform = parts[1].strip()
            app_version = parts[2].strip()
            if len(parts) > 3:
                device_name = parts[3].strip()
        else:
            if len(parts) > 1:
                app_version = parts[1].strip()
            if len(parts) > 2:
                platform = parts[2].strip()
            if len(parts) > 4:
                device_name = parts[4].strip()
            elif len(parts) > 3 and not re.fullmatch(r"[0-9]{8,}", parts[3].strip()):
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
    else:
        p = platform.lower()
        if p == "ios":
            platform = "iOS"
        elif p == "macos catalyst":
            platform = "macOS"
        elif p == "macos":
            platform = "macOS"
        elif p == "android":
            platform = "Android"
        elif p == "windows":
            platform = "Windows"

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

    known_devices = ["iPhone", "iPad", "Macintosh", "Windows", "Android"]
    if not device_name:
        for name in known_devices:
            if name.lower() in lower:
                device_name = name
                break
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



def _read_wl_links_no_probe() -> list[str]:
    return _collect_wl_links(probe=False)


def _refresh_wl_links_cache() -> list[str]:
    now = time.time()
    links = _collect_wl_links(probe=True)
    _wl_links_cache["ts"] = now
    _wl_links_cache["links"] = list(links)
    return links


def _schedule_wl_refresh() -> None:
    global _wl_refresh_running
    if _wl_refresh_running:
        return
    _wl_refresh_running = True

    def _run():
        global _wl_refresh_running
        try:
            _refresh_wl_links_cache()
        finally:
            _wl_refresh_running = False

    Thread(target=_run, daemon=True).start()


def read_wl_links(*, allow_stale: bool = False) -> list[str]:
    now = time.time()
    cached = list(_wl_links_cache.get("links") or [])
    if now - float(_wl_links_cache.get("ts") or 0) < WL_CACHE_TTL:
        return cached
    if allow_stale:
        if cached:
            _schedule_wl_refresh()
            return cached
        # cold cache на /subscribe — без tcp-probe, иначе Happ ловит 9s timeout
        links = _read_wl_links_no_probe()
        _wl_links_cache["links"] = list(links)
        _schedule_wl_refresh()
        return links
    return _refresh_wl_links_cache()


def normalize_wl_entry(line: str) -> str:
    if line.startswith(WL_JSON_PREFIX):
        return normalize_wl_json_line(line) or ""
    return normalize_wl_link(line)


def _wl_entry_servers(link: str) -> set[tuple[str, int]]:
    """Возвращает множество (host, port) из WL-записи для дедупликации по серверам."""
    if link.startswith(WL_JSON_PREFIX):
        cfg = decode_wl_json_line(link)
        return set(wl_json_vless_targets(cfg or {}))
    try:
        netloc = urllib.parse.urlsplit(link).netloc
        if "@" in netloc:
            netloc = netloc.split("@", 1)[1]
        h, p = netloc.rsplit(":", 1)
        return {(h.strip("[]"), int(p))}
    except Exception:
        return set()


def _get_sub_uid_from_wl_file() -> str:
    """Читает текущий sub_uid из первой записи WL_FILE (проставлен sync_remna_hydra)."""
    try:
        with open(WL_FILE) as f:
            for raw in f:
                line = raw.strip()
                if not line.startswith(WL_JSON_PREFIX):
                    continue
                cfg = decode_wl_json_line(line)
                if not cfg:
                    continue
                for o in cfg.get("outbounds", []) or []:
                    if o.get("protocol") != "vless":
                        continue
                    for vn in o.get("settings", {}).get("vnext", []) or []:
                        for u in vn.get("users", []) or []:
                            uid = u.get("id", "")
                            if uid and uid != "{uuid}":
                                return uid
    except (FileNotFoundError, Exception):
        pass
    return ""


def _mask_wl_entry_uuid(link: str) -> str:
    """Заменяет реальный UUID в WL-записи на {uuid} перед сохранением в реестр."""
    if link.startswith(WL_JSON_PREFIX):
        cfg = decode_wl_json_line(link)
        if not cfg:
            return link
        import copy as _copy
        cfg = _copy.deepcopy(cfg)
        for o in cfg.get("outbounds", []) or []:
            if o.get("protocol") != "vless":
                continue
            for vn in o.get("settings", {}).get("vnext", []) or []:
                for u in vn.get("users", []) or []:
                    u["id"] = "{uuid}"
        return encode_wl_json_line(cfg)
    return re.sub(
        r"(?i)^(vless://)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(@)",
        r"\1{uuid}\2",
        link,
    )


def _collect_wl_links(*, probe: bool) -> list[str]:
    """Объединяет WL_FILE и WL_REGISTRY_FILE с дедупликацией по серверам.

    WL_FILE: доверенный источник (проверен sync_remna_hydra), включается as-is.
    WL_REGISTRY_FILE: {uuid} заменяется актуальным sub_uid; серверы, уже покрытые
    WL_FILE, пропускаются; при probe=True мёртвые серверы не включаются.
    """
    sub_uid = _get_sub_uid_from_wl_file()
    known_servers: set[tuple[str, int]] = set()
    seen_links: set[str] = set()
    links: list[str] = []

    try:
        with open(WL_FILE) as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                link = normalize_wl_entry(line)
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                known_servers.update(_wl_entry_servers(link))
                links.append(link)
    except FileNotFoundError:
        pass

    try:
        with open(WL_REGISTRY_FILE) as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if sub_uid:
                    line = line.replace("{uuid}", sub_uid)
                link = normalize_wl_entry(line)
                if not link or link in seen_links:
                    continue
                servers = _wl_entry_servers(link)
                if servers & known_servers:
                    continue  # серверы уже покрыты записью из WL_FILE
                if probe and not wl_entry_alive(link):
                    continue
                seen_links.add(link)
                known_servers.update(servers)
                links.append(link)
    except FileNotFoundError:
        pass

    return renumber_wl_links(links)


def renumber_wl_links(links: list[str]) -> list[str]:
    """Переназывает все WL-записи по порядку: Whitelist 1🇷🇺, Whitelist 2🇷🇺..."""
    result = []
    for i, link in enumerate(links, 1):
        label = f"Whitelist {i} \U0001f1f7\U0001f1fa"
        if link.startswith(WL_JSON_PREFIX):
            cfg = decode_wl_json_line(link)
            if cfg:
                cfg["remarks"] = label
                link = encode_wl_json_line(cfg)
        else:
            try:
                parsed = urllib.parse.urlsplit(link)
                link = urllib.parse.urlunsplit((
                    parsed.scheme, parsed.netloc, parsed.path,
                    parsed.query, urllib.parse.quote(label),
                ))
            except Exception:
                pass
        result.append(link)
    return result


def normalize_wl_json_line(line: str) -> str | None:
    cfg = decode_wl_json_line(line)
    if not cfg:
        return None
    cfg["remarks"] = clean_wl_remark(str(cfg.get("remarks") or ""))
    return encode_wl_json_line(cfg)


def encode_wl_json_line(cfg: dict) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return WL_JSON_PREFIX + payload


def decode_wl_json_line(line: str) -> dict | None:
    if not line.startswith(WL_JSON_PREFIX):
        return None
    payload = line[len(WL_JSON_PREFIX):].strip()
    if not payload:
        return None
    try:
        padded = payload + "=" * (-len(payload) % 4)
        cfg = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        return None
    return cfg if validate_wl_json_config(cfg) else None


def validate_wl_json_config(cfg: object) -> bool:
    if not isinstance(cfg, dict):
        return False
    if not str(cfg.get("remarks", "")).strip():
        return False
    return bool(wl_json_vless_targets(cfg))


def clean_wl_remark(remark: str) -> str:
    return re.sub(r"\s*\[Расходует трафик\]\s*", "", remark or "", flags=re.I).strip()


def wl_json_vless_targets(cfg: dict) -> list[tuple[str, int]]:
    targets: list[tuple[str, int]] = []
    for outbound in cfg.get("outbounds", []) or []:
        if not isinstance(outbound, dict) or outbound.get("protocol") != "vless":
            continue
        for vnext in outbound.get("settings", {}).get("vnext", []) or []:
            host = str(vnext.get("address") or "").strip()
            try:
                port = int(vnext.get("port") or 443)
            except (TypeError, ValueError):
                continue
            if host and 0 < port <= 65535:
                targets.append((host, port))
    return targets


def wl_entry_alive(entry: str) -> bool:
    if entry.startswith(WL_JSON_PREFIX):
        cfg = decode_wl_json_line(entry)
        targets = wl_json_vless_targets(cfg or {})
        return bool(targets) and any(tcp_alive(host, port, WL_CHECK_TIMEOUT) for host, port in targets)
    parsed = parse_vless_wl(entry)
    if not parsed:
        return False
    return tcp_alive(parsed["host"], int(parsed["port"]), WL_CHECK_TIMEOUT)


def tcp_alive(host: str, port: int, timeout: float) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


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


def get_next_clients(username: str, disabled_servers: set[str] | None = None) -> tuple[dict, dict]:
    disabled_servers = disabled_servers if disabled_servers is not None else get_client_disabled_servers(username)
    clients_by_key = {}
    for key, ib in INBOUNDS.items():
        if key == "smart-pro" and username != "bozhenkas":
            continue
        if key in disabled_servers:
            continue
        client = xui_find_client(ib["id"], email_for_inbound(username, key, ib))
        if client:
            clients_by_key[key] = client

    hydra_clients_by_key = {}
    for key, ib in HYDRA_INBOUNDS.items():
        if f"hydra:{key}" in disabled_servers:
            continue
        client = xui_find_client(ib["id"], f"{ib['prefix']}{username}")
        if client:
            hydra_clients_by_key[key] = client
    return clients_by_key, hydra_clients_by_key


def get_next_traffic(username: str) -> dict:
    remna = remnawave_user(username)
    if remna:
        expire_raw = str(remna.get("expireAt") or "")
        expire = 0
        if expire_raw:
            try:
                expire = int(datetime.fromisoformat(expire_raw.replace("Z", "+00:00")).timestamp())
            except Exception:
                expire = 0
        return {
            "up": 0,
            "down": int(remna.get("usedTrafficBytes") or 0),
            "total": int(remna.get("trafficLimitBytes") or 0),
            "expire": expire,
        }

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
    if DEVICE_LIMITS_TEMP_DISABLED:
        return 0
    remna = remnawave_user(username)
    if remna:
        return int(remna.get("deviceLimit") or 0)
    conn = sqlite3.connect(BOT_DB, timeout=30)
    try:
        row = conn.execute(
            "SELECT device_limit FROM client_profiles WHERE username=?",
            (username,),
        ).fetchone()
        if row and int(row[0] or 0) > 0:
            return int(row[0])
    finally:
        conn.close()
    client = xui_find_client(INBOUNDS["smart"]["id"], username)
    if client:
        return int(client.get("limitIp", IP_LIMIT) or 0)
    return IP_LIMIT


def get_known_devices(token: str) -> set[str]:
    remna_username = remnawave_username_by_token(token)
    if remna_username:
        return {f"hwid:{item.get('hwid')}" for item in remnawave_devices_by_username(remna_username) if item.get("hwid")}
    conn = sqlite3.connect(BOT_DB, timeout=30)
    rows = conn.execute(
        "SELECT device_id FROM user_devices WHERE token=? AND device_id LIKE 'hwid:%'",
        (token,),
    ).fetchall()
    conn.close()
    return {row[0] for row in rows}


def remnawave_remember_device(
    username: str,
    device_id: str,
    user_agent: str,
    client_ip: str,
    *,
    user_limit: int = 0,
    platform_hint: str = "",
    platform_version_hint: str = "",
    device_model_hint: str = "",
) -> None:
    hwid = device_id.removeprefix("hwid:").strip()
    if not hwid:
        return

    # eviction: если новое устройство и лимит заполнен — выгоняем самое старое
    if user_limit > 0:
        devices = remnawave_devices_by_username(username)
        known = {d["hwid"] for d in devices if d.get("hwid")}
        if hwid not in known and len(known) >= user_limit:
            oldest = min(devices, key=lambda d: d.get("updatedAt") or d.get("createdAt") or "")
            oldest_hwid = (oldest.get("hwid") or "").replace("'", "''")
            safe_u = username.replace("'", "''")
            if oldest_hwid:
                remnawave_query(
                    f"delete from hwid_user_devices "
                    f"where hwid='{oldest_hwid}' "
                    f"and user_uuid=(select uuid from users where username='{safe_u}');"
                )

    meta = parse_device_metadata(device_id, client_ip, user_agent)
    if platform_hint:
        meta["platform"] = platform_hint[:60]
    if platform_version_hint:
        meta["platform_version"] = platform_version_hint[:60]
    if device_model_hint:
        meta["device_name"] = device_model_hint[:120]

    def sql_text(value: str) -> str:
        return "'" + (value or "").replace("'", "''")[:500] + "'"

    safe_username = username.replace("'", "''")
    remnawave_query(
        "insert into hwid_user_devices "
        "(hwid, user_uuid, platform, os_version, device_model, user_agent, created_at, updated_at) "
        "select "
        f"{sql_text(hwid)}, u.uuid, "
        f"{sql_text(meta.get('platform', ''))}, "
        f"{sql_text(meta.get('platform_version', ''))}, "
        f"{sql_text(meta.get('device_name', ''))}, "
        f"{sql_text(user_agent or '')}, "
        "now(), now() "
        "from users u "
        f"where u.username='{safe_username}' "
        "on conflict (hwid, user_uuid) do update set "
        "platform=coalesce(nullif(excluded.platform, ''), hwid_user_devices.platform), "
        "os_version=coalesce(nullif(excluded.os_version, ''), hwid_user_devices.os_version), "
        "device_model=coalesce(nullif(excluded.device_model, ''), hwid_user_devices.device_model), "
        "user_agent=coalesce(nullif(excluded.user_agent, ''), hwid_user_devices.user_agent), "
        "updated_at=now();"
    )


def remember_device(token: str, device_id: str, client_ip: str, user_agent: str, *, user_limit: int = 0):
    if not device_id:
        return
    remna_username = remnawave_username_by_token(token)
    if remna_username:
        remnawave_remember_device(remna_username, device_id, user_agent, client_ip, user_limit=user_limit)
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
    # eviction для legacy: удаляем самые старые устройства сверх лимита
    if user_limit > 0:
        conn2 = sqlite3.connect(BOT_DB, timeout=30)
        try:
            rows = conn2.execute(
                "SELECT device_id FROM user_devices WHERE token=? AND device_id LIKE 'hwid:%' ORDER BY last_seen ASC",
                (token,),
            ).fetchall()
            excess = len(rows) - user_limit
            if excess > 0:
                for (old_id,) in rows[:excess]:
                    conn2.execute("DELETE FROM user_devices WHERE token=? AND device_id=?", (token, old_id))
                conn2.commit()
        finally:
            conn2.close()


def send_text(handler: BaseHTTPRequestHandler, status: int, body: str, headers: dict[str, str]):
    payload = body.encode()
    handler.send_response(status)
    for key, value in headers.items():
        handler.send_header(key, value)
    if not any(key.lower() == "content-length" for key in headers):
        handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def remnawave_native_subscription(
    handler: BaseHTTPRequestHandler,
    remna_user: dict,
    token: str,
    kind: str,
    client_type: str,
    client_ip: str,
):
    # пре-вытеснение: если новый hwid + лимит достигнут → выкидываем старейшее устройство ДО запроса к Remnawave
    # hwid берём из x-hwid заголовка (тот же что увидит Remnawave), fallback → UA-extraction
    if not DEVICE_LIMITS_TEMP_DISABLED and client_ip not in SERVER_IPS:
        _ua = handler.headers.get("User-Agent", "")
        _hwid = (
            handler.headers.get("x-hwid")
            or handler.headers.get("X-Hwid")
            or SubscriptionEngine.extract_hwid(_ua)
        )
        _limit = int(remna_user.get("deviceLimit") or 0)
        _username = remna_user.get("username") or ""
        if _hwid and _limit > 0 and _username:
            _devices = remnawave_devices_by_username(_username)
            _known = {d["hwid"] for d in _devices if d.get("hwid")}
            print(f"[hwid-check] {_username}: hwid={_hwid[:8]}… known={[h[:8] for h in _known]} limit={_limit}", file=sys.stderr)
            if _hwid not in _known and len(_known) >= _limit:
                _oldest = min(_devices, key=lambda d: d.get("updatedAt") or d.get("createdAt") or "")
                _oldest_hwid = (_oldest.get("hwid") or "").replace("'", "''")
                _safe_u = _username.replace("'", "''")
                if _oldest_hwid:
                    print(f"[evict-remna] {_username}: вытесняем {_oldest_hwid[:8]}… (лимит {_limit})", file=sys.stderr)
                    remnawave_query(
                        f"delete from hwid_user_devices "
                        f"where hwid='{_oldest_hwid}' "
                        f"and user_uuid=(select uuid from users where username='{_safe_u}');"
                    )

    short_uuid = str(remna_user.get("shortUuid") or "")
    if not short_uuid:
        send_text(handler, 404, "404", {"Content-Type": "text/plain; charset=utf-8"})
        return
    suffix = {"plain": "", "json": "/json", "clash": "/clash"}.get(kind, "")
    url = REMNAWAVE_API_URL.rstrip("/") + f"/api/sub/{urllib.parse.quote(short_uuid, safe='')}{suffix}"
    headers = {
        "User-Agent": handler.headers.get("User-Agent", ""),
        "Accept": handler.headers.get("Accept", "*/*"),
        "X-Real-IP": client_ip,
        "X-Forwarded-For": client_ip,
    }
    for key in ("x-hwid", "x-device-os", "x-ver-os", "x-device-model"):
        value = handler.headers.get(key) or handler.headers.get(key.title())
        if value:
            headers[key] = value
    if not headers.get("x-hwid"):
        hwid = SubscriptionEngine.extract_hwid(headers.get("User-Agent", ""))
        if hwid:
            headers["x-hwid"] = hwid
    request = urllib.request.Request(url, headers=headers, method="GET")
    context = ssl._create_unverified_context() if url.startswith("https://127.") else None
    try:
        with urllib.request.urlopen(request, timeout=20, context=context) as resp:
            body = resp.read().decode(errors="replace")
            allow_smart_lite = (remna_user.get("username") or "") == "pentest"
            body = strip_broken_smart_lite_from_subscription(body, allow_smart_lite=allow_smart_lite)
            response_headers = {
                key: value for key, value in resp.headers.items()
                if key.lower() not in {"connection", "transfer-encoding", "content-encoding", "content-length"}
            }
            send_text(handler, resp.status, body, response_headers)
            response_type = "fake" if resp.headers.get("x-hwid-max-devices-reached") else "real"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        send_text(handler, exc.code, body, {"Content-Type": exc.headers.get("Content-Type", "text/plain; charset=utf-8")})
        response_type = "fake"
    except Exception as exc:
        print(f"[remnawave] native subscription failed: {exc}", file=sys.stderr)
        send_text(handler, 502, "remnawave subscription error", {"Content-Type": "text/plain; charset=utf-8"})
        response_type = "fake"
    log_sub_request(
        token=token,
        client_type=client_type,
        hwid_present=bool(headers.get("x-hwid")),
        platform=str(headers.get("x-device-os") or ""),
        app_version="",
        client_ip=client_ip,
        response_type=response_type,
    )
    username = remna_user.get("username") or ""
    hwid = headers.get("x-hwid") or ""
    if response_type == "real" and username and hwid and client_ip not in SERVER_IPS:
        remnawave_remember_device(
            username,
            f"hwid:{hwid}",
            headers.get("User-Agent", ""),
            client_ip,
            platform_hint=headers.get("x-device-os", ""),
            platform_version_hint=headers.get("x-ver-os", ""),
            device_model_hint=headers.get("x-device-model", ""),
        )


def sub_hash(value: str, first_bytes: int | None = None) -> str:
    data = (value or "").encode("utf-8")
    if first_bytes is not None:
        data = data[:first_bytes]
    return hashlib.sha256(data).hexdigest()


# Happ всегда JSON-routing (AGENTS.md + known-bugs §6), не plain happ://routing
HAPP_JSON_PUBLIC_PATHS = frozenset({"subscribe", "subscribe-next"})


def classify_subscription_client(
    *,
    user_agent: str,
    kind: str,
    public_path: str,
    browser_like: bool,
) -> str:
    ua = (user_agent or "").lower()
    if kind == "clash" or "clash" in ua:
        return "clash"
    if browser_like:
        return "browser"
    if public_path == "subscribe-old" or any(name in ua for name in (
        "v2ray", "sing-box", "hiddify", "nekoray", "streisand",
    )):
        return "legacy"
    if ua.startswith("happ/") or " happ/" in ua:
        return "happ"
    return "unknown"


def log_sub_request(
    *,
    token: str,
    client_type: str,
    hwid_present: bool,
    platform: str,
    app_version: str,
    client_ip: str,
    response_type: str,
):
    try:
        conn = sqlite3.connect(BOT_DB, timeout=30)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sub_requests_log (
                ts TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                client_type TEXT NOT NULL,
                hwid_present INTEGER NOT NULL,
                platform TEXT NOT NULL,
                app_version TEXT NOT NULL,
                ip_hash TEXT NOT NULL,
                response_type TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_requests_log_ts ON sub_requests_log(ts)")
        conn.execute(
            """
            INSERT INTO sub_requests_log (
                ts, token_hash, client_type, hwid_present, platform,
                app_version, ip_hash, response_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                sub_hash(token, first_bytes=8),
                client_type,
                1 if hwid_present else 0,
                (platform or "")[:60],
                (app_version or "")[:60],
                sub_hash(client_ip),
                response_type,
            ),
        )
        # держим только последние 5000 запросов
        conn.execute(
            """
            DELETE FROM sub_requests_log
            WHERE rowid NOT IN (
                SELECT rowid FROM sub_requests_log ORDER BY rowid DESC LIMIT 5000
            )
            """
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"sub log error: {e}", file=sys.stderr)


def get_sub_stats(days: int = 7, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sub_requests_log (
            ts TEXT NOT NULL,
            token_hash TEXT NOT NULL,
            client_type TEXT NOT NULL,
            hwid_present INTEGER NOT NULL,
            platform TEXT NOT NULL,
            app_version TEXT NOT NULL,
            ip_hash TEXT NOT NULL,
            response_type TEXT NOT NULL
        )
        """
    )
    total = conn.execute(
        "SELECT COUNT(*) FROM sub_requests_log WHERE ts>=?",
        (since,),
    ).fetchone()[0]
    clients = conn.execute(
        """
        SELECT client_type, COUNT(*) AS cnt
        FROM sub_requests_log
        WHERE ts>=?
        GROUP BY client_type
        ORDER BY cnt DESC, client_type
        LIMIT 5
        """,
        (since,),
    ).fetchall()
    responses = conn.execute(
        """
        SELECT response_type, COUNT(*) AS cnt
        FROM sub_requests_log
        WHERE ts>=?
        GROUP BY response_type
        """,
        (since,),
    ).fetchall()
    unique_tokens = conn.execute(
        "SELECT COUNT(DISTINCT token_hash) FROM sub_requests_log WHERE ts>=?",
        (since,),
    ).fetchone()[0]
    conn.close()
    return {
        "days": days,
        "total": total,
        "clients": clients,
        "responses": dict(responses),
        "unique_tokens": unique_tokens,
    }


def format_sub_stats(stats: dict) -> str:
    clients = stats.get("clients") or []
    top = "\n".join(
        f"{idx}. <code>{html.escape(client_type)}</code> — {count}"
        for idx, (client_type, count) in enumerate(clients, start=1)
    ) or "нет данных"
    responses = stats.get("responses") or {}
    return (
        f"📊 <b>subscription stats · {stats.get('days', 7)}d</b>\n\n"
        f"всего: <b>{stats.get('total', 0)}</b>\n"
        f"уникальных token_hash: <b>{stats.get('unique_tokens', 0)}</b>\n\n"
        f"<b>client_type top-5</b>\n{top}\n\n"
        "<b>responses</b>\n"
        f"real: <b>{responses.get('real', 0)}</b> · "
        f"fake: <b>{responses.get('fake', 0)}</b> · "
        f"html: <b>{responses.get('html', 0)}</b> · "
        f"legacy: <b>{responses.get('legacy', 0)}</b>"
    )


def get_user_devices(token: str) -> list[dict]:
    remna_username = remnawave_username_by_token(token)
    if remna_username:
        devices = []
        for item in remnawave_devices_by_username(remna_username):
            hwid = item.get("hwid") or ""
            devices.append({
                "device_id": f"hwid:{hwid}" if hwid and not hwid.startswith("hwid:") else hwid,
                "first_seen": item.get("createdAt") or "",
                "last_seen": item.get("updatedAt") or "",
                "client_ip": "",
                "user_agent": item.get("userAgent") or "",
                "app_name": "Happ",
                "app_version": "",
                "platform": item.get("platform") or "",
                "platform_version": item.get("osVersion") or "",
                "device_name": item.get("deviceModel") or "",
                "source": "remnawave",
            })
        return devices
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


def get_happ_manual_proxy_sites() -> list[str]:
    """ручные home/foreign правила должны дойти до серверного xray"""
    try:
        db = sqlite3.connect(XUI_DB, timeout=30)
        row = db.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
        db.close()
    except Exception as exc:
        print(f"[sub] manual proxy sites read failed: {exc}")
        return []
    if not row:
        return []
    try:
        cfg = json.loads(row[0])
    except Exception as exc:
        print(f"[sub] manual proxy sites parse failed: {exc}")
        return []

    sites: list[str] = []
    seen: set[str] = set()
    for rule in cfg.get("routing", {}).get("rules", []):
        if not str(rule.get("ruleTag", "")).startswith("manual-"):
            continue
        target = rule.get("outboundTag") or rule.get("balancerTag") or ""
        if target not in {"home-mac-exit", "balancer-smart"}:
            continue
        for domain in rule.get("domain", []) or []:
            if domain and domain not in seen:
                sites.append(domain)
                seen.add(domain)
    return sites


def handle_subscribe_next(
    handler: BaseHTTPRequestHandler,
    token: str,
    kind: str,
    public_path: str = "subscribe-next",
    require_hwid: bool = True,
):
    client_ip = handler.headers.get("X-Real-IP", handler.client_address[0])
    ua = handler.headers.get("User-Agent", "")
    browser_like = is_browser_subscription_request(handler)
    query_params = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    diagnostic_json_proxy = (query_params.get("diag") or query_params.get("debug") or [""])[0] in {"1", "true", "2ip", "proxy"}
    device_id_for_log = SubscriptionEngine.device_id(client_ip, ua)
    device_meta = parse_device_metadata(device_id_for_log, client_ip, ua)
    client_type = classify_subscription_client(
        user_agent=ua,
        kind=kind,
        public_path=public_path,
        browser_like=browser_like,
    )

    def finish(status: int, body: str, headers: dict[str, str], response_type: str):
        send_text(handler, status, body, headers)
        log_sub_request(
            token=token,
            client_type=client_type,
            hwid_present=bool(device_id_for_log),
            platform=device_meta.get("platform", ""),
            app_version=device_meta.get("app_version", ""),
            client_ip=client_ip,
            response_type=response_type,
        )

    user = get_user_by_token(token)
    engine = build_next_engine()
    description = get_subscribe_next_description()
    if not user:
        remna_legacy = remnawave_user_by_legacy_token(token)
        if not remna_legacy:
            # fallback: токен может быть shortUuid (для pure-RW пользователей из client-bot)
            remna_legacy = remnawave_user_by_short_uuid(token)
        if remna_legacy:
            # remnawave core, наша подписка — без нативного Remnawave proxy
            _username = remna_legacy.get("username") or ""
            _vless_uuid = remnawave_vless_uuid(_username) if _username else ""
            if not _vless_uuid:
                finish(404, "404", {"Content-Type": "text/plain; charset=utf-8"}, "fake")
                return
            custom_sub = remnawave_subscription_links(_username, _vless_uuid)
            plain = engine.generate_plain(
                username=_username,
                clients_by_key={},
                hydra_clients_by_key=None,
                custom_sub=custom_sub,
                wl_enabled=wl_get_status(_username),
                wl_links=read_wl_links(allow_stale=True),
                description=description,
                support_url=NEXT_SUPPORT_URL,
            )
            traffic = get_next_traffic(_username)
            hdrs = engine.normal_headers(
                plain,
                description=description,
                support_url=NEXT_SUPPORT_URL,
                upload=traffic["up"],
                download=traffic["down"],
                total=traffic["total"],
                expire=traffic["expire"],
                username=_username,
            )
            if browser_like:
                subscription_url = f"https://{DOMAIN}/{public_path}/{urllib.parse.quote(token, safe='')}"
                html_body = engine.browser_stub_html(
                    logo_svg=read_subscription_stub_logo(),
                    support_url=NEXT_SUPPORT_URL,
                    subscription_url=subscription_url,
                )
                finish(200, html_body, {
                    "Content-Type": "text/html; charset=utf-8",
                    "Cache-Control": "no-store",
                    "X-Robots-Tag": "noindex, nofollow",
                }, "html")
                return
            if kind == "clash":
                finish(200, engine.generate_clash_unsupported(), {**hdrs, "Content-Type": "text/yaml; charset=utf-8"}, "fake")
                return
            if kind == "json" or (kind == "plain" and client_type == "happ" and public_path in HAPP_JSON_PUBLIC_PATHS):
                json_body = engine.generate_json_profile(plain, ru_direct=True, diagnostic_proxy=diagnostic_json_proxy, adblock=adblock_dns_get(_username))
                finish(
                    200,
                    json_body,
                    {**hdrs, "Content-Type": "application/json; charset=utf-8", "Routing-Enable": "false", "routing": "happ://routing/off"},
                    "real",
                )
                return
            finish(200, SubscriptionEngine.encode_body(plain), hdrs, "real")
            return
        if is_deleted_sub(token):
            body = SubscriptionEngine.encode_body(next_deleted_sub_content())
            finish(200, body, engine.normal_headers(
                body,
                description=description,
                support_url=NEXT_SUPPORT_URL,
            ), "fake")
            return
        finish(404, "404", {"Content-Type": "text/plain; charset=utf-8"}, "fake")
        return

    username = user["name"]
    invite_row = get_invite_for_username(username)
    if invite_row:
        now = datetime.now(timezone.utc)
        if invite_row["status"] not in ("paid",):
            try:
                log_invite_event(invite_row["id"], "fetch", {"ip": client_ip, "ua": ua})
            except Exception:
                pass
        conn = sqlite3.connect(BOT_DB, timeout=30)
        conn.row_factory = sqlite3.Row
        invite_row = apply_invite_lazy_transition(conn, invite_row, now)
        conn.close()
        mode = decide_invite_mode(invite_row, now)
        if mode != "normal":
            content = build_invite_mode_content(invite_row, mode, username, client_ip, ua)
            body = SubscriptionEngine.encode_body(content)
            headers = engine.normal_headers(body, description=description, support_url=NEXT_SUPPORT_URL)
            if kind == "json" or (kind == "plain" and client_type == "happ" and public_path in HAPP_JSON_PUBLIC_PATHS):
                finish(
                    200,
                    engine.generate_json_profile(content, ru_direct=True),
                    {**headers, "Content-Type": "application/json; charset=utf-8", "Routing-Enable": "false", "routing": "happ://routing/off"},
                    "fake",
                )
                return
            if kind == "clash":
                finish(200, engine.generate_clash_unsupported(), {**headers, "Content-Type": "text/yaml; charset=utf-8"}, "fake")
                return
            finish(200, body, headers, "fake")
            return
    if native_sub_get(username):
        remna_user = remnawave_user(username)
        if not remna_user:
            finish(404, "404", {"Content-Type": "text/plain; charset=utf-8"}, "fake")
            return
        # Happ берёт JSON-профили: hysteria/Лайт не попадает в Remnawave base64 (XRAY_BASE64),
        # только в /json. Зеркалим legacy-логику HAPP_JSON_PUBLIC_PATHS.
        native_kind = kind
        if kind == "plain" and client_type == "happ" and public_path in HAPP_JSON_PUBLIC_PATHS:
            native_kind = "json"
        remnawave_native_subscription(
            handler=handler,
            remna_user=remna_user,
            token=token,
            kind=native_kind,
            client_type=client_type,
            client_ip=client_ip,
        )
        return

    # legacy path — generate_plain для всех пользователей
    disabled_servers = get_client_disabled_servers(username)
    remna_mode = bool(remnawave_user(username))
    if remna_mode:
        # всегда live из vpn-bot — DB мог быть перезаписан sync_remna_hydra legacy-форматом
        custom_sub = remna_live_custom_sub(username)
        stored = (user.get("custom_sub") or "").strip()
        if custom_sub and stored != custom_sub.strip():
            set_custom_sub(username, custom_sub)
    else:
        custom_sub = user.get("custom_sub") or ""
    legacy_disabled_servers = set(disabled_servers)
    has_custom_sub = bool(custom_sub_server_keys(custom_sub))
    if remna_mode or has_custom_sub:
        legacy_disabled_servers.update({"smart", "smart2", "smart-lite", "fi", "se", "zapret", "direct", "smart-pro"})
    if remna_mode:
        clients_by_key, hydra_clients_by_key = {}, {}
    else:
        clients_by_key, hydra_clients_by_key = get_next_clients(username, legacy_disabled_servers)
    if remna_mode or has_custom_sub:
        # remna-юзер: все серверы берём ТОЛЬКО из custom_sub (там верный порядок, hydra в конце).
        hydra_clients_by_key = {}
    wl_enabled = wl_get_status(username)
    custom_payload = filter_custom_sub_links(custom_sub, disabled_servers)
    plain = engine.generate_plain(
        username=username,
        clients_by_key=clients_by_key,
        hydra_clients_by_key=hydra_clients_by_key,
        custom_sub=custom_payload,
        hysteria_enabled=False,
        wl_enabled=wl_enabled,
        wl_links=read_wl_links(allow_stale=True) if wl_enabled else [],
        description=description,
        support_url=NEXT_SUPPORT_URL,
        include_happ_metadata=(public_path != "subscribe-old"),
    )
    real_plain = plain

    if require_hwid:
        _user_limit = get_next_user_limit(username)
        # x-hwid header имеет приоритет над UA-embedded hwid
        _xhwid = handler.headers.get("x-hwid") or handler.headers.get("X-Hwid") or ""
        _guard_ua = (f"hwid={_xhwid}" if _xhwid else ua)
        plain, device_id = engine.guard_ip_limit(
            token=token,
            client_ip=client_ip,
            user_agent=_guard_ua,
            content=plain,
            device_rows=get_known_devices(token),
            user_limit=_user_limit,
        )
        if device_id and client_ip not in SERVER_IPS:
            if remna_mode:
                remnawave_remember_device(
                    username,
                    device_id,
                    ua,
                    client_ip,
                    user_limit=_user_limit,
                    platform_hint=handler.headers.get("x-device-os", "") or handler.headers.get("X-Device-Os", ""),
                    platform_version_hint=handler.headers.get("x-ver-os", "") or handler.headers.get("X-Ver-Os", ""),
                    device_model_hint=handler.headers.get("x-device-model", "") or handler.headers.get("X-Device-Model", ""),
                )
            else:
                remember_device(token, device_id, client_ip, _guard_ua, user_limit=_user_limit)

    traffic = get_next_traffic(username)
    headers = engine.normal_headers(
        plain,
        description=description,
        support_url=NEXT_SUPPORT_URL,
        upload=traffic["up"],
        download=traffic["down"],
        total=traffic["total"],
        expire=traffic["expire"],
        username=username,
    )
    if public_path == "subscribe-old":
        headers = engine.legacy_headers()
    if kind == "clash":
        finish(200, engine.generate_clash_unsupported(), {**headers, "Content-Type": "text/yaml; charset=utf-8"}, "fake")
        return
    if kind == "json" or (kind == "plain" and client_type == "happ" and public_path in HAPP_JSON_PUBLIC_PATHS):
        finish(
            200,
            engine.generate_json_profile(plain, ru_direct=True, diagnostic_proxy=diagnostic_json_proxy, adblock=adblock_dns_get(username)),
            {**headers, "Content-Type": "application/json; charset=utf-8", "Routing-Enable": "false", "routing": "happ://routing/off"},
            "legacy" if public_path == "subscribe-old" else ("fake" if plain != real_plain else "real"),
        )
        return
    if browser_like:
        subscription_url = f"https://{DOMAIN}/{public_path}/{urllib.parse.quote(token, safe='')}"
        html_body = engine.browser_stub_html(
            logo_svg=read_subscription_stub_logo(),
            support_url=NEXT_SUPPORT_URL,
            subscription_url=subscription_url,
        )
        finish(200, html_body, {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow",
        }, "html")
        return
    encoded = SubscriptionEngine.encode_body(plain)
    finish(200, encoded, headers, "legacy" if public_path == "subscribe-old" else ("fake" if plain != real_plain else "real"))


def is_browser_subscription_request(handler: BaseHTTPRequestHandler) -> bool:
    accept = (handler.headers.get("Accept") or "").lower()
    sec_dest = (handler.headers.get("Sec-Fetch-Dest") or "").lower()
    ua = (handler.headers.get("User-Agent") or "").lower()
    if is_subscription_client_user_agent(ua):
        return False
    if "text/html" in accept:
        return True
    if sec_dest == "document":
        return True
    if "mozilla/" in ua:
        return True
    return False


def is_subscription_client_user_agent(ua: str) -> bool:
    return any(client in ua for client in ("happ", "clash", "v2ray", "sing-box", "hiddify"))


def read_subscription_stub_logo() -> str:
    try:
        return SUB_STUB_LOGO.read_text(encoding="utf-8")
    except Exception:
        return ""


def happ_bridge_html(token: str, user_agent: str) -> str:
    safe_token = urllib.parse.quote(token, safe="")
    sub_url = f"https://{DOMAIN}/subscribe/{safe_token}"
    happ_url = "happ://add/" + urllib.parse.quote(sub_url, safe="")
    is_windows = "windows" in (user_agent or "").lower()
    title = "скопируй ссылку и&nbsp;добавь в&nbsp;Happ вручную:" if is_windows else "открываем Happ"
    auto_script = "" if is_windows else f"""
    <script>
      setTimeout(() => {{ window.location.href = "{html.escape(happ_url)}"; }}, 250);
    </script>"""
    action = f"""
      <div class="copy-row">
        <input id="sub-url" readonly value="{html.escape(sub_url)}">
        <button type="button" onclick="copySub()">скопировать</button>
      </div>
      <p class="hint">если Happ не&nbsp;открылся, скопируй ссылку и&nbsp;добавь её в&nbsp;приложении вручную</p>
    """ if is_windows else f"""
      <a class="open-btn" href="{html.escape(happ_url)}">открыть Happ</a>
      <p class="hint">если ничего не&nbsp;произошло, вернись сюда и&nbsp;нажми кнопку ещё раз</p>
    """
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>goida · happ</title>
  <style>
    @font-face {{
      font-family: "Inter";
      src: url("https://web.goida.fun/fonts/Inter-Regular.ttf") format("truetype");
      font-weight: 400;
      font-style: normal;
      font-display: swap;
    }}
    @font-face {{
      font-family: "Inter";
      src: url("https://web.goida.fun/fonts/Inter-Medium.ttf") format("truetype");
      font-weight: 500;
      font-style: normal;
      font-display: swap;
    }}
    * {{
      box-sizing: border-box;
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: -0.04em;
    }}
    html {{ letter-spacing: -0.04em; }}
    html, body {{ margin: 0; min-height: 100%; }}
    body {{
      min-height: 100vh;
      display: grid;
      place-items: start center;
      padding: calc(18px + env(safe-area-inset-top)) 16px calc(24px + env(safe-area-inset-bottom));
      background:
        radial-gradient(75% 58% at 16% 7%, rgba(54,210,158,.36), transparent 58%),
        radial-gradient(62% 48% at 92% 28%, rgba(37,116,156,.30), transparent 62%),
        radial-gradient(88% 64% at 46% 112%, rgba(23,126,92,.42), transparent 68%),
        linear-gradient(158deg, #0b3a2f 0%, #06221b 46%, #020d0a 100%);
      color: rgba(246,255,249,.94);
      font-weight: 400;
      letter-spacing: -0.04em;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: -20%;
      pointer-events: none;
      background:
        radial-gradient(42% 34% at 18% 80%, rgba(48,209,158,.18), transparent 62%),
        radial-gradient(38% 30% at 78% 12%, rgba(255,255,255,.055), transparent 68%),
        linear-gradient(120deg, transparent 0 36%, rgba(255,255,255,.035) 49%, transparent 63%);
      filter: blur(28px);
      opacity: .85;
    }}
    main {{
      position: relative;
      width: min(100%, 640px);
      display: grid;
      gap: 12px;
      padding-top: 6px;
    }}
    h1 {{ margin: 0; font-size: clamp(28px, 7vw, 38px); line-height: 1.05; font-weight: 500; }}
    .title-animated {{
      width: fit-content;
      background: linear-gradient(90deg, rgba(246,255,249,.95) 0%, rgba(246,255,249,.95) 38%, rgba(246,255,249,.48) 50%, rgba(246,255,249,.95) 62%, rgba(246,255,249,.95) 100%);
      background-size: 260% 100%;
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
      animation: titleIn .42s cubic-bezier(.2,.8,.2,1) both, titleSheen 2.8s ease-in-out .48s infinite;
    }}
    @keyframes titleIn {{
      from {{ opacity: 0; transform: translateY(8px); filter: blur(5px); }}
      to {{ opacity: 1; transform: translateY(0); filter: blur(0); }}
    }}
    @keyframes titleSheen {{
      0%, 48% {{ background-position: 120% 0; }}
      82%, 100% {{ background-position: -120% 0; }}
    }}
    .card {{
      display: grid;
      gap: 16px;
      padding: clamp(18px, 4vw, 24px);
      border-radius: 18px;
      background: rgba(255,255,255,.070);
      border: 1px solid rgba(255,255,255,.125);
      box-shadow: 0 10px 24px rgba(0,0,0,.20);
      backdrop-filter: blur(24px) saturate(1.18);
      -webkit-backdrop-filter: blur(24px) saturate(1.18);
      overflow: hidden;
      -webkit-mask-image: -webkit-radial-gradient(white, black);
    }}
    .open-btn,
    .copy-row > button {{
      border: 0;
      border-radius: 14px;
      min-height: 50px;
      padding: 0 20px;
      background: rgba(255,255,255,.10);
      color: rgba(246,255,249,.98);
      font: inherit;
      font-weight: 500;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      white-space: nowrap;
      border: 1px solid rgba(255,255,255,.20);
      box-shadow: 0 6px 16px rgba(0,0,0,.18);
    }}
    button, input, a {{ font: inherit; letter-spacing: inherit; }}
    .copy-row {{ display: grid; grid-template-columns: 1fr auto; gap: 12px; }}
    input {{
      min-width: 0;
      min-height: 50px;
      padding: 0 18px;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,.13);
      background: rgba(0,0,0,.14);
      color: rgba(226,244,235,.78);
      outline: none;
    }}
    .hint {{ margin: 0; color: rgba(226,244,235,.66); font-size: 15px; line-height: 1.35; }}
    .download-mini {{ display: grid; gap: 0; padding: 2px 0 0; }}
    .download-mini.expanded {{ gap: 12px; }}
    .download-head {{
      width: 100%;
      min-height: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
      border: 0;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
      color: inherit;
      padding: 4px 0;
      text-align: left;
      cursor: pointer;
    }}
    .download-head h2 {{ margin: 0; font-size: clamp(24px, 6vw, 32px); line-height: 1.08; font-weight: 500; }}
    .download-chevron {{ width: 34px; height: 34px; border-radius: 999px; display: grid; place-items: center; background: rgba(255,255,255,.075); border: 1px solid rgba(255,255,255,.14); }}
    .download-chevron svg {{ width: 18px; height: 18px; transition: transform .24s cubic-bezier(.2,.8,.2,1); }}
    .download-mini.expanded .download-chevron svg {{ transform: rotate(180deg); }}
    .download-body {{
      display: grid;
      gap: 12px;
      overflow: hidden;
      max-height: 0;
      opacity: 0;
      transform: translateY(-6px);
      transition: max-height .3s cubic-bezier(.2,.8,.2,1), opacity .2s ease, transform .24s ease;
    }}
    .download-body.open {{ max-height: 520px; opacity: 1; transform: translateY(0); }}
    .muted {{ margin: 0; color: rgba(226,244,235,.66); }}
    .download-tabs {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(92px, 1fr)); gap: 8px; }}
    .download-tabs button {{
      min-width: 0;
      min-height: 42px;
      border-radius: 14px;
      padding: 0 10px;
      background: rgba(255,255,255,.070);
      color: rgba(226,244,235,.70);
      font-weight: 400;
      box-shadow: none;
      white-space: normal;
      overflow-wrap: anywhere;
      text-align: center;
      line-height: 1.08;
    }}
    .download-tabs button.active {{ background: rgba(48,209,158,.24); color: rgba(246,255,249,.98); }}
    .download-actions {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 160px), 1fr)); gap: 8px; }}
    .download-actions a {{
      min-width: 0;
      min-height: 46px;
      border-radius: 14px;
      padding: 0 10px;
      background: rgba(255,255,255,.10);
      color: rgba(246,255,249,.98);
      text-decoration: none;
      display: flex;
      align-items: center;
      justify-content: center;
      border: 1px solid rgba(255,255,255,.18);
      font-weight: 500;
      white-space: normal;
      overflow-wrap: anywhere;
      text-align: center;
      line-height: 1.08;
    }}
    @media (max-width: 640px) {{
      .copy-row {{ grid-template-columns: 1fr; }}
      .open-btn, .copy-row > button, input {{ width: 100%; }}
      .download-actions {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="card bridge-card">
      <h1 class="title-animated">{title}</h1>
      {action}
    </section>
    <section class="download-mini" id="dl-card">
      <button class="download-head" type="button" onclick="toggleDownloads()" aria-expanded="false">
        <h2>нет Happ? скачай!</h2>
        <span class="download-chevron"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"></path></svg></span>
      </button>
      <div class="download-body" id="download-body">
        <p class="muted">выберите устройство</p>
        <div class="download-tabs" id="download-tabs"></div>
        <p class="muted">скачивание</p>
        <div class="download-actions" id="download-actions"></div>
      </div>
    </section>
  </main>
  <script>
    const downloads = {{
      ios: [["AppStore [ru]", "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973"], ["AppStore [global]", "https://apps.apple.com/us/app/happ-proxy-utility/id6504287215"]],
      android: [["Google Play", "https://play.google.com/store/apps/details?id=com.happproxy"], ["APK", "https://github.com/Happ-proxy/happ-android/releases/latest"]],
      windows: [["GitHub Releases", "https://github.com/Happ-proxy/happ-desktop/releases/latest"]],
      macos: [["AppStore [ru]", "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973"], ["DMG", "https://github.com/Happ-proxy/happ-desktop/releases/latest"]],
      appletv: [["App Store", "https://apps.apple.com/us/app/happ-proxy-utility-for-tv/id6748297274"]],
      androidtv: [["Google Play", "https://play.google.com/store/apps/details?id=com.happproxy"]]
    }};
    const downloadLabels = [["ios","iOS"],["android","Android"],["windows","Windows"],["macos","macOS"],["appletv","Apple TV"],["androidtv","Android TV"]];
    let downloadPlatform = guessPlatform();
    function guessPlatform() {{
      const ua = navigator.userAgent.toLowerCase();
      if (ua.includes("windows")) return "windows";
      if (ua.includes("macintosh") || ua.includes("mac os")) return "macos";
      if (ua.includes("android")) return "android";
      return "ios";
    }}
    function renderDownloads() {{
      const tabs = document.getElementById("download-tabs");
      const actions = document.getElementById("download-actions");
      tabs.innerHTML = downloadLabels.map(([key, label]) =>
        `<button type="button" class="${{downloadPlatform === key ? "active" : ""}}" onclick="setDownloadPlatform('${{key}}')">${{label}}</button>`
      ).join("");
      actions.innerHTML = (downloads[downloadPlatform] || []).map(([label, url]) =>
        `<a href="${{url}}" rel="noopener noreferrer">${{label}}</a>`
      ).join("");
    }}
    function toggleDownloads() {{
      const card = document.getElementById("dl-card");
      const body = document.getElementById("download-body");
      const open = !card.classList.contains("expanded");
      card.classList.toggle("expanded", open);
      body.classList.toggle("open", open);
      card.querySelector(".download-head").setAttribute("aria-expanded", open ? "true" : "false");
    }}
    function setDownloadPlatform(key) {{
      downloadPlatform = key;
      renderDownloads();
    }}
    function copySub() {{
      const input = document.getElementById("sub-url");
      navigator.clipboard.writeText(input.value).catch(() => {{
        input.select();
        document.execCommand("copy");
      }});
    }}
    renderDownloads();
  </script>
  {auto_script}
</body>
</html>"""


_notify_bot: "TelegramBot | None" = None


class SubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    INTERNAL_INVITE_PATHS = (
        "/internal/invites/create-user",
        "/internal/invites/set-remna",
        "/internal/invites/notify-owner",
    )

    def _json_response(self, status: int, payload: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_POST(self):
        path = self.path.rstrip("/")
        if path not in ("/notify", "/rknstatus", "/analyzer") + self.INTERNAL_INVITE_PATHS:
            self.send_response(404)
            self.end_headers()
            return

        notify_token = os.environ.get("NOTIFY_TOKEN", "")
        if not notify_token or self.headers.get("Authorization") != f"Bearer {notify_token}":
            self.send_response(401)
            self.end_headers()
            return

        # /notify доступен только с домашнего сервера
        if path == "/notify":
            client_ip = self.headers.get("X-Real-IP", self.client_address[0])
            if client_ip != NOTIFY_ALLOWED_IP:
                self.send_response(403)
                self.end_headers()
                return
        elif path == "/analyzer" or path in self.INTERNAL_INVITE_PATHS:
            client_ip = self.headers.get("X-Real-IP", self.client_address[0])
            if client_ip not in ("127.0.0.1", "::1") and client_ip not in SERVER_IPS:
                self.send_response(403)
                self.end_headers()
                return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self.send_response(400)
            self.end_headers()
            return

        if path == "/notify":
            text = str(body.get("text", ""))[:4000]
            silent = bool(body.get("silent", True))
            if _notify_bot and text:
                _notify_bot.send(OWNER_ID, text, disable_notification=silent)
        elif path == "/rknstatus":
            set_bot_setting(RKN_STATUS_KEY, json.dumps(body))
            tspu = body.get("tspu_status") or {}
            profiles = body.get("service_profiles") or {}
            # primary_ip_status ненадёжен: проба к bare-IP всегда даёт cert-fail
            # (TLS_BLOCK), т.к. сертификат на домен. Доверяем domain_status (SNI=домен).
            # Аварийный режим — только когда И домен, И primary недоступны и есть hy2.
            domain_bad = tspu.get("domain_status") not in ("", "OK", None)
            primary_bad = tspu.get("primary_ip_status") not in ("", "OK", None)
            hy2_ok = bool((profiles.get("backup_hy2_8443_udp") or {}).get("ok"))
            if domain_bad and primary_bad and hy2_ok:
                emergency_ingress_set(True)
            elif tspu.get("domain_status") == "OK":
                # домен снова доступен — снимаем аварийный режим автоматически
                emergency_ingress_set(False)
        elif path == "/analyzer":
            action = str(body.get("action") or "").strip()
            enabled = bool(body.get("enabled"))
            if action == "foreign_exits_down":
                foreign_exits_down_set(enabled)
            elif action == "fra_exit_down":
                fra_exit_down_set(enabled)
            else:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":false,"error":"unknown action"}')
                return
        elif path == "/internal/invites/create-user":
            username = str(body.get("username") or "").strip()
            device_limit = body.get("deviceLimit")
            expire_days = body.get("expireDays")
            if not username or not isinstance(device_limit, int) or not isinstance(expire_days, int):
                self._json_response(400, {"ok": False, "error": "bad request"})
                return
            try:
                result = provision_invited_user(username, device_limit, expire_days)
            except Exception as exc:
                self._json_response(400, {"ok": False, "error": str(exc)})
                return
            self._json_response(200, {"ok": True, "token": result["token"], "subUrl": result["subUrl"]})
            return
        elif path == "/internal/invites/set-remna":
            username = str(body.get("username") or "").strip()
            if not username:
                self._json_response(400, {"ok": False, "error": "bad request"})
                return
            device_limit = body.get("deviceLimit")
            expire_days = body.get("expireDays")
            expire_now = bool(body.get("expireNow"))
            try:
                if isinstance(device_limit, int):
                    remnawave_set_device_limit(username, device_limit)
                if isinstance(expire_days, int):
                    remnawave_query(
                        f"update users set expire_at=now() + interval '{int(expire_days)} days', "
                        f"updated_at=now() where username={pg_quote(username)};"
                    )
                if expire_now:
                    remnawave_query(
                        f"update users set expire_at=now(), updated_at=now() where username={pg_quote(username)};"
                    )
            except Exception as exc:
                self._json_response(400, {"ok": False, "error": str(exc)})
                return
            self._json_response(200, {"ok": True})
            return
        elif path == "/internal/invites/notify-owner":
            invite_id = body.get("inviteId")
            if not isinstance(invite_id, int):
                self._json_response(400, {"ok": False, "error": "bad request"})
                return
            conn = sqlite3.connect(BOT_DB, timeout=30)
            conn.row_factory = sqlite3.Row
            invite_row = conn.execute("SELECT * FROM client_invites WHERE id=?", (invite_id,)).fetchone()
            conn.close()
            if not invite_row:
                self._json_response(404, {"ok": False, "error": "invite not found"})
                return
            invite = dict(invite_row)
            text = (
                f"у {html.escape(invite['inviter_username'])} появился друг "
                f"{html.escape(invite['activated_username'])}\n"
                f"tg_id: <code>{invite['activated_tg_id']}</code> @{html.escape(invite['activated_tg_username'])}"
            )
            markup = {"inline_keyboard": [[
                {"text": "Одобрить", "callback_data": f"invapprove:{invite_id}", "style": "success"},
                {"text": "Забанить", "callback_data": f"invban:{invite_id}", "style": "danger"},
            ]]}
            if _notify_bot:
                _notify_bot.send(OWNER_ID, text, markup)
            self._json_response(200, {"ok": True})
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def do_GET(self):
        parts = self.path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "happ":
            token = parts[1]
            if not get_user_by_token(token) and not remnawave_user_by_legacy_token(token) and not is_deleted_sub(token):
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("404".encode())
                return
            body = happ_bridge_html(token, self.headers.get("User-Agent", ""))
            send_text(self, 200, body, {
                "Content-Type": "text/html; charset=utf-8",
                "Cache-Control": "no-store",
                "X-Robots-Tag": "noindex, nofollow",
            })
            return

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

        if urllib.parse.urlsplit(self.path).path == "/wl/share":
            handle_wl_share(self)
            return

        if urllib.parse.urlsplit(self.path).path == "/routing/share":
            handle_routing_share(self)
            return

        self.send_response(404)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body><h1>404</h1></body></html>")


# ── партнёрские share-эндпоинты — общий token+domain-allowlist guard ─────────

_partner_share_dns_cache: dict[str, dict] = {}


def _resolve_domain_ips(domain: str) -> set[str]:
    """Резолвит domain → множество IP, кэш 5 минут (отдельный на каждый domain)."""
    import socket as _sock
    now = time.time()
    cache = _partner_share_dns_cache.setdefault(domain, {"ts": 0.0, "ips": set()})
    if now - cache["ts"] < 300 and cache["ips"]:
        return cache["ips"]
    try:
        _, _, addrs = _sock.gethostbyname_ex(domain)
        cache["ips"] = set(addrs)
        cache["ts"] = now
    except Exception as exc:
        print(f"partner share dns resolve {domain}: {exc}", file=sys.stderr)
    return cache["ips"]


def _partner_share_guard(
    handler: BaseHTTPRequestHandler,
    *,
    token_env: str,
    allowed_domain_env: str,
    default_domain: str,
    log_label: str,
) -> bool:
    """Проверяет ?token= и X-Real-IP против allowlist домена. True — доступ разрешён."""
    share_token = os.environ.get(token_env, "")
    if not share_token:
        send_text(handler, 404, "not found", {"Content-Type": "text/plain"})
        return False

    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    provided = qs.get("token", [""])[0]
    if not provided or provided != share_token:
        send_text(handler, 401, "unauthorized", {"Content-Type": "text/plain"})
        return False

    domain = os.environ.get(allowed_domain_env, default_domain)
    client_ip = handler.headers.get("X-Real-IP", handler.client_address[0])
    allowed_ips = _resolve_domain_ips(domain)
    if allowed_ips and client_ip not in allowed_ips:
        print(f"{log_label} blocked ip={client_ip} allowed={allowed_ips}", file=sys.stderr)
        send_text(handler, 403, "forbidden", {"Content-Type": "text/plain"})
        return False

    return True


def handle_wl_share(handler: BaseHTTPRequestHandler) -> None:
    """GET /wl/share?token=TOKEN — отдаёт wl-list.txt с {uuid} вместо реальных UID.

    Проверки:
      1. WL_SHARE_TOKEN задан и совпадает с ?token=
      2. X-Real-IP входит в DNS-записи WL_SHARE_ALLOWED_DOMAIN (по умолчанию lekanta.ru)
    """
    if not _partner_share_guard(
        handler,
        token_env="WL_SHARE_TOKEN",
        allowed_domain_env="WL_SHARE_ALLOWED_DOMAIN",
        default_domain="lekanta.ru",
        log_label="wl/share",
    ):
        return

    try:
        with open(WL_REGISTRY_FILE) as f:
            body = f.read()
    except FileNotFoundError:
        body = ""

    send_text(handler, 200, body, {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "no-store, no-cache",
        "X-Robots-Tag": "noindex, nofollow",
    })


# ── /routing/share — защищённая выдача routing.json для партнёрских серверов ─

ROUTING_SERVED_FILE = os.environ.get("ROUTING_SERVED_FILE", "/var/www/html/routing.json")


def handle_routing_share(handler: BaseHTTPRequestHandler) -> None:
    """GET /routing/share?token=TOKEN — отдаёт routing.json партнёрским серверам.

    routing.json больше не публичный: свои Happ-клиенты получают роутинг встроенным
    в саму подписку (happ://routing/onadd/), этот путь нужен только внешним потребителям.

    Проверки:
      1. ROUTING_SHARE_TOKEN задан и совпадает с ?token=
      2. X-Real-IP входит в DNS-записи ROUTING_SHARE_ALLOWED_DOMAIN (по умолчанию lekanta.ru)
    """
    if not _partner_share_guard(
        handler,
        token_env="ROUTING_SHARE_TOKEN",
        allowed_domain_env="ROUTING_SHARE_ALLOWED_DOMAIN",
        default_domain="lekanta.ru",
        log_label="routing/share",
    ):
        return

    try:
        with open(ROUTING_SERVED_FILE) as f:
            body = f.read()
    except FileNotFoundError:
        send_text(handler, 404, "not found", {"Content-Type": "text/plain"})
        return

    send_text(handler, 200, body, {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store, no-cache",
        "X-Robots-Tag": "noindex, nofollow",
    })


def start_sub_server():
    server = ThreadingHTTPServer(("127.0.0.1", SUB_PORT), SubHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"subscription server on 127.0.0.1:{SUB_PORT}", file=sys.stderr)


# === telegram api ===

class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"
        self.offset = 0

    def api(self, method: str, data: dict = None, http_timeout: int = 20) -> dict:
        url = f"{self.base}/{method}"
        if data:
            payload = json.dumps(data).encode()
            req = urllib.request.Request(url, data=payload,
                                         headers={"Content-Type": "application/json"})
        else:
            req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            print(f"tg api error: {e}", file=sys.stderr)
            return {}

    def get_updates(self) -> list:
        result = self.api("getUpdates", {
            "offset": self.offset,
            "timeout": 30,
            "allowed_updates": ["message", "callback_query"]
        }, http_timeout=40)
        updates = result.get("result", [])
        if updates:
            self.offset = updates[-1]["update_id"] + 1
        return updates

    def send(self, chat_id: int, text: str, reply_markup: dict = None,
             parse_mode: str = "HTML", disable_notification: bool = False) -> dict:
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
            "disable_notification": disable_notification,
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
edit_state: dict = {}       # chat_id → {"user": name, "message_id": int}
edit_buffer: dict = {}      # chat_id → {"user": name, "parts": [...], "timer": Thread}
rename_state: dict = {}     # chat_id → {"user": name, "message_id": int}
addwl_state: dict = {}      # chat_id → {"stage": "waiting"} | {"entries": [{"vless", "parsed"}]}
addwl_buffer: dict = {}     # chat_id → {"parts": [str], "timer": Thread}
subdesc_state: dict = {}    # chat_id → {"stage": "waiting"}
sub_setting_state: dict = {}  # chat_id → {"key": str, "msg_id": int}
admin_paid_state: dict = {}  # chat_id → {"user": name, "message_id": int}


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
        "/user &lt;name&gt; — карточка пользователя\n"
        "/adduser &lt;name&gt; — добавить пользователя\n"
        "/devices [name] — устройства\n"
        "/stats — статистика подписок за 7 дней\n"
        "/subdesc — описание shadow-подписки\n"
        "/dnsip — DNS IP (primary/backup)\n"
        "/rkn — доступность эндпоинтов с RU ISP\n"
        "/emergency [on|off] — аварийный режим входа\n"
        "/sub — серверы подписки (hydra)\n"
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


def cmd_stats(bot: TelegramBot, chat_id: int):
    bot.send(chat_id, format_sub_stats(get_sub_stats(days=7)))


PAGE_SIZE = 5


def build_users_keyboard(users: list, page: int) -> tuple:
    """строит клавиатуру пользователей с пагинацией, возвращает (text, markup)"""
    total = len(users)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * PAGE_SIZE
    chunk = users[start:start + PAGE_SIZE]

    # сводка — remnawave_usernames() один запрос вместо N (было: hydra_get_status
    # дёргал docker exec+psql на каждого юзера, отсюда и тормоза /users/пагинации)
    remna_usernames = remnawave_usernames()
    hydra_count = sum(1 for u in users if hydra_get_status_batch(u["name"], remna_usernames))
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


# === DNS IP control ===

def build_dns_keyboard(current_ip: str) -> dict:
    row = []
    for label, flag, ip in (("primary", "🇷🇺", CF_PRIMARY_IP), ("backup", "🇰🇵", CF_BACKUP_IP)):
        btn = {"text": f"{flag} {label} {ip}", "callback_data": f"dnsip:{ip}"}
        if ip == current_ip:
            btn["style"] = "success"
        row.append(btn)
    return {
        "inline_keyboard": [
            row,
            [{"text": "заменить IP", "callback_data": "dnsip:replace"}],
            [{"text": "закрыть  ↜", "callback_data": "dnsip:close"}],
        ]
    }


def cmd_rkn(bot: TelegramBot, chat_id: int):
    raw = get_bot_setting(RKN_STATUS_KEY, "")
    if not raw:
        bot.send(chat_id, "нет данных — проверка запустится через ~10 мин (rkn-checker.timer)")
        return
    try:
        data = json.loads(raw)
    except Exception:
        bot.send(chat_id, "❌ ошибка чтения статуса")
        return

    ts = data.get("ts", 0)
    results = data.get("results", [])
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m %H:%M UTC") if ts else "—"

    lines = [f"🔍 <b>RKN check</b>  <i>{dt}</i>\n"]
    for r in results:
        ok      = r.get("ok")
        tcp_ok  = r.get("tcp_ok", False)
        verdict = r.get("verdict", "")
        ms      = r.get("ms", 0)
        ip      = r.get("sys_ip") or ""
        if ok:
            icon   = "✅"
            detail = f"{ms}ms" if ms else "ok"
        else:
            icon   = "🔴"
            detail = verdict or "FAIL"
        ip_str = f"  <code>{ip}</code>" if ip else ""
        lines.append(f"{icon} <b>{r['label']}</b>{ip_str}  {detail}")
    bot.send(chat_id, "\n".join(lines))


def cmd_emergency(bot: TelegramBot, chat_id: int, arg: str = ""):
    arg = arg.strip().lower()
    if arg in ("on", "1", "enable", "вкл"):
        emergency_ingress_set(True)
    elif arg in ("off", "0", "disable", "выкл"):
        emergency_ingress_set(False)
    enabled = emergency_ingress_get()
    bot.send(
        chat_id,
        "🚨 <b>emergency ingress mode</b>\n\n"
        f"status: <b>{'on' if enabled else 'off'}</b>\n"
        "on: «Резервный» поднимается в начало подписки; Happ JSON остаётся JSON.\n"
        "usage: <code>/emergency on</code> или <code>/emergency off</code>",
    )


def cmd_dnsip(bot: TelegramBot, chat_id: int, msg_id: int | None = None):
    current_ip, _ = cf_get_dns_record()
    if not current_ip:
        text = (
            "❌ не удалось прочитать DNS из Cloudflare\n"
            "убедись что CF_TOKEN и CF_ZONE_ID заданы в .env"
        )
    else:
        label = "primary" if current_ip == CF_PRIMARY_IP else "backup"
        text = (
            f"🌐 <b>DNS: {CF_DNS_DOMAIN}</b>\n\n"
            f"активный IP: <code>{current_ip}</code> ({label})\n\n"
            f"primary: <code>{CF_PRIMARY_IP}</code>\n"
            f"backup:  <code>{CF_BACKUP_IP}</code>\n\n"
            "ручное переключение фиксируется: watchdog не вернёт IP обратно сам"
        )
    markup = build_dns_keyboard(current_ip or "")
    if msg_id:
        bot.edit(chat_id, msg_id, text, markup)
    else:
        bot.send(chat_id, text, markup)


# === subscription servers ===

def build_sub_keyboard(page: int = 0) -> tuple[str, dict]:
    servers = get_hydra_outbound_status()  # [(tag, enabled)]
    total = len(servers)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = servers[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    sub_url = get_bot_setting(HYDRA_SUB_URL_KEY, HYDRA_SUB_URL_DEFAULT)
    sub_ua  = get_bot_setting(HYDRA_SUB_UA_KEY,  HYDRA_SUB_UA_DEFAULT)
    enabled_count = sum(1 for _, e in servers if e)
    text = (
        f"📋 <b>серверы подписки</b>\n\n"
        f"🔗 <code>{html.escape(sub_url)}</code>\n"
        f"🤖 <code>{html.escape(sub_ua)}</code>\n\n"
        f"серверов: {total}  активных: {enabled_count}"
    )

    buttons = []
    for tag, enabled in chunk:
        btn = {"text": hydra_outbound_label(tag), "callback_data": f"subserver:{tag}"}
        btn["style"] = "success" if enabled else "danger"
        buttons.append([btn])

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append({"text": "⇽", "callback_data": f"subserverpage:{page - 1}"})
        nav.append({"text": f"{page + 1}/{total_pages}", "callback_data": "subserverpage:noop"})
        if page < total_pages - 1:
            nav.append({"text": "⇾", "callback_data": f"subserverpage:{page + 1}"})
        buttons.append(nav)

    buttons.append([
        {"text": "изменить URL",        "callback_data": "subserver:edit_url"},
        {"text": "изменить UA",         "callback_data": "subserver:edit_ua"},
    ])
    buttons.append([{"text": "закрыть  ↜", "callback_data": "subserver:close"}])
    return text, {"inline_keyboard": buttons}


def cmd_sub(bot: TelegramBot, chat_id: int, msg_id: int | None = None):
    text, markup = build_sub_keyboard(0)
    if msg_id:
        bot.edit(chat_id, msg_id, text, markup)
    else:
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

    bot.send(chat_id, f"⏳ создаю пользователя <b>{name}</b> в Remnawave...")

    try:
        remna = remnawave_create_user(name)
        vless_uuid = remna.get("vlessUuid") or remnawave_query(
            f"select vless_uuid from users where username={pg_quote(name)} limit 1;"
        ).strip()
        if not vless_uuid:
            raise RuntimeError("Remnawave user created, but vless_uuid is empty")

        # добавляем в бот-бд
        token = add_user_db(name)
        set_custom_sub(name, remnawave_subscription_links(name, vless_uuid))
        profile_conn = sqlite3.connect(BOT_DB, timeout=30)
        ensure_client_profile(profile_conn, name)
        profile_conn.commit()
        profile_conn.close()
        invite_token = create_client_invite_token(name)

        # старый URL остаётся точкой входа, но содержимое уже Remnawave-only
        sub_url = f"https://{DOMAIN}/subscribe/{token}"

        bot.send(chat_id,
            f"✅ пользователь <b>{name}</b> создан\n\n"
            f"📎 подписка:\n<code>{sub_url}</code>\n\n"
            f"🔗 код приглашения клиентского бота:\n<code>{invite_token}</code>\n\n"
            f"источник пользователя и лимита: Remnawave.",
            {"inline_keyboard": [[
                {"text": "👥 перейти к пользователям", "callback_data": "back:users"}
            ]]}
        )

    except Exception as e:
        bot.send(chat_id, f"❌ ошибка: {e}")


def cmd_readdrw(bot: TelegramBot, chat_id: int, name: str):
    """восстанавливает RW-пользователя в bot.db.
    нужен если юзер жив в Remnawave, но удалён из bot.db/users.
    идемпотентен: если уже есть в bot.db — только обновляет custom_sub.
    """
    if not name:
        bot.send(chat_id, "использование: /readdrw &lt;username&gt;")
        return

    remna = remnawave_user(name)
    if not remna:
        bot.send(chat_id, f"пользователь <b>{html.escape(name)}</b> не найден в Remnawave")
        return

    # уже есть в bot.db — только рефреш
    if get_user(name):
        custom_sub = remnawave_refresh_custom_sub(name)
        user = get_user(name)
        invite_token = create_client_invite_token(name)
        sub_url = f"https://{DOMAIN}/subscribe/{user['token']}"
        bot.send(chat_id,
            f"<b>{html.escape(name)}</b> уже в bot.db, custom_sub обновлён.\n\n"
            f"подписка:\n<code>{sub_url}</code>\n\n"
            f"приглашение client-bot:\n<code>{invite_token}</code>"
        )
        return

    # пробуем восстановить тот же token из RW tag (чтобы не менять URL у клиента)
    existing_token = remnawave_get_legacy_sub_token(name)
    if existing_token:
        conn = sqlite3.connect(BOT_DB, timeout=30)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO users (name, token, created_at) VALUES (?, ?, ?)",
                (name, existing_token, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
        token = existing_token
    else:
        # генерируем новый token, сохраняем в Remnawave tag
        token = add_user_db(name)
        remnawave_query(
            f"update users set tag={pg_quote('legacy-sub-token:' + token)} "
            f"where username={pg_quote(name)};"
        )

    vless_uuid = remnawave_vless_uuid(name)
    if vless_uuid:
        set_custom_sub(name, remnawave_subscription_links(name, vless_uuid))

    profile_conn = sqlite3.connect(BOT_DB, timeout=30)
    try:
        ensure_client_profile(profile_conn, name)
        profile_conn.commit()
    finally:
        profile_conn.close()

    invite_token = create_client_invite_token(name)
    sub_url = f"https://{DOMAIN}/subscribe/{token}"

    bot.send(chat_id,
        f"✅ <b>{html.escape(name)}</b> восстановлен в bot.db\n\n"
        f"подписка:\n<code>{sub_url}</code>\n\n"
        f"приглашение client-bot:\n<code>{invite_token}</code>"
    )


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
    admin_profile = get_admin_profile(username)
    paid_label = admin_profile["paid_until"] or "не задано"
    text += (
        f"💳 действует до: <b>{html.escape(paid_label)}</b>\n"
        f"🎁 бесплатный доступ: {'вкл' if admin_profile['free_access'] else 'выкл'}\n"
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
            btn("Продлить", f"adminpaid:{username}"),
            btn("Устройства", f"admindev:{username}"),
        ],
        [
            btn("Free access", f"adminfree:{username}"),
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


def _wl_json_to_entry(cfg: dict) -> dict | None:
    if not validate_wl_json_config(cfg):
        return None
    cfg["remarks"] = clean_wl_remark(str(cfg.get("remarks") or ""))
    targets = wl_json_vless_targets(cfg)
    if not targets:
        return None
    marker = encode_wl_json_line(cfg)
    host, port = targets[0]
    return {
        "vless": marker,
        "parsed": {
            "uuid": "",
            "host": host,
            "port": port,
            "sni": "",
            "pbk": "",
            "sid": "",
            "flow": "",
            "fp": "",
            "name": str(cfg.get("remarks") or "wl-json"),
            "kind": "json",
            "targets": targets,
        },
    }


def parse_wl_blob(blob: str) -> list[dict]:
    """парсит blob → [{vless, parsed}]. Поддерживает vless, full Xray JSON и JSON-массив."""
    blob = (blob or "").strip()
    entries: list[dict] = []
    try:
        data = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        data = None
    if isinstance(data, dict):
        entry = _wl_json_to_entry(data)
        if entry:
            return [entry]
        v = _wl_obj_to_vless(data)
        if v:
            p = parse_vless_wl(v)
            if p:
                return [{"vless": v, "parsed": p}]
        return []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str) and item.strip().startswith("vless://"):
                v = item.strip()
                p = parse_vless_wl(v)
                if p:
                    entries.append({"vless": v, "parsed": p})
            elif isinstance(item, dict):
                json_entry = _wl_json_to_entry(item)
                if json_entry:
                    entries.append(json_entry)
                    continue
                v = _wl_obj_to_vless(item)
                if v:
                    p = parse_vless_wl(v)
                    if p:
                        entries.append({"vless": v, "parsed": p})
        return entries
    for line in blob.splitlines():
        line = line.strip()
        if line.startswith(WL_JSON_PREFIX):
            cfg = decode_wl_json_line(line)
            entry = _wl_json_to_entry(cfg or {})
            if entry:
                entries.append(entry)
        elif line.startswith("vless://"):
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
        if p.get("kind") == "json":
            preview.append(f"• <code>{p['name']}</code> targets=<code>{len(p.get('targets') or [])}</code>")
        else:
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

    elif data.startswith("adminpaid:"):
        username = data[10:]
        bot.edit(
            chat_id,
            msg_id,
            f"на сколько дней продлить <b>{html.escape(username)}</b>?\n\n"
            "отправьте число дней сообщением, например <code>30</code>.",
            {"inline_keyboard": [[{"text": "отмена  ↜", "callback_data": f"user:{username}"}]]},
        )
        admin_paid_state[chat_id] = {"user": username, "message_id": msg_id}

    elif data.startswith("admindev:"):
        username = data[9:]
        limit = get_user_device_limit(username)
        label = "∞" if limit == 0 else str(limit)
        markup = {"inline_keyboard": [
            [
                {"text": "-1", "callback_data": f"admindevset:{username}:-1"},
                {"text": "+1", "callback_data": f"admindevset:{username}:1"},
                {"text": "+3", "callback_data": f"admindevset:{username}:3"},
            ],
            [{"text": "← карточка", "callback_data": f"user:{username}"}],
        ]}
        bot.edit(chat_id, msg_id, f"📱 <b>{html.escape(username)}</b>\nтекущий лимит устройств: <b>{label}</b>", markup)

    elif data.startswith("admindevset:"):
        _, username, delta_raw = data.split(":", 2)
        current = get_user_device_limit(username)
        new_limit = max(1, current + int(delta_raw))
        set_user_device_limit(username, new_limit, user_id)
        bot.answer_callback(cb_id, f"лимит: {new_limit}")
        show_user_info(bot, chat_id, msg_id, username)

    elif data.startswith("adminfree:"):
        username = data[10:]
        current = get_admin_profile(username)["free_access"]
        action = "выключить" if current else "включить"
        markup = {"inline_keyboard": [[
            {"text": "Да", "callback_data": f"adminfreeok:{username}:{0 if current else 1}", "style": "danger" if current else "success"},
            {"text": "Нет", "callback_data": f"user:{username}"},
        ]]}
        bot.edit(chat_id, msg_id, f"{action.capitalize()} бесплатный доступ для <b>{html.escape(username)}</b>?", markup)

    elif data.startswith("adminfreeok:"):
        _, username, enabled_raw = data.split(":", 2)
        enabled = enabled_raw == "1"
        set_user_free_access(username, enabled, user_id)
        bot.answer_callback(cb_id, "бесплатный доступ " + ("включён" if enabled else "выключен"))
        show_user_info(bot, chat_id, msg_id, username)

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

    elif data.startswith("invapprove:"):
        invite_id = int(data[len("invapprove:"):])
        conn = sqlite3.connect(BOT_DB, timeout=30)
        cur = conn.execute(
            "UPDATE client_invites SET status='approved' WHERE id=? AND status='trial'",
            (invite_id,),
        )
        conn.commit()
        if not cur.rowcount:
            conn.close()
            bot.answer_callback(cb_id, "уже обработан")
            return
        conn.row_factory = sqlite3.Row
        invite_row = conn.execute("SELECT * FROM client_invites WHERE id=?", (invite_id,)).fetchone()
        conn.close()
        invite = dict(invite_row) if invite_row else {}
        try:
            log_invite_event(invite_id, "approve", {})
        except Exception:
            pass
        bot.edit(
            chat_id, msg_id,
            f"✅ одобрено: <b>{html.escape(invite.get('activated_username', ''))}</b> "
            f"(пригласил {html.escape(invite.get('inviter_username', ''))})",
        )
        activated_tg_id = int(invite.get("activated_tg_id") or 0)
        if activated_tg_id:
            _notify_client_bot(activated_tg_id, "оплата теперь доступна — можно оформить тариф")

    elif data.startswith("invban:"):
        invite_id = int(data[len("invban:"):])
        conn = sqlite3.connect(BOT_DB, timeout=30)
        cur = conn.execute(
            "UPDATE client_invites SET status='banned' WHERE id=? AND status NOT IN ('paid','banned','revoked','expired')",
            (invite_id,),
        )
        conn.commit()
        if not cur.rowcount:
            conn.close()
            bot.answer_callback(cb_id, "уже обработан")
            return
        conn.row_factory = sqlite3.Row
        invite_row = conn.execute("SELECT * FROM client_invites WHERE id=?", (invite_id,)).fetchone()
        conn.close()
        invite = dict(invite_row) if invite_row else {}
        try:
            log_invite_event(invite_id, "ban", {})
        except Exception:
            pass
        friend_username = invite.get("activated_username", "")
        if friend_username:
            try:
                remnawave_query(
                    f"update users set expire_at=now(), updated_at=now() where username={pg_quote(friend_username)};"
                )
            except Exception:
                pass
        bot.edit(
            chat_id, msg_id,
            f"⛔ забанен: <b>{html.escape(friend_username)}</b> "
            f"(пригласил {html.escape(invite.get('inviter_username', ''))})",
        )

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

    elif data.startswith("dnsip:"):
        arg = data[6:]
        if arg == "close":
            bot.edit(chat_id, msg_id, cb["message"].get("text", "закрыто"))
            return
        if arg == "replace":
            bot.answer_callback(cb_id, "замена IP пока через deploy/env, не из кнопки")
            return
        new_ip = arg
        if new_ip not in (CF_PRIMARY_IP, CF_BACKUP_IP):
            bot.answer_callback(cb_id, "❌ неизвестный IP")
            return
        current_ip, record_id = cf_get_dns_record()
        if not record_id:
            bot.answer_callback(cb_id, "❌ Cloudflare API недоступен")
            return
        if current_ip == new_ip:
            bot.answer_callback(cb_id, "уже активен")
            cmd_dnsip(bot, chat_id, msg_id)
            return
        ok = cf_set_dns_ip(record_id, new_ip)
        if ok:
            label = "primary" if new_ip == CF_PRIMARY_IP else "backup"
            bot.answer_callback(cb_id, f"✅ DNS переключён на {label}")
        else:
            bot.answer_callback(cb_id, "❌ ошибка Cloudflare API")
        cmd_dnsip(bot, chat_id, msg_id)
        return

    elif data.startswith("subserver:"):
        arg = data[10:]
        if arg == "close":
            bot.edit(chat_id, msg_id, cb["message"].get("text", "закрыто"))
            return
        if arg == "edit_url":
            cur = get_bot_setting(HYDRA_SUB_URL_KEY, HYDRA_SUB_URL_DEFAULT)
            bot.edit(chat_id, msg_id,
                f"🔗 <b>текущий URL подписки:</b>\n<code>{html.escape(cur)}</code>\n\n"
                "отправьте новый URL следующим сообщением\n"
                "или <code>-</code> чтобы сбросить на дефолт",
                {"inline_keyboard": [[{"text": "отмена  ↜", "callback_data": "subserver:cancel_edit"}]]}
            )
            sub_setting_state[chat_id] = {"key": HYDRA_SUB_URL_KEY, "msg_id": msg_id}
            return
        if arg == "edit_ua":
            cur = get_bot_setting(HYDRA_SUB_UA_KEY, HYDRA_SUB_UA_DEFAULT)
            bot.edit(chat_id, msg_id,
                f"🤖 <b>текущий User-Agent:</b>\n<code>{html.escape(cur)}</code>\n\n"
                "отправьте новый User-Agent следующим сообщением\n"
                "или <code>-</code> чтобы сбросить на дефолт",
                {"inline_keyboard": [[{"text": "отмена  ↜", "callback_data": "subserver:cancel_edit"}]]}
            )
            sub_setting_state[chat_id] = {"key": HYDRA_SUB_UA_KEY, "msg_id": msg_id}
            return
        if arg == "cancel_edit":
            sub_setting_state.pop(chat_id, None)
            text, markup = build_sub_keyboard(0)
            bot.edit(chat_id, msg_id, text, markup)
            return
        tag = arg
        if not tag.startswith("hydra-proxy-"):
            bot.answer_callback(cb_id, "❌ неизвестный тег")
            return
        status = dict(get_hydra_outbound_status())
        if tag not in status:
            bot.answer_callback(cb_id, "❌ сервер не найден в конфиге")
            return
        currently_on = status[tag]
        set_hydra_outbound_enabled(tag, not currently_on)
        xui_restart()
        regenerate_all_subs()
        action = "включён" if not currently_on else "выключен"
        bot.answer_callback(cb_id, f"{hydra_outbound_label(tag)} {action}")
        text, markup = build_sub_keyboard(0)
        bot.edit(chat_id, msg_id, text, markup)
        return

    elif data.startswith("subserverpage:"):
        page_str = data[14:]
        if page_str == "noop":
            bot.answer_callback(cb_id, "ты на этой странице 🙃")
            return
        text, markup = build_sub_keyboard(int(page_str))
        bot.edit(chat_id, msg_id, text, markup)
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
            v = _mask_wl_entry_uuid(e["vless"])
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
            for key, ib in list(INBOUNDS.items()) + list(HYDRA_INBOUNDS.items()):
                if key == "smart-pro" and username != "bozhenkas":
                    continue
                prefix = ib.get("prefix", "")
                email = f"{prefix}{username}"
                xui_remove_client(ib["id"], email)

            xui_restart()
            remnawave_delete_user(username)

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
    stored_rule = get_manual_domain_rule(BOT_DB, domain)
    if stored_rule:
        return stored_rule
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
    upsert_manual_domain_rule(BOT_DB, domain, rule)
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
    try:
        sync_remnawave_manual_routes(BOT_DB)
    except Exception as exc:
        print(f"[manual-routes] remnawave sync failed: {exc}")


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

    # sub setting edit (URL / UA)
    if chat_id in sub_setting_state:
        state = sub_setting_state.pop(chat_id)
        if not text:
            bot.send(chat_id, "❌ нужен текст")
            return
        key = state["key"]
        msg_id = state["msg_id"]
        if text == "-":
            value = HYDRA_SUB_URL_DEFAULT if key == HYDRA_SUB_URL_KEY else HYDRA_SUB_UA_DEFAULT
        else:
            value = text.strip()
        set_bot_setting(key, value)
        # синхронизируем оба значения в config.env для sub-updater
        url = get_bot_setting(HYDRA_SUB_URL_KEY, HYDRA_SUB_URL_DEFAULT)
        ua  = get_bot_setting(HYDRA_SUB_UA_KEY,  HYDRA_SUB_UA_DEFAULT)
        write_sub_updater_config(url, ua)
        label = "URL" if key == HYDRA_SUB_URL_KEY else "User-Agent"
        bot.send(chat_id, f"✅ {label} обновлён: <code>{html.escape(value)}</code>")
        text2, markup2 = build_sub_keyboard(0)
        bot.edit(chat_id, msg_id, text2, markup2)
        return

    if chat_id in admin_paid_state:
        state = admin_paid_state.pop(chat_id)
        username = state["user"]
        msg_id = state["message_id"]
        try:
            days = int(text.strip())
            if days <= 0 or days > 3650:
                raise ValueError
        except ValueError:
            bot.send(chat_id, "❌ нужно число дней от 1 до 3650")
            admin_paid_state[chat_id] = state
            return
        current = get_admin_profile(username)["paid_until"]
        base = datetime.utcnow().date()
        if current:
            try:
                base = max(base, datetime.strptime(current, "%Y-%m-%d").date())
            except ValueError:
                pass
        paid_until = (base + timedelta(days=days)).isoformat()
        set_user_paid_until(username, paid_until, user_id)
        bot.send(chat_id, f"✅ <b>{html.escape(username)}</b> продлён до <b>{paid_until}</b>")
        show_user_info(bot, chat_id, msg_id, username)
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

    elif text.startswith("/user"):
        parts = text.split(maxsplit=1)
        username = parts[1].strip() if len(parts) > 1 else ""
        if not username:
            bot.send(chat_id, "использование: /user &lt;username&gt;")
        elif not get_user(username):
            bot.send(chat_id, f"пользователь <b>{html.escape(username)}</b> не найден")
        else:
            sent = bot.send(chat_id, f"открываю карточку <b>{html.escape(username)}</b>...")
            show_user_info(bot, chat_id, sent.get("result", {}).get("message_id", 0), username)

    elif text.startswith("/devices"):
        parts = text.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        cmd_devices(bot, chat_id, arg)

    elif text == "/stats":
        cmd_stats(bot, chat_id)

    elif text == "/subdesc":
        cmd_subdesc(bot, chat_id)

    elif text.startswith("/adduser"):
        parts = text.split(maxsplit=1)
        name = parts[1].strip() if len(parts) > 1 else ""
        cmd_adduser(bot, chat_id, name)

    elif text.startswith("/readdrw"):
        parts = text.split(maxsplit=1)
        name = parts[1].strip() if len(parts) > 1 else ""
        cmd_readdrw(bot, chat_id, name)

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

    elif text in ("/dnsip", "/ip"):
        cmd_dnsip(bot, chat_id)

    elif text == "/rkn":
        cmd_rkn(bot, chat_id)

    elif text.startswith("/emergency"):
        parts = text.split(maxsplit=1)
        cmd_emergency(bot, chat_id, parts[1] if len(parts) > 1 else "")

    elif text == "/sub":
        cmd_sub(bot, chat_id)

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
    # подписки слушаем сразу — regen в фоне с паузой, иначе 502 и 9s+ timeout у Happ
    start_sub_server()

    def _deferred_regen():
        time.sleep(120)
        regenerate_all_subs()

    Thread(target=_deferred_regen, daemon=True).start()
    Thread(target=_refresh_wl_links_cache, daemon=True).start()

    # бот
    bot = TelegramBot(token)
    global _notify_bot
    _notify_bot = bot
    info = bot.api("getMe")
    if not info.get("ok"):
        print("telegram api недоступен, подписки остаются в degraded mode", file=sys.stderr)
    else:
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
