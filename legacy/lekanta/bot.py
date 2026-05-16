#!/usr/bin/env python3
# --- патч: IPv4 only + SOCKS5 через xray socks-bot (9099) ---
import socket as _socket
_orig_gai = _socket.getaddrinfo
def _ipv4_only(h, p, family=0, type=0, proto=0, flags=0):
    return _orig_gai(h, p, _socket.AF_INET, type, proto, flags)
_socket.getaddrinfo = _ipv4_only
try:
    import socks as _socks
    _socks.set_default_proxy(_socks.SOCKS5, "127.0.0.1", 9099)
    _socket.socket = _socks.socksocket
except ImportError:
    pass
# --- конец патча ---

import base64, json, os, secrets, sqlite3, subprocess, sys, time, uuid
import urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread, Lock

# мьютекс для write-операций BOT_DB: SubHandler-thread (check_ip_limit) vs
# main-thread (cmd_adduser/delete) — иначе «database is locked» внутри процесса.
_BOT_DB_LOCK = Lock()

# x-ui рестарт ⇒ xray :9099 (наш SOCKS5 для bot.api) умирает на ~2с.
# ждём после рестарта чтобы bot.send/edit не упал в SOCKS5 connection refused.
XRAY_RESTART_WAIT = 3.0

# === КОНФИГ ===
# OWNER_IDS подгружается из .env (см. load_owner_ids ниже), дефолт — один владелец
OWNER_IDS: list = [294057781]
XUI_DB   = "/etc/x-ui/x-ui.db"
BOT_DB   = "/root/vpn-bot/bot.db"
SUBS_DIR = "/root/vpn-bot/subscriptions"
DOMAIN   = "lekanta.ru"
SUB_PORT = 9090
WL_FILE  = "/opt/sub-updater/whitelist_links.txt"
IP_LIMIT = 5  # лимит устройств (по HWID из UA Happ)

# инбаунды lekanta: smart → балансер-гидра, zapret → direct+nfqws
INBOUNDS = {
    "smart":  {"id": 1, "port": 10003, "path": "/smart", "tag": "inbound-10003",
               "prefix": ""},
    "zapret": {"id": 2, "port": 10004, "path": "/ru",    "tag": "inbound-10004",
               "prefix": "zapret-"},
}

# HAPP routing — копия RU бота, Name изменён на lekanta.ru
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

DOTENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _dotenv_get(key: str, default: str = "") -> str:
    if os.path.exists(DOTENV_PATH):
        with open(DOTENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get(key, default)


def load_token() -> str:
    token = _dotenv_get("BOT_TOKEN")
    if not token:
        print("BOT_TOKEN не найден", file=sys.stderr)
        sys.exit(1)
    return token


def load_owner_ids() -> list:
    # OWNER_IDS=12345,67890  (или OWNER_ID для обратной совместимости)
    raw = _dotenv_get("OWNER_IDS") or _dotenv_get("OWNER_ID")
    out = []
    for p in (raw or "").replace(";", ",").split(","):
        p = p.strip()
        if p.isdigit():
            out.append(int(p))
    return out or [294057781]


# === БД ===

def init_bot_db():
    os.makedirs(os.path.dirname(BOT_DB), exist_ok=True)
    os.makedirs(SUBS_DIR, exist_ok=True)
    conn = sqlite3.connect(BOT_DB, timeout=30)
    # WAL — снижает write-lock конфликты с другими процессами (например, sub-updater)
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
    # «надгробие» — токены удалённых юзеров остаются живыми, отдают stub-инбаунд
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deleted_subs (
            token TEXT PRIMARY KEY,
            deleted_at TEXT NOT NULL
        )
    """)
    conn.commit()
    # миграции
    for sql in [
        "ALTER TABLE users ADD COLUMN wl INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN hydra INTEGER DEFAULT 0",
        "ALTER TABLE user_ips ADD COLUMN user_agent TEXT NOT NULL DEFAULT ''",
    ]:
        try:
            conn.execute(sql); conn.commit()
        except Exception:
            pass
    conn.close()


def get_all_users() -> list:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    rows = conn.execute("SELECT name, token, created_at FROM users ORDER BY created_at").fetchall()
    conn.close()
    return [{"name": r[0], "token": r[1], "created_at": r[2]} for r in rows]


def get_user(name: str) -> dict | None:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    row = conn.execute("SELECT name, token, created_at, custom_sub, wl FROM users WHERE name=?", (name,)).fetchone()
    conn.close()
    if row:
        return {"name": row[0], "token": row[1], "created_at": row[2], "custom_sub": row[3], "wl": row[4] or 0}
    return None


def get_user_by_token(token: str) -> dict | None:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    row = conn.execute(
        "SELECT name, token, created_at, custom_sub, wl FROM users WHERE token=?", (token,)
    ).fetchone()
    conn.close()
    if row:
        return {"name": row[0], "token": row[1], "created_at": row[2], "custom_sub": row[3], "wl": row[4] or 0}
    return None


def add_user_db(name: str) -> str:
    token = secrets.token_urlsafe(32)
    with _BOT_DB_LOCK:
        conn = sqlite3.connect(BOT_DB, timeout=30)
        conn.execute("INSERT INTO users (name, token, created_at) VALUES (?, ?, ?)",
                     (name, token, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()
    return token


def delete_user_db(name: str):
    with _BOT_DB_LOCK:
        conn = sqlite3.connect(BOT_DB, timeout=30)
        conn.execute("DELETE FROM users WHERE name=?", (name,))
        conn.commit()
        conn.close()


def wl_get(name: str) -> bool:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    row = conn.execute("SELECT wl FROM users WHERE name=?", (name,)).fetchone()
    conn.close()
    return bool(row[0]) if row and row[0] else False


def wl_set(name: str, enable: bool):
    with _BOT_DB_LOCK:
        conn = sqlite3.connect(BOT_DB, timeout=30)
        conn.execute("UPDATE users SET wl=? WHERE name=?", (1 if enable else 0, name))
        conn.commit()
        conn.close()


def hydra_get(name: str) -> bool:
    conn = sqlite3.connect(BOT_DB, timeout=30)
    row = conn.execute("SELECT hydra FROM users WHERE name=?", (name,)).fetchone()
    conn.close()
    return bool(row[0]) if row and row[0] else False


def hydra_set(name: str, enable: bool):
    with _BOT_DB_LOCK:
        conn = sqlite3.connect(BOT_DB, timeout=30)
        conn.execute("UPDATE users SET hydra=? WHERE name=?", (1 if enable else 0, name))
        conn.commit()
        conn.close()


def set_custom_sub(name: str, content: str):
    with _BOT_DB_LOCK:
        conn = sqlite3.connect(BOT_DB, timeout=30)
        conn.execute("UPDATE users SET custom_sub=? WHERE name=?", (content, name))
        conn.commit()
        conn.close()


def mark_deleted_sub(token: str):
    with _BOT_DB_LOCK:
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


# === 3X-UI ===

def xui_add_client(inbound_id: int, email: str, client_uuid: str):
    conn = sqlite3.connect(XUI_DB, timeout=30)
    row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"inbound {inbound_id} не найден")
    s = json.loads(row[0])
    clients = s.get("clients", [])
    if any(c.get("email") == email for c in clients):
        conn.close()
        return
    clients.append({
        "id": client_uuid, "flow": "", "email": email,
        "limitIp": 0, "totalGB": 0, "expiryTime": 0,
        "enable": True, "tgId": "", "subId": "", "comment": "", "reset": 0
    })
    s["clients"] = clients
    conn.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(s), inbound_id))
    conn.execute(
        "INSERT OR IGNORE INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset) VALUES (?,1,?,0,0,0,0,0)",
        (inbound_id, email)
    )
    conn.commit()
    conn.close()


def xui_remove_client(inbound_id: int, email: str):
    conn = sqlite3.connect(XUI_DB, timeout=30)
    row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()
    if not row:
        conn.close()
        return
    s = json.loads(row[0])
    s["clients"] = [c for c in s.get("clients", []) if c.get("email") != email]
    conn.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(s), inbound_id))
    conn.commit()
    conn.close()


def xui_find_client(inbound_id: int, email: str) -> dict | None:
    conn = sqlite3.connect(XUI_DB, timeout=30)
    row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()
    conn.close()
    if not row: return None
    for c in json.loads(row[0]).get("clients", []):
        if c.get("email") == email:
            return c
    return None


def xui_get_traffic(email: str) -> dict:
    conn = sqlite3.connect(XUI_DB, timeout=30)
    rows = conn.execute("SELECT up, down FROM client_traffics WHERE email=?", (email,)).fetchall()
    conn.close()
    return {"up": sum(r[0] for r in rows), "down": sum(r[1] for r in rows)}


def xui_restart():
    subprocess.run(["systemctl", "restart", "x-ui"], capture_output=True, timeout=15)


def format_bytes(b: int) -> str:
    if b < 1024: return f"{b} B"
    if b < 1024**2: return f"{b/1024:.1f} KB"
    if b < 1024**3: return f"{b/1024**2:.1f} MB"
    return f"{b/1024**3:.2f} GB"


RU_MONTHS = {
    "01": "января", "02": "февраля", "03": "марта",   "04": "апреля",
    "05": "мая",    "06": "июня",    "07": "июля",    "08": "августа",
    "09": "сентября","10": "октября","11": "ноября",  "12": "декабря",
}

def format_gb(b: int) -> str:
    gb = b / 1024**3
    if gb >= 0.5: return f"{round(gb)} gb"
    mb = b / 1024**2
    if mb >= 1: return f"{round(mb)} mb"
    return f"{round(b/1024)} kb"

def format_date(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str)
    now = datetime.now(timezone.utc)
    m = RU_MONTHS[f"{dt.month:02d}"]
    return f"{dt.day} {m}" if dt.year == now.year else f"{dt.day} {m} {dt.year}"


# === ПОДПИСКИ ===

def generate_vless_link(client_uuid: str, ib_key: str, username: str) -> str:
    ib = INBOUNDS[ib_key]
    path = urllib.parse.quote(ib["path"])
    # имена в RU-формате (см. CLAUDE.md / ru bot)
    if ib_key == "smart":
        remark_raw = f"smart-{username} 🇸🇨"
    elif ib_key == "zapret":
        remark_raw = "ru-zapret (discord/youtube) 🇷🇺"
    else:
        remark_raw = f"{ib_key}-{username}"
    remark = urllib.parse.quote(remark_raw)
    return (
        f"vless://{client_uuid}@{DOMAIN}:443/"
        f"?type=ws&security=tls&sni={DOMAIN}&path={path}&host={DOMAIN}"
        f"#{remark}"
    )


def _split_flag_and_name(remark: str) -> tuple:
    """разбирает remark вида '🇳🇱 Нидерланды', '🇩🇪Германия-2' → (flag, name).
    флаг = первые 2 regional-indicator кодпойнта (U+1F1E6..U+1F1FF)."""
    if not remark:
        return ("", remark or "")
    code_points = [c for c in remark]
    # первые два символа должны быть RIS
    flag = ""
    rest = remark
    if len(code_points) >= 2:
        c1, c2 = code_points[0], code_points[1]
        if 0x1F1E6 <= ord(c1) <= 0x1F1FF and 0x1F1E6 <= ord(c2) <= 0x1F1FF:
            flag = c1 + c2
            rest = remark[2:].lstrip()
    return (flag, rest)


def generate_hydra_links(username: str) -> list:
    """генерирует vless ws+tls ссылки через наши hydra inbound'ы на lekanta.
    переформатирует remark: '🇳🇱 Нидерланды' → 'Нидерланды (hydra) 🇳🇱'."""
    links = []
    try:
        conn = sqlite3.connect(XUI_DB, timeout=30)
        rows = conn.execute(
            "SELECT remark, settings, stream_settings FROM inbounds "
            "WHERE tag LIKE 'inbound-hydra-%' ORDER BY tag"
        ).fetchall()
        conn.close()
        for remark, settings_str, stream_str in rows:
            clients = json.loads(settings_str).get("clients", [])
            client  = next((c for c in clients if c.get("email") == username), None)
            if not client:
                continue
            uid  = client["id"]
            path = urllib.parse.quote(
                json.loads(stream_str).get("wsSettings", {}).get("path", "/")
            )
            flag, name = _split_flag_and_name(remark)
            if flag and name:
                new_remark = f"{name} (hydra) {flag}"
            else:
                new_remark = remark
            r = urllib.parse.quote(new_remark)
            links.append(
                f"vless://{uid}@{DOMAIN}:443/"
                f"?type=ws&security=tls&sni={DOMAIN}&path={path}&host={DOMAIN}"
                f"#{r}"
            )
    except Exception as e:
        print(f"[hydra links] {e}", file=sys.stderr)
    return links


def xui_sync_hydra_clients(username: str, client_uuid: str):
    """добавляет клиента во все hydra inbound'ы (один и тот же uuid)."""
    conn = sqlite3.connect(XUI_DB, timeout=30)
    rows = conn.execute("SELECT id, settings FROM inbounds WHERE tag LIKE 'inbound-hydra-%'").fetchall()
    for ib_id, settings_str in rows:
        s = json.loads(settings_str)
        clients = s.get("clients", [])
        if any(c.get("email") == username for c in clients):
            continue
        clients.append({
            "id": client_uuid, "flow": "", "email": username,
            "limitIp": 0, "totalGB": 0, "expiryTime": 0,
            "enable": True, "tgId": "", "subId": "", "comment": "", "reset": 0,
        })
        s["clients"] = clients
        conn.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(s), ib_id))
        conn.execute(
            "INSERT OR IGNORE INTO client_traffics "
            "(inbound_id,enable,email,up,down,expiry_time,total,reset) VALUES (?,1,?,0,0,0,0,0)",
            (ib_id, username),
        )
    conn.commit()
    conn.close()


# UUID, под которым lekanta сама подключается к whitestore (источник подписки).
# В xrayTemplateConfig у всех hydra-* outbound'ов указан один и тот же UUID — это HWID-токен подписки.
# Для WL-ссылок (которые юзер использует напрямую) надо подменить «случайный» UUID из whitelist_links.txt
# на этот общий HWID — иначе сервер whitestore банит чужие UUID.
import re as _re
_WL_UID_CACHE = {"uid": None, "ts": 0.0}
_WL_UID_TTL = 60.0  # секунд

def _get_lekanta_wl_uid():
    """возвращает UUID из первого hydra-N outbound в xrayTemplateConfig, кешируется на 60с"""
    now = time.time()
    if _WL_UID_CACHE["uid"] and now - _WL_UID_CACHE["ts"] < _WL_UID_TTL:
        return _WL_UID_CACHE["uid"]
    try:
        conn = sqlite3.connect(XUI_DB, timeout=30)
        row = conn.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
        conn.close()
        if not row:
            return None
        cfg = json.loads(row[0])
        # ищем outbound с tag начинающимся на "hydra-" — берём первый (hydra-1)
        hydra_outs = sorted(
            [o for o in cfg.get("outbounds", []) if str(o.get("tag", "")).startswith("hydra-")],
            key=lambda o: o["tag"],
        )
        if not hydra_outs:
            return None
        uid = hydra_outs[0].get("settings", {}).get("vnext", [{}])[0].get("users", [{}])[0].get("id")
        if uid:
            _WL_UID_CACHE["uid"] = uid
            _WL_UID_CACHE["ts"] = now
        return uid
    except Exception as e:
        print(f"_get_lekanta_wl_uid err: {e}", file=sys.stderr)
        return None


_WL_UUID_RE = _re.compile(r'vless://[0-9a-fA-F-]{36}@')

def _patch_wl_link_uid(line: str, uid: str) -> str:
    """меняет UUID в vless://<uuid>@... на переданный uid"""
    return _WL_UUID_RE.sub(f"vless://{uid}@", line, count=1)


def generate_subscription(username: str) -> str:
    lines = [HAPP_ROUTING_LINE, "#profile-title: lekanta :)"]
    for key, ib in INBOUNDS.items():
        email = f"{ib['prefix']}{username}"
        client = xui_find_client(ib["id"], email)
        if client:
            lines.append(generate_vless_link(client["id"], key, username))
    if hydra_get(username):
        lines.extend(generate_hydra_links(username))
    if wl_get(username):
        try:
            with open(WL_FILE) as f:
                wl_lines = [l.strip() for l in f if l.strip()]
            wl_uid = _get_lekanta_wl_uid()
            if wl_uid:
                wl_lines = [
                    _patch_wl_link_uid(l, wl_uid) if l.startswith("vless://") else l
                    for l in wl_lines
                ]
            lines.extend(wl_lines)
        except FileNotFoundError:
            pass

    # дописываем доп.ссылки из custom_sub (только append, не подменяет)
    user_row = get_user(username)
    if user_row and user_row.get("custom_sub"):
        for extra in user_row["custom_sub"].split("\n"):
            extra = extra.strip()
            if extra:
                lines.append(extra)

    return "\n".join(lines)


def save_subscription(username: str, content: str | None = None):
    user = get_user(username)
    if not user: return
    if content is None:
        content = generate_subscription(username)
    sub_path = os.path.join(SUBS_DIR, user["token"])
    with open(sub_path, "w") as f:
        f.write(content)


def get_subscription_content(username: str) -> str | None:
    user = get_user(username)
    if not user: return None
    sub_path = os.path.join(SUBS_DIR, user["token"])
    if os.path.exists(sub_path):
        with open(sub_path) as f: return f.read()
    content = generate_subscription(username)
    save_subscription(username, content)
    return content


# === IP/HWID ЛИМИТ (копия логики с RU) ===

def get_device_id(ip: str, ua: str) -> str:
    if ua and ua.startswith("Happ/"):
        parts = ua.split("/")
        if len(parts) >= 4:
            return f"hwid:{parts[3].strip()}"
    return f"ip:{ip}"


def check_ip_limit(token: str, client_ip: str, content: str, user_agent: str = "") -> str:
    now = datetime.now(timezone.utc).isoformat()
    ua = (user_agent or "")[:200]
    device_id = get_device_id(client_ip, user_agent)
    count = 0
    try:
        # write-секция под глобальным lock — иначе SubHandler-thread конкурирует
        # с main-thread cmd_adduser и /adduser падает с "database is locked"
        with _BOT_DB_LOCK:
            conn = sqlite3.connect(BOT_DB, timeout=30)
            try:
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
                        "INSERT INTO user_ips (token, ip, first_seen, last_seen, user_agent) VALUES (?,?,?,?,?)",
                        (token, device_id, now, now, ua)
                    )
                conn.commit()
                count = conn.execute("SELECT COUNT(*) FROM user_ips WHERE token=?", (token,)).fetchone()[0]
            finally:
                conn.close()
        if count > IP_LIMIT:
            stub_remark = urllib.parse.quote(f"⚠️ лимит: {IP_LIMIT} устройств. ошибка → @bozhenkas")
            stub = f"vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443/?type=tcp&security=none#{stub_remark}"
            return f"#profile-title: lekanta — лимит превышен\n{stub}"
    except Exception:
        pass
    return content


# === HTTP-СЕРВЕР ПОДПИСОК ===

class SubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    # клиент часто рвёт соединение сразу после получения тела — глушим шумные трейсбеки
    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        try:
            parts = self.path.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "subscribe":
                token = parts[1]
                user = get_user_by_token(token)
                if user:
                    username = user["name"]
                    # custom_sub теперь — append-only доп.ссылки внутри generate_subscription
                    content = generate_subscription(username)
                    client_ip = self.headers.get("X-Real-IP", self.client_address[0])
                    ua = self.headers.get("User-Agent", "")
                    content = check_ip_limit(token, client_ip, content, ua)
                    encoded = base64.b64encode(content.encode()).decode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Profile-Update-Interval", "2")
                    self.send_header("routing", HAPP_ROUTING_LINE)
                    self.send_header("Subscription-Userinfo", "")
                    self.end_headers()
                    self.wfile.write(encoded.encode())
                    return
                if is_deleted_sub(token):
                    encoded = base64.b64encode(deleted_sub_content().encode()).decode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Profile-Update-Interval", "2")
                    self.end_headers()
                    self.wfile.write(encoded.encode())
                    return
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>404</h1></body></html>")
        except (BrokenPipeError, ConnectionResetError):
            # клиент закрыл сокет — игнорируем
            pass


def start_sub_server():
    server = HTTPServer(("127.0.0.1", SUB_PORT), SubHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    print(f"sub server on 127.0.0.1:{SUB_PORT}", file=sys.stderr)


# === TELEGRAM API ===

class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"
        self.offset = 0

    def api(self, method: str, data: dict = None) -> dict:
        url = f"{self.base}/{method}"
        if data:
            req = urllib.request.Request(url, json.dumps(data).encode(),
                                         {"Content-Type": "application/json"})
        else:
            req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=35) as r:
                return json.loads(r.read())
        except Exception as e:
            print(f"tg api error: {e}", file=sys.stderr)
            return {}

    def get_updates(self) -> list:
        result = self.api("getUpdates", {"offset": self.offset, "timeout": 20,
                                          "allowed_updates": ["message", "callback_query"]})
        updates = result.get("result", [])
        if updates:
            self.offset = updates[-1]["update_id"] + 1
        return updates

    def send(self, chat_id: int, text: str, reply_markup: dict = None,
             parse_mode: str = "HTML") -> dict:
        data = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode,
                "disable_web_page_preview": True}
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self.api("sendMessage", data)

    def edit(self, chat_id: int, message_id: int, text: str,
             reply_markup: dict = None, parse_mode: str = "HTML") -> dict:
        data = {"chat_id": chat_id, "message_id": message_id, "text": text,
                "parse_mode": parse_mode, "disable_web_page_preview": True}
        if reply_markup:
            data["reply_markup"] = reply_markup
        return self.api("editMessageText", data)

    def answer_callback(self, cb_id: str, text: str = ""):
        self.api("answerCallbackQuery", {"callback_query_id": cb_id, "text": text})


# === ОБРАБОТЧИКИ ===

edit_state: dict = {}
edit_buffer: dict = {}

PAGE_SIZE = 5


def btn(text, cb, style=None):
    b = {"text": text, "callback_data": cb}
    if style:
        b["style"] = style
    return b


def build_users_keyboard(users: list, page: int) -> tuple:
    total = len(users)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = users[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    hydra_count = sum(1 for u in users if hydra_get(u["name"]))
    wl_count    = sum(1 for u in users if wl_get(u["name"]))
    text = f"пользователи — {total}\n🌍 hydra — {hydra_count}  💬 whitelist — {wl_count}"
    buttons = [[{"text": u["name"], "callback_data": f"user:{u['name']}"}] for u in chunk]
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append({"text": "←", "callback_data": f"userspage:{page-1}"})
        nav.append({"text": f"{page+1}/{total_pages}", "callback_data": "userspage:noop"})
        if page < total_pages - 1:
            nav.append({"text": "→", "callback_data": f"userspage:{page+1}"})
        buttons.append(nav)
    return text, {"inline_keyboard": buttons}


def show_user_info(bot: TelegramBot, chat_id: int, message_id: int, username: str):
    user = get_user(username)
    if not user:
        bot.edit(chat_id, message_id, f"пользователь {username} не найден")
        return

    LABELS = {"smart": "🧠 smart", "zapret": "📺 zapret"}
    server_lines = []
    total_up = total_down = 0
    for key, ib in INBOUNDS.items():
        email = f"{ib['prefix']}{username}"
        tr = xui_get_traffic(email)
        total_up   += tr["up"]
        total_down += tr["down"]
        if tr["up"] > 0 or tr["down"] > 0:
            server_lines.append(f"  {LABELS.get(key, key)}: ↓{format_gb(tr['down'])} ↑{format_gb(tr['up'])}")

    sub_url  = f"https://{DOMAIN}/subscribe/{user['token']}"
    created  = format_date(user["created_at"])
    wl_on    = wl_get(username)
    hydra_on = hydra_get(username)

    text = f"👤 <b>{username}</b>\n\nсоздан: {created}\n"

    if total_up > 0 or total_down > 0:
        text += f"\nтрафик:  ↑{format_gb(total_up)}  ↓{format_gb(total_down)}\n"
        if server_lines:
            text += f"<blockquote expandable>{chr(10).join(server_lines)}</blockquote>\n"

    text += f"\n🔗 <b>подписка:</b>\n<code>{sub_url}</code>"

    buttons = [
        [btn("редактировать", f"edit:{username}"),
         btn("перевыпустить", f"confirm_regen:{username}")],
        [btn(f"hydra: {'вкл' if hydra_on else 'выкл'}", f"togglehydra:{username}",
             "success" if hydra_on else None),
         btn(f"wl: {'вкл' if wl_on else 'выкл'}", f"togglewl:{username}",
             "success" if wl_on else None)],
        [btn("удалить", f"delete:{username}", "danger")],
        [btn("назад  ↜", "back:users")],
    ]
    bot.edit(chat_id, message_id, text, {"inline_keyboard": buttons})


def cmd_adduser(bot: TelegramBot, chat_id: int, name: str):
    if not name:
        bot.send(chat_id, "использование: /adduser &lt;name&gt;")
        return
    if get_user(name):
        bot.send(chat_id, f"пользователь <b>{name}</b> уже существует")
        return

    bot.send(chat_id, f"⏳ создаю пользователя <b>{name}</b>...")
    try:
        smart_uuid  = str(uuid.uuid4())
        zapret_uuid = str(uuid.uuid4())
        xui_add_client(INBOUNDS["smart"]["id"],  name,             smart_uuid)
        xui_add_client(INBOUNDS["zapret"]["id"], f"zapret-{name}", zapret_uuid)
        xui_sync_hydra_clients(name, smart_uuid)
        xui_restart()
        time.sleep(XRAY_RESTART_WAIT)  # ждём пока xray :9099 (SOCKS5 для bot.api) поднимется
        token = add_user_db(name)
        save_subscription(name)
        sub_url = f"https://{DOMAIN}/subscribe/{token}"
        bot.send(chat_id,
            f"✅ пользователь <b>{name}</b> создан\n\n"
            f"📎 подписка:\n<code>{sub_url}</code>",
            {"inline_keyboard": [[
                {"text": "👥 перейти к пользователям", "callback_data": "back:users"}
            ]]}
        )
    except Exception as e:
        print(f"[adduser] {name} fail: {type(e).__name__}: {e}", file=sys.stderr)
        bot.send(chat_id, f"❌ ошибка: {e}")


def handle_callback(bot: TelegramBot, cb: dict):
    cb_id  = cb["id"]
    data   = cb.get("data", "")
    chat_id = cb["message"]["chat"]["id"]
    msg_id  = cb["message"]["message_id"]

    if cb["from"]["id"] not in OWNER_IDS:
        bot.answer_callback(cb_id, "⛔"); return

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
        show_user_info(bot, chat_id, msg_id, data[5:])

    elif data == "back:users":
        users = get_all_users()
        text, markup = build_users_keyboard(users, 0)
        bot.edit(chat_id, msg_id, text, markup)

    elif data.startswith("userspage:"):
        p = data[10:]
        if p == "noop":
            return
        users = get_all_users()
        text, markup = build_users_keyboard(users, int(p))
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
            buttons = [[{"text": "отмена  ↜", "callback_data": f"user:{username}"}]]
            bot.edit(chat_id, msg_id, text, {"inline_keyboard": buttons})
            edit_state[chat_id] = {"user": username, "message_id": msg_id}

    elif data.startswith("confirm_regen:"):
        username = data[14:]
        buttons = [[
            btn("перевыпустить", f"regen:{username}", "danger"),
            btn("отмена  ↜",     f"user:{username}",  "primary"),
        ]]
        bot.edit(chat_id, msg_id, f"перевыпустить подписку <b>{username}</b>?",
                 {"inline_keyboard": buttons})

    elif data.startswith("regen:"):
        username = data[6:]
        save_subscription(username)
        bot.answer_callback(cb_id, "✅ перевыпущено")
        show_user_info(bot, chat_id, msg_id, username)

    elif data.startswith("confirm_resetip:"):
        username = data[16:]
        buttons = [[
            {"text": "сбросить IP", "callback_data": f"resetip:{username}"},
            {"text": "отмена  ↜",   "callback_data": f"user:{username}"},
        ]]
        bot.edit(chat_id, msg_id, f"сбросить все IP для <b>{username}</b>?",
                 {"inline_keyboard": buttons})

    elif data.startswith("resetip:"):
        username = data[8:]
        user = get_user(username)
        if user:
            with _BOT_DB_LOCK:
                conn = sqlite3.connect(BOT_DB, timeout=30)
                conn.execute("DELETE FROM user_ips WHERE token=?", (user["token"],))
                conn.commit(); conn.close()
        bot.answer_callback(cb_id, "✅ IP сброшены")
        show_user_info(bot, chat_id, msg_id, username)

    elif data.startswith("togglehydra:"):
        username = data[12:]
        currently_on = hydra_get(username)
        hydra_set(username, not currently_on)
        save_subscription(username)
        bot.answer_callback(cb_id, "hydra " + ("включена" if not currently_on else "выключена"))
        show_user_info(bot, chat_id, msg_id, username)

    elif data.startswith("togglewl:"):
        username = data[9:]
        currently_on = wl_get(username)
        wl_set(username, not currently_on)
        save_subscription(username)
        bot.answer_callback(cb_id, "whitelist " + ("включён" if not currently_on else "выключен"))
        show_user_info(bot, chat_id, msg_id, username)

    elif data.startswith("delete:"):
        username = data[7:]
        buttons = [[
            btn("удалить",   f"confirm_del:{username}", "danger"),
            btn("отмена  ↜", f"user:{username}",        "primary"),
        ]]
        bot.edit(chat_id, msg_id,
                 f"удалить пользователя <b>{username}</b>?\n"
                 f"клиенты будут удалены из всех инбаундов.",
                 {"inline_keyboard": buttons})

    elif data.startswith("confirm_del:"):
        username = data[12:]
        try:
            xui_remove_client(INBOUNDS["smart"]["id"],  username)
            xui_remove_client(INBOUNDS["zapret"]["id"], f"zapret-{username}")
            xui_restart()
            time.sleep(XRAY_RESTART_WAIT)  # ждём SOCKS5 :9099 чтобы bot.edit не упал
            user = get_user(username)
            if user:
                # «надгробие»: токен живёт, отдаёт stub-инбаунд «пользователь удалён»
                mark_deleted_sub(user["token"])
                sub_path = os.path.join(SUBS_DIR, user["token"])
                try:
                    with open(sub_path, "w") as f:
                        f.write(deleted_sub_content())
                except Exception:
                    pass
            delete_user_db(username)
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


def handle_message(bot: TelegramBot, msg: dict):
    text    = msg.get("text", "").strip()
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")

    if user_id not in OWNER_IDS:
        return

    # state редактирования подписки
    if chat_id in edit_state or chat_id in edit_buffer:
        import threading
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
            if not buf: return
            username = buf["user"]
            full_text = "\n".join(buf["parts"]).strip()
            # "-" → очистить custom_sub
            if full_text == "-":
                full_text = ""
            set_custom_sub(username, full_text)
            # перегенерируем подписку с новыми custom_sub-доп.ссылками
            save_subscription(username)
            label = "очищены" if not full_text else "обновлены"
            bot.send(cid, f"✅ доп.ссылки <b>{username}</b> {label}",
                     {"inline_keyboard": [[
                         {"text": "← карточка",  "callback_data": f"user:{username}"},
                         {"text": "к списку  ↜", "callback_data": "back:users"},
                     ]]})

        timer = threading.Timer(3.0, flush, args=[chat_id])
        buf["timer"] = timer
        timer.start()
        return

    if text in ("/start", "/help"):
        bot.send(chat_id,
            "🌐 <b>vpn bot — lekanta.ru</b>\n\n"
            "/users — список пользователей\n"
            "/adduser &lt;name&gt; — добавить пользователя\n"
            "/ping — проверка бота"
        )
    elif text == "/users":
        users = get_all_users()
        if not users:
            bot.send(chat_id, "пользователей нет. /adduser &lt;name&gt; для добавления")
        else:
            text_out, markup = build_users_keyboard(users, 0)
            bot.send(chat_id, text_out, markup)
    elif text.startswith("/adduser"):
        parts = text.split(maxsplit=1)
        cmd_adduser(bot, chat_id, parts[1].strip() if len(parts) > 1 else "")
    elif text == "/ping":
        bot.send(chat_id, "🏓 pong")
    elif text == "/restart":
        bot.send(chat_id, "⏳ перезапускаю x-ui...")
        r = subprocess.run(["systemctl", "restart", "x-ui"], capture_output=True)
        bot.send(chat_id, "✅ x-ui перезапущен" if r.returncode == 0 else f"❌ {r.stderr.decode()}")


# === MAIN ===

def regenerate_all_subs():
    conn = sqlite3.connect(BOT_DB, timeout=30)
    rows = conn.execute("SELECT name FROM users ORDER BY created_at").fetchall()
    conn.close()
    for (name,) in rows:
        try:
            save_subscription(name)
        except Exception as e:
            print(f"[regen] {name}: {e}", file=sys.stderr)
    if rows:
        print(f"[regen] обновлено {len(rows)} подписок", file=sys.stderr)


def main():
    global OWNER_IDS
    token = load_token()
    OWNER_IDS = load_owner_ids()
    print(f"OWNER_IDS: {OWNER_IDS}", file=sys.stderr)
    init_bot_db()
    regenerate_all_subs()
    start_sub_server()

    bot = TelegramBot(token)
    info = {}
    for attempt in range(5):
        info = bot.api("getMe")
        if info.get("ok"):
            break
        print(f"getMe попытка {attempt+1}/5 не удалась, ждём...", file=sys.stderr)
        time.sleep(10)
    if not info.get("ok"):
        print("не удалось подключиться к Telegram API после 5 попыток", file=sys.stderr)
        sys.exit(1)

    username = info["result"]["username"]
    print(f"бот запущен: @{username}", file=sys.stderr)

    while True:
        try:
            updates = bot.get_updates()
            for upd in updates:
                if "callback_query" in upd:
                    handle_callback(bot, upd["callback_query"])
                elif "message" in upd and upd["message"].get("text"):
                    handle_message(bot, upd["message"])
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"ошибка: {e}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    main()
