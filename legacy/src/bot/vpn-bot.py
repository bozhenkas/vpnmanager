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
import os
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

# === конфиг ===

OWNER_ID = 294057781
XUI_DB = "/etc/x-ui/x-ui.db"
BOT_DB = "/root/vpn-bot/bot.db"
SUBS_DIR = "/root/vpn-bot/subscriptions"
DOMAIN = "ru.goida.fun"
IP_LIMIT = 4
SERVER_IPS = {"83.147.255.98", "77.110.108.57", "89.22.230.5"}
SUB_PORT = 9090  # внутренний порт для подписок

# инбаунды на RU-сервере
INBOUNDS = {
    "fi":      {"id": 1,  "port": 10001, "path": "/fi",       "tag": "inbound-10001", "prefix": "fin-",    "remark_suffix": "🇫🇮"},
    "se":      {"id": 4,  "port": 10002, "path": "/se",       "tag": "inbound-10002", "prefix": "swe-",    "remark_suffix": "🇸🇪"},
    "smart":   {"id": 5,  "port": 10003, "path": "/smart",    "tag": "inbound-10003", "prefix": "",        "remark_suffix": ""},
    "smart-pro": {"id": 16, "port": 10005, "path": "/smart-pro", "tag": "inbound-10005", "prefix": "",    "remark_suffix": "⚡"},
    "zapret":  {"id": 6,  "port": 10004, "path": "/direct",     "tag": "inbound-10004", "prefix": "zapret-","remark_suffix": " (youtube/discord)"},
}

# hydra — сторонние серверы из подписки whitestore
HYDRA_INBOUNDS = {
    "usa": {"id": 7,  "port": 10011, "path": "/usa-out", "tag": "inbound-10011", "prefix": "usa-", "flag": "🇺🇸", "label": "usa"},
    "pol": {"id": 8,  "port": 10012, "path": "/pol-out", "tag": "inbound-10012", "prefix": "pol-", "flag": "🇵🇱", "label": "poland"},
    "tur": {"id": 9,  "port": 10013, "path": "/tur-out", "tag": "inbound-10013", "prefix": "tur-", "flag": "🇹🇷", "label": "turkey"},
    "nl":  {"id": 13, "port": 10014, "path": "/nl-out",  "tag": "inbound-10014", "prefix": "nl-",  "flag": "🇳🇱", "label": "netherlands"},
    "de":  {"id": 14, "port": 10015, "path": "/de-out",  "tag": "inbound-10015", "prefix": "de-",  "flag": "🇩🇪", "label": "germany"},
    "fiws":{"id": 15, "port": 10016, "path": "/fi-out",  "tag": "inbound-10016", "prefix": "fi2-", "flag": "🇫🇮", "label": "finland-ws"},
}

WL_FILE = "/opt/sub-updater/whitelist_links.txt"
HYSTERIA_LINK = "hysteria2://a5ab16e3a57158eec010e65eaa010dd5@77.110.108.57:8443/?sni=fin.goida.fun#hysteria2%F0%9F%87%AB%F0%9F%87%AE"

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
    conn = sqlite3.connect(BOT_DB)
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
    conn.commit()
    # миграция: добавляем user_agent если нет
    try:
        conn.execute("ALTER TABLE user_ips ADD COLUMN user_agent TEXT NOT NULL DEFAULT ''")
        conn.commit()
    except Exception:
        pass  # колонка уже есть
    conn.close()


def get_all_users() -> list[dict]:
    conn = sqlite3.connect(BOT_DB)
    rows = conn.execute("SELECT name, token, created_at FROM users ORDER BY created_at").fetchall()
    conn.close()
    return [{"name": r[0], "token": r[1], "created_at": r[2]} for r in rows]


def get_user(name: str) -> dict | None:
    conn = sqlite3.connect(BOT_DB)
    row = conn.execute("SELECT name, token, created_at, custom_sub FROM users WHERE name=?", (name,)).fetchone()
    conn.close()
    if row:
        return {"name": row[0], "token": row[1], "created_at": row[2], "custom_sub": row[3]}
    return None


def add_user_db(name: str) -> str:
    """добавляет пользователя в бот-бд, возвращает token"""
    token = secrets.token_urlsafe(32)
    created = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(BOT_DB)
    conn.execute("INSERT INTO users (name, token, created_at) VALUES (?, ?, ?)",
                 (name, token, created))
    conn.commit()
    conn.close()
    return token


def delete_user_db(name: str):
    conn = sqlite3.connect(BOT_DB)
    conn.execute("DELETE FROM users WHERE name=?", (name,))
    conn.commit()
    conn.close()


def set_custom_sub(name: str, content: str):
    conn = sqlite3.connect(BOT_DB)
    conn.execute("UPDATE users SET custom_sub=? WHERE name=?", (content, name))
    conn.commit()
    conn.close()


# === 3X-UI управление ===

def xui_get_inbound(inbound_id: int) -> dict | None:
    conn = sqlite3.connect(XUI_DB)
    row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return None


def xui_add_client(inbound_id: int, email: str, client_uuid: str):
    """добавляет клиента в inbound 3X-UI"""
    conn = sqlite3.connect(XUI_DB)
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
    conn = sqlite3.connect(XUI_DB)
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
    conn = sqlite3.connect(XUI_DB)
    rows = conn.execute(
        "SELECT inbound_id, up, down FROM client_traffics WHERE email LIKE ?",
        (f"%{email}%",)
    ).fetchall()
    conn.close()

    total_up = sum(r[1] for r in rows)
    total_down = sum(r[2] for r in rows)
    per_inbound = {str(r[0]): {"up": r[1], "down": r[2]} for r in rows}

    return {"total_up": total_up, "total_down": total_down, "per_inbound": per_inbound}


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
        remark = f"smart-{username}"
    elif inbound_key == "smart-pro":
        remark = f"smart-pro-{username}⚡"
    elif inbound_key == "zapret":
        remark = f"ru(tls)-zapret-{username} (youtube/discord)"
    elif inbound_key == "fi":
        remark = f"ru(tls)-fin-{username}{ib['remark_suffix']}"
    else:
        remark = f"ru(tls)-swe-{username}{ib['remark_suffix']}"

    remark_encoded = urllib.parse.quote(remark)

    return (
        f"vless://{client_uuid}@{DOMAIN}:443/"
        f"?type=ws&security=tls&sni={DOMAIN}"
        f"&path={path}&host={DOMAIN}"
        f"#{remark_encoded}"
    )


def generate_subscription(username: str) -> str:
    """генерирует содержимое подписки из текущих клиентов в 3X-UI"""
    lines = ["#profile-title: goida :)"]

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

    # добавляем hysteria если включена
    if hysteria_get_status(username):
        lines.append(HYSTERIA_LINK)

    # добавляем whitelist серверы если включены
    if wl_get_status(username):
        lines.extend(generate_wl_links())

    return "\n".join(lines)


def hydra_get_status(username: str) -> bool:
    """проверяет включён ли hydra для пользователя (по наличию enable=true клиента в nl-инбаунде)"""
    conn = sqlite3.connect(XUI_DB)
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
    conn = sqlite3.connect(XUI_DB)
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
    conn = sqlite3.connect(BOT_DB)
    row = conn.execute("SELECT wl FROM users WHERE name=?", (username,)).fetchone()
    conn.close()
    return bool(row[0]) if row and row[0] else False


def wl_set(username: str, enable: bool):
    conn = sqlite3.connect(BOT_DB)
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
    conn = sqlite3.connect(BOT_DB)
    row = conn.execute("SELECT hysteria FROM users WHERE name=?", (username,)).fetchone()
    conn.close()
    if row and row[0]:
        return bool(row[0])
    return False


def hysteria_set(username: str, enable: bool):
    conn = sqlite3.connect(BOT_DB)
    conn.execute("UPDATE users SET hysteria=? WHERE name=?", (1 if enable else 0, username))
    conn.commit()
    conn.close()


def generate_hydra_links(username: str) -> list:
    """генерирует vless-ссылки для hydra если клиент включён"""
    links = []
    conn = sqlite3.connect(XUI_DB)
    for key, ib in HYDRA_INBOUNDS.items():
        row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (ib["id"],)).fetchone()
        if not row:
            continue
        email = f"{ib['prefix']}{username}"
        for c in json.loads(row[0]).get("clients", []):
            if c.get("email") == email and c.get("enable", False):
                params = f"type=ws&security=tls&sni={DOMAIN}&path={ib['path']}&host={DOMAIN}"
                remark = urllib.parse.quote(f"ru(tls)-hydra-{key}-{username}{ib['flag']}")
                links.append(f"vless://{c['id']}@{DOMAIN}:443/?{params}#{remark}")
                break
    conn.close()
    return links


def hydra_get_status(username: str) -> bool:
    """проверяет включён ли hydra для пользователя (по наличию enable=true клиента в nl-инбаунде)"""
    conn = sqlite3.connect(XUI_DB)
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
    conn = sqlite3.connect(XUI_DB)
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

    try:
        conn = sqlite3.connect(BOT_DB)

        existing = conn.execute(
            "SELECT ip FROM user_ips WHERE token=? AND ip=?", (token, client_ip)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE user_ips SET last_seen=?, user_agent=? WHERE token=? AND ip=?",
                (now, ua, token, client_ip)
            )
        else:
            conn.execute(
                "INSERT INTO user_ips (token, ip, first_seen, last_seen, user_agent) VALUES (?, ?, ?, ?, ?)",
                (token, client_ip, now, now, ua)
            )

        conn.commit()

        # лимит смотрим из 3X-UI для конкретного пользователя
        user_limit = IP_LIMIT
        try:
            xui_conn = sqlite3.connect(XUI_DB)
            xui_row = xui_conn.execute(
                "SELECT settings FROM inbounds WHERE id=?", (INBOUNDS["smart"]["id"],)
            ).fetchone()
            xui_conn.close()
            if xui_row:
                token_to_email = sqlite3.connect(BOT_DB).execute(
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


class SubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        # /subscribe/<token>
        parts = self.path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "subscribe":
            token = parts[1]
            sub_path = os.path.join(SUBS_DIR, token)
            if os.path.exists(sub_path):
                with open(sub_path) as f:
                    content = f.read()

                # проверяем IP лимит — ищем пользователя по токену
                client_ip = self.headers.get("X-Real-IP", self.client_address[0])
                ua = self.headers.get("User-Agent", "")
                content = check_ip_limit(token, client_ip, content, ua)

                # base64 для совместимости с клиентами
                encoded = base64.b64encode(content.encode()).decode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Disposition", "inline")
                self.send_header("Profile-Update-Interval", "12")
                self.send_header("Subscription-Userinfo", "")
                self.end_headers()
                self.wfile.write(encoded.encode())
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


# === обработчики ===

# state для редактирования подписок
edit_state: dict = {}   # chat_id → {"user": name, "message_id": int}
edit_buffer: dict = {}  # chat_id → {"user": name, "parts": [...], "timer": Thread}
rename_state: dict = {}  # chat_id → {"user": name, "message_id": int}


def xui_rename_client(old_name: str, new_name: str):
    """переименовывает клиентов во всех инбаундах 3X-UI"""
    conn = sqlite3.connect(XUI_DB)
    all_inbounds = list(INBOUNDS.items()) + list(HYDRA_INBOUNDS.items())
    for key, ib in all_inbounds:
        row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (ib["id"],)).fetchone()
        if not row:
            continue
        s = json.loads(row[0])
        changed = False
        # email для этого инбаунда
        if key in INBOUNDS:
            if key == "smart":
                old_email, new_email = old_name, new_name
            elif key == "fi":
                old_email, new_email = f"fin-{old_name}", f"fin-{new_name}"
            else:
                old_email, new_email = f"swe-{old_name}", f"swe-{new_name}"
        else:
            prefix = ib["prefix"]
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
        "/xray — статус Xray\n"
        "/ping — проверка бота"
    )


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
    hysteria_count = sum(1 for u in users if hysteria_get_status(u["name"]))
    wl_count = sum(1 for u in users if wl_get_status(u["name"]))
    text = (
        f"👥 <b>пользователи</b> — {total} чел.\n🌍 hydra: {hydra_count}  ⚡ hysteria: {hysteria_count}  wl: {wl_count}"
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
        # генерируем uuid для каждого инбаунда
        uuids = {
            "fi": str(uuid.uuid4()),
            "se": str(uuid.uuid4()),
            "smart": str(uuid.uuid4()),
        }

        # добавляем клиентов в 3X-UI
        for key, ib in INBOUNDS.items():
            if key == "smart":
                email = name
            elif key == "fi":
                email = f"fin-{name}"
            else:
                email = f"swe-{name}"

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
            f"клиенты добавлены во все инбаунды, xray перезапущен."
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
        if key == "smart":
            email = username
        elif key == "fi":
            email = f"fin-{username}"
        else:
            email = f"swe-{username}"

        tr = xui_get_traffic(email)
        up = tr["total_up"]
        down = tr["total_down"]
        total_up += up
        total_down += down

        if up > 0 or down > 0:
            flag = {"fi": "🇫🇮", "se": "🇸🇪", "smart": "🔀"}.get(key, "")
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
        conn_xui_tr = sqlite3.connect(XUI_DB)
        for key, ib in HYDRA_INBOUNDS.items():
            email = f"{ib['prefix']}{username}"
            row = conn_xui_tr.execute(
                "SELECT up, down FROM client_traffics WHERE email=? AND inbound_id=?",
                (email, ib["id"])
            ).fetchone()
            if row and (row[0] > 0 or row[1] > 0):
                hydra_traffic_parts.append(f"  {ib['flag']} {key}: ↑{format_bytes(row[0])} ↓{format_bytes(row[1])}")
        conn_xui_tr.close()
        hysteria_on_display = hysteria_get_status(username)
        hydra_status = "🌍 hydra: вкл" + (" · ⚡ hysteria: вкл" if hysteria_on_display else "")
        text += f"\n{hydra_status}"
        if hydra_traffic_parts:
            text += "\n<blockquote expandable>" + "\n".join(hydra_traffic_parts) + "</blockquote>"

    text += f"\n📎 подписка:\n<code>{sub_url}</code>"

    # ip и лимит
    conn_ips = sqlite3.connect(BOT_DB)
    ip_rows = conn_ips.execute(
        "SELECT ip, last_seen, user_agent FROM user_ips WHERE token=? ORDER BY last_seen DESC",
        (user["token"],)
    ).fetchall()
    conn_ips.close()
    ip_count = len(ip_rows)

    # лимит у пользователя в 3X-UI (смотрим по smart-инбаунду)
    conn_xui = sqlite3.connect(XUI_DB)
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

    if ip_count > 0:
        ip_lines = "\n".join(
            f"{row[0]}  <i>{row[1][:16]}</i>  <code>{(row[2] or '')[:40]}</code>" for row in ip_rows
        )
        limit_str = "∞" if current_limit == 0 else str(current_limit)
        text += f"\n\n📱 устройств (IP): {ip_count}/{limit_str}\n<blockquote expandable>{ip_lines}</blockquote>"

    hydra_on = hydra_get_status(username)
    hysteria_on = hysteria_get_status(username)
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
            btn("hysteria: " + ("вкл" if hysteria_on else "выкл"), f"togglehysteria:{username}", "success" if hysteria_on else None),
        ],
        [
            btn("wl: " + ("вкл" if wl_get_status(username) else "выкл"), f"togglewl:{username}", "success" if wl_get_status(username) else None),
        ],
        [
            btn("удалить", f"delete:{username}", "danger"),
        ],
        [
            btn("назад  ↜", "back:users"),
        ],
    ]

    bot.edit(chat_id, message_id, text, {"inline_keyboard": buttons})


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

    if data.startswith("user:"):
        username = data[5:]
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

    elif data.startswith("edit:"):
        username = data[5:]
        content = get_subscription_content(username)
        if content:
            text = (
                f"✏️ редактирование подписки <b>{username}</b>\n\n"
                f"текущее содержимое:\n"
                f"<blockquote expandable><code>{content}</code></blockquote>\n"
                f"отправьте новое содержимое сообщением (или нажмите отмена)"
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
            conn_b = sqlite3.connect(BOT_DB)
            conn_b.execute("DELETE FROM user_ips WHERE token=?", (user["token"],))
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

    elif data.startswith("togglehysteria:"):
        username = data[15:]
        user = get_user(username)
        if user:
            currently_on = hysteria_get_status(username)
            hysteria_set(username, not currently_on)
            save_subscription(username)
            status = "включена" if not currently_on else "выключена"
            bot.answer_callback(cb_id, f"⚡ hysteria {status}")
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
            conn_xui = sqlite3.connect(XUI_DB)
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

    elif data.startswith("confirm_del:"):
        username = data[12:]
        try:
            # удаляем клиентов из 3X-UI
            for key, ib in INBOUNDS.items():
                if key == "smart":
                    email = username
                elif key == "fi":
                    email = f"fin-{username}"
                else:
                    email = f"swe-{username}"
                xui_remove_client(ib["id"], email)

            xui_restart()

            # удаляем файл подписки
            user = get_user(username)
            if user:
                sub_path = os.path.join(SUBS_DIR, user["token"])
                if os.path.exists(sub_path):
                    os.remove(sub_path)

            # удаляем из бот-бд
            delete_user_db(username)

            # возвращаемся к списку
            users = get_all_users()
            if users:
                buttons = [[{"text": u["name"], "callback_data": f"user:{u['name']}"}] for u in users]
                bot.edit(chat_id, msg_id,
                         f"✅ пользователь <b>{username}</b> удалён\n\n👥 <b>пользователи</b>",
                         {"inline_keyboard": buttons})
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
        conn = sqlite3.connect(XUI_DB)

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
        bot_conn = sqlite3.connect(BOT_DB)
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
    db = sqlite3.connect(XUI_DB)
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
    db = sqlite3.connect(XUI_DB)
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
                os.environ.get("DOMAINS_REPO_URL", "https://github.com/bozhenkas/russia-vpn-block-domains.git"),
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
    text = msg.get("text", "").strip()
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")

    if user_id != OWNER_ID:
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
        conn_b = sqlite3.connect(BOT_DB)
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
            full_text = "\n".join(buf["parts"])
            set_custom_sub(username, full_text)
            save_subscription(username, full_text)
            bot.send(cid, f"✅ подписка <b>{username}</b> обновлена", {
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

    elif text.startswith("/adduser"):
        parts = text.split(maxsplit=1)
        name = parts[1].strip() if len(parts) > 1 else ""
        cmd_adduser(bot, chat_id, name)

    elif text == "/xray":
        cmd_xray(bot, chat_id)

    elif text == "/ping":
        bot.send(chat_id, "🏓 pong")

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

def main():
    token = load_token()
    init_bot_db()

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
                elif "message" in upd and upd["message"].get("text"):
                    handle_message(bot, upd["message"])
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"ошибка: {e}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    main()
