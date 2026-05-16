#!/bin/bash
set -e

echo "=== 1. Чистим и исправляем updater.py (Reality Support) ==="
cat << 'EOF' > /opt/sub-updater/updater.py
#!/usr/bin/env python3
import base64, json, logging, sqlite3, urllib.request, urllib.parse, os

SUB_URL  = "https://sub.whitestore.club/zPFyxgNrQGy2ekY7"
DB_PATH  = "/etc/x-ui/x-ui.db"
LOG_PATH = "/var/log/sub-updater-lekanta.log"
WL_FILE  = "/opt/sub-updater/whitelist_links.txt"
HWID     = "up8jf5kjyrzi0013"

logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

def parse_vless(link):
    try:
        p = urllib.parse.urlparse(link)
        netloc = p.netloc.split('@')
        uuid = netloc[0]
        host_port = netloc[1].split(':')
        addr = host_port[0]
        port = int(host_port[1])
        query = urllib.parse.parse_qs(p.query)
        
        outbound = {
            "protocol": "vless",
            "settings": {"vnext": [{"address": addr, "port": port, "users": [{"id": uuid, "encryption": "none"}]}]},
            "streamSettings": {
                "network": query.get("type", ["tcp"])[0],
                "security": query.get("security", ["none"])[0]
            }
        }
        
        if outbound["streamSettings"]["security"] == "reality":
            outbound["streamSettings"]["realitySettings"] = {
                "show": False,
                "fingerprint": query.get("fp", ["chrome"])[0],
                "serverName": query.get("sni", [""])[0],
                "publicKey": query.get("pbk", [""])[0],
                "shortId": query.get("sid", [""])[0],
                "spiderX": query.get("spx", [""])[0]
            }
        elif outbound["streamSettings"]["security"] == "tls":
            outbound["streamSettings"]["tlsSettings"] = {"serverName": query.get("sni", [""])[0]}
            
        if query.get("flow"): outbound["settings"]["vnext"][0]["users"][0]["flow"] = query.get("flow")[0]
        return outbound
    except: return None

def update():
    try:
        req = urllib.request.Request(SUB_URL, headers={"User-Agent": "v2box", "X-HWID": HWID})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
            try: raw = base64.b64decode(data).decode()
            except: raw = data.decode()
            links = [l.strip() for l in raw.splitlines() if l.strip()]

        wl_links, hydra_nodes = [], []
        wl_keywords = ["whitelist", "резерв", "smart", "🔐"]
        
        for link in links:
            remark = urllib.parse.unquote(link.split("#")[-1]).lower() if "#" in link else ""
            if any(k in remark for k in wl_keywords): wl_links.append(link)
            else: hydra_nodes.append(link)
        
        if not os.path.exists("/opt/sub-updater"): os.makedirs("/opt/sub-updater")
        with open(WL_FILE, "w") as f: f.write("\n".join(wl_links))
        
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
        if row:
            tmpl = json.loads(row[0])
            tmpl["outbounds"] = [ob for ob in tmpl.get("outbounds", []) if not ob.get("tag", "").startswith("hydra-")]
            for i, link in enumerate(hydra_nodes[:15]):
                cfg = parse_vless(link)
                if cfg:
                    cfg["tag"] = f"hydra-{i+1}"
                    tmpl["outbounds"].append(cfg)
            conn.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'", (json.dumps(tmpl),))
            conn.commit()
        conn.close()
    except Exception as e: logging.error(f"Error: {e}")

if __name__ == "__main__": update()
EOF

echo "=== 2. Чистим и исправляем vpn-bot.py (Happ Styles + Correct Logic) ==="
cat << 'EOF' > /root/vpn-bot/vpn-bot.py
#!/usr/bin/env python3
import socket as _socket
_orig_gai = _socket.getaddrinfo
def _ipv4_only(h, p, family=0, type=0, proto=0, flags=0): return _orig_gai(h, p, _socket.AF_INET, type, proto, flags)
_socket.getaddrinfo = _ipv4_only
try:
    import socks as _socks
    _socks.set_default_proxy(_socks.SOCKS5, "127.0.0.1", 9099)
    _socket.socket = _socks.socksocket
except: pass

import base64, json, os, secrets, sqlite3, subprocess, sys, time, uuid, urllib.request, urllib.parse
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

OWNER_ID = 294057781
DOMAIN   = "lekanta.ru"
BOT_DB   = "/root/vpn-bot/bot.db"
XUI_DB   = "/etc/x-ui/x-ui.db"
SUBS_DIR = "/root/vpn-bot/subscriptions"
SUB_PORT = 9090
WL_FILE  = "/opt/sub-updater/whitelist_links.txt"

INBOUNDS = {
    "smart":  {"port": 10003, "path": "/smart", "prefix": "", "label": "🔀 Smart"},
    "zapret": {"port": 10004, "path": "/ru",    "prefix": "zapret-", "label": "📺 Zapret"}
}
HYDRA_MAP = {
    "usa": {"port": 10011, "prefix": "h-usa-", "flag": "🇺🇸", "label": "USA"},
    "pol": {"port": 10012, "prefix": "h-pol-", "flag": "🇵🇱", "label": "Pol"},
    "tur": {"port": 10013, "prefix": "h-tur-", "flag": "🇹🇷", "label": "Tur"},
    "nl":  {"port": 10014, "prefix": "h-nl-",  "flag": "🇳🇱", "label": "NL"},
    "de":  {"port": 10015, "prefix": "h-de-",  "flag": "🇩🇪", "label": "Ger"},
    "fiws":{"port": 10016, "prefix": "h-fi-",  "flag": "🇫🇮", "label": "Fi-ws"}
}

HAPP_ROUTING_LINE = "happ://routing/onadd/eyJOYW1lIjoibGVrYW50YS5ydSDigJQgU21hcnQiLCJHbG9iYWxQcm94eSI6InRydWUiLCJSZW1vdGVETlNUeXBlIjoiRG9IIiwiUmVtb3RlRE5TRG9tYWluIjoiaHR0cHM6Ly9jbG91ZGZsYXJlLWRucy5jb20vZG5zLXF1ZXJ5IiwiUmVtb3RlRE5TSVAiOiIxLjEuMS4xIiwiRG9tZXN0aWNETlNUeXBlIjoiRG9IIiwiUmVtb3RlRE5TRG9tYWluIjoiaHR0cHM6Ly9kbnMueWFuZGV4LnJ1L2Rucy1xdWVyeSIsIkRvbWVzdGljRE5TSVAiOiI3Ny44OC44LjgiLCJEaXJlY3RTaXRlcyI6WyJnZW9zaXRlOmNhdGVnb3J5LXJ1IiwiZG9tYWluOnNiZXJiYW5rLnJ1IiwiZG9tYWluOnNicmYucnUiLCJkb21haW46c2Jlci5ydSIsImRvbWFpbjp0aW5rb2ZmLnJ1IiwiZG9tYWluOnRiYW5rLnJ1IiwiZG9tYWluOnZ0Yi5ydSIsImRvbWFpbjphbGZhYmFuay5ydSIsImRvbWFpbjpyYWlmZmVpc2VuLnJ1IiwiZG9tYWluOmdhenByb21iYW5rLnJ1IiwiZG9tYWluOmdvc3VzbHVnaS5ydSIsImRvbWFpbjplc2lhLmdvc3VzbHVnaS5ydSIsImRvbWFpbjpuYWxvZy5ydSIsImRvbWFpbjptb3MucnUiLCJkb21haW46Z292LnNwYi5ydSIsImRvbWFpbjp3aWxkYmVycmllcy5ydSIsImRvbWFpbjp3YmNkbi5ydSIsImRvbWFpbjp3Yi5ydSIsImRvbWFpbjp3YmJhc2tldC5ydSIsImRvbWFpbjp3YngucnUiLCJkb21haW46d2JzdGF0aWMubmV0IiwiZG9tYWluOm96b24ucnUiLCJkb21haW46b3pvbnVzZXJjb250ZW50LmNvbSIsImRvbWFpbjphdml0by5ydSIsImRvbWFpbjphdml0by5zdCIsImRvbWFpbjpnaXNtZXRlby5ydSIsImRvbWFpbjpyYW1ibGVyLnJ1IiwiZG9tYWluOnR1dHUucnUiLCJkb21haW46ZnVucGF5LmNvbSIsImRvbWFpbjptYW5nYWxpYi5vcmciXSwiRGlyZWN0SXAiOlsiZ2VvaXA6cnUiLCIxMC4wLjAuMC84IiwiMTcyLjE2LjAuMC8xMiIsIjE5Mi4xNjguMC4wLzE2IiwiMTY5LjI1NC4wLjAvMTYiLCIyMjQuMC4wLjAvNCIsIjI1NS4yNTUuMjU1LjI1NS8zMiJdLCJEb21haW5TdHJhdGVneSI6IklQSWZOb25NYXRjaCIsIkZha2VETlMiOiJmYWxzZSJ9"

def btn(t, c, s=None):
    b = {"text": t, "callback_data": c}
    if s: b["style"] = s
    return b

def get_user(n):
    conn = sqlite3.connect(BOT_DB)
    r = conn.execute("SELECT name, token, created_at, wl FROM users WHERE name=?", (n,)).fetchone()
    conn.close()
    return {"name": r[0], "token": r[1], "created_at": r[2], "wl": r[3]} if r else None

def xui_get_client(p, e):
    conn = sqlite3.connect(XUI_DB)
    row = conn.execute("SELECT settings FROM inbounds WHERE port=?", (p,)).fetchone()
    conn.close()
    if not row: return None
    for c in json.loads(row[0]).get("clients", []):
        if c.get("email") == e: return c
    return None

def xui_toggle_client(p, e, en):
    conn = sqlite3.connect(XUI_DB)
    row = conn.execute("SELECT settings, id FROM inbounds WHERE port=?", (p,)).fetchone()
    if not row: return
    s = json.loads(row[0]); ib_id = row[1]; cls = s.get("clients", [])
    found = False
    for c in cls:
        if c.get("email") == e: c["enable"] = en; found = True; break
    if not found and en:
        cls.append({"id": str(uuid.uuid4()), "email": e, "enable": True, "expiryTime": 0, "totalGB": 0, "limitIp": 0})
    s["clients"] = cls
    conn.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(s), ib_id))
    conn.commit(); conn.close()

def save_sub(n):
    u = get_user(n)
    if not u: return
    lines = [HAPP_ROUTING_LINE, "#profile-title: lekanta :)"]
    for k, ib in INBOUNDS.items():
        c = xui_get_client(ib['port'], f"{ib['prefix']}{n}")
        if c:
            p = urllib.parse.quote(ib['path']); r = urllib.parse.quote(f"{ib['label']}-{n}")
            lines.append(f"vless://{c['id']}@{DOMAIN}:443?type=ws&security=tls&sni={DOMAIN}&path={p}&host={DOMAIN}#{r}")
    for k, h in HYDRA_MAP.items():
        c = xui_get_client(h['port'], f"{h['prefix']}{n}")
        if c and c.get("enable"):
            r = urllib.parse.quote(f"Hydra-{h['label']}-{n}{h['flag']}")
            lines.append(f"vless://{c['id']}@{DOMAIN}:443?type=ws&security=tls&sni={DOMAIN}&path={urllib.parse.quote('/'+k+'-out')}&host={DOMAIN}#{r}")
    if u["wl"]:
        try:
            with open(WL_FILE) as f: lines.extend([l.strip() for l in f if l.strip()])
        except: pass
    with open(os.path.join(SUBS_DIR, u["token"]), "w") as f: f.write("\n".join(lines))

def show_user(bot, cid, mid, name):
    u = get_user(name); wl_on = u["wl"]
    txt = f"👤 <b>{name}</b>\n🔐 Whitelist: {'✅' if wl_on else '❌'}\n\n<code>https://{DOMAIN}/subscribe/{u['token']}</code>"
    kb = {"inline_keyboard": [[btn("🔄 Регенерация", f"regen:{name}", "primary"), btn(f"WL: {'✅ ВКЛ' if wl_on else '❌ ВЫКЛ'}", f"twl:{name}", "success" if wl_on else "danger")]]}
    h_row = []
    for k, h in HYDRA_MAP.items():
        c = xui_get_client(h["port"], f"{h['prefix']}{name}")
        is_on = c and c.get("enable")
        h_row.append(btn(f"{h['flag']} {h['label']}: {'✅' if is_on else '❌'}", f"th:{name}:{k}", "success" if is_on else "danger"))
        if len(h_row) == 2: kb["inline_keyboard"].append(h_row); h_row = []
    kb["inline_keyboard"].append([btn("❌ Удалить", f"del:{name}", "danger"), btn("← Назад", "back")])
    bot.api("editMessageText", {"chat_id": cid, "message_id": mid, "text": txt, "parse_mode": "HTML", "reply_markup": kb, "disable_web_page_preview": True})

class Bot:
    def __init__(self):
        self.token = ""; self.offset = 0
        try:
            with open("/root/vpn-bot/.env") as f:
                for l in f:
                    if l.startswith("BOT_TOKEN="): self.token = l.split("=")[1].strip().strip("'").strip('"')
        except: pass

    def api(self, m, d=None):
        try:
            req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{m}", json.dumps(d).encode() if d else None, {"Content-Type":"application/json"} if d else {})
            with urllib.request.urlopen(req, timeout=15) as r: return json.loads(r.read())
        except: return {}

def main():
    bot = Bot()
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            token = self.path.split("/")[-1]
            if token in os.listdir(SUBS_DIR):
                with open(os.path.join(SUBS_DIR, token)) as f: content = f.read()
                self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers()
                self.wfile.write(base64.b64encode(content.encode())); return
            self.send_response(404); self.end_headers()
    Thread(target=lambda: HTTPServer(("127.0.0.1", SUB_PORT), H).serve_forever(), daemon=True).start()
    print("Bot starting loop...")
    while True:
        try:
            updates = bot.api("getUpdates", {"offset": bot.offset, "timeout": 20}).get("result", [])
            for u in updates:
                bot.offset = u["update_id"] + 1
                if "message" in u and u["message"]["chat"]["id"] == OWNER_ID:
                    cid = u["message"]["chat"]["id"]; txt = u["message"].get("text", "")
                    if txt in ("/start", "/users"):
                        conn = sqlite3.connect(BOT_DB); usrs = conn.execute("SELECT name FROM users ORDER BY created_at").fetchall(); conn.close()
                        kb = {"inline_keyboard": [[btn(n[0], f"u:{n[0]}")] for n in usrs]}
                        bot.api("sendMessage", {"chat_id": cid, "text": "👥 Пользователи:", "reply_markup": kb})
                    elif txt.startswith("/adduser "):
                        name = txt.split(" ", 1)[1].strip()
                        if not get_user(name):
                            token = secrets.token_urlsafe(16); conn = sqlite3.connect(BOT_DB)
                            conn.execute("INSERT INTO users (name, token, created_at, wl) VALUES (?,?,?,0)", (name, token, datetime.now().isoformat())); conn.commit(); conn.close()
                            save_sub(name); bot.api("sendMessage", {"chat_id": cid, "text": f"✅ {name} создан"})
                elif "callback_query" in u:
                    cb = u["callback_query"]; cid = cb["message"]["chat"]["id"]; mid = cb["message"]["message_id"]; data = cb["data"]
                    bot.api("answerCallbackQuery", {"callback_query_id": cb["id"]})
                    if data.startswith("u:"): show_user(bot, cid, mid, data[2:])
                    elif data.startswith("twl:"):
                        n = data[4:]; u = get_user(n)
                        if u:
                            conn = sqlite3.connect(BOT_DB); conn.execute("UPDATE users SET wl=? WHERE name=?", (0 if u["wl"] else 1, n)); conn.commit(); conn.close()
                            save_sub(n); show_user(bot, cid, mid, n)
                    elif data.startswith("th:"):
                        n, node = data[3:].split(":"); h = HYDRA_MAP[node]; email = f"{h['prefix']}{n}"
                        c = xui_get_client(h["port"], email); xui_toggle_client(h["port"], email, False if (c and c.get("enable")) else True)
                        save_sub(n); subprocess.run(["x-ui", "restart"], capture_output=True); time.sleep(1); show_user(bot, cid, mid, n)
                    elif data == "back":
                        conn = sqlite3.connect(BOT_DB); usrs = conn.execute("SELECT name FROM users ORDER BY created_at").fetchall(); conn.close()
                        kb = {"inline_keyboard": [[btn(n[0], f"u:{n[0]}")] for n in usrs]}
                        bot.api("editMessageText", {"chat_id": cid, "message_id": mid, "text": "👥 Пользователи:", "reply_markup": kb})
        except: pass
        time.sleep(1)

if __name__ == "__main__": main()
EOF

echo "=== 3. Перезапуск узлов ==="
python3 /opt/sub-updater/updater.py
x-ui restart
systemctl restart vpn-bot
echo "Готово! Проверяй бота и VPN."