#!/usr/bin/env python3
import json
import sqlite3
import subprocess
import urllib.request
import urllib.parse
import base64
import logging
import time
import re
import requests
import urllib3

# Отключаем ворнинги на самоподписанный сертификат панели
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SUB_URL = "https://sub.whitestore.club/Mk93Kvj6vcJMUakG"
HWID = "f7bdgmo86aik45lc"
DB_PATH = "/etc/x-ui/x-ui.db"
LOG_PATH = "/var/log/sub-updater.log"
INTERVAL = 600

# Данные для доступа к API панели
PANEL_URL = "https://127.0.0.1:25565/penis"
PANEL_USER = "bozhenkas"
PANEL_PASS = "75aqtyqQUxfC7C9"

# Фиксированный UUID для твоих Inbound'ов (чтобы ссылки не менялись)
CLIENT_UUID = "e16a2d64-a690-41da-848e-d9cf5ff1a100"

WL_FILE = "/opt/sub-updater/whitelist_links.txt"

# ключевые слова для whitelist серверов
WL_KEYWORDS = ["Whitelist", "РЕЗЕРВ"]

TARGETS = {
    "США": {"out_tag": "proxy-usa", "port": 10011, "remark": "USA-Out", "path": "/usa-out"},
    "Польша": {"out_tag": "proxy-pol", "port": 10012, "remark": "POL-Out", "path": "/pol-out"},
    "Турция": {"out_tag": "proxy-tur", "port": 10013, "remark": "TUR-Out", "path": "/tur-out"},
    "Нидерланды": {"out_tag": "proxy-nl", "port": 10014, "remark": "NL-Out", "path": "/nl-out"},
    "Германия": {"out_tag": "proxy-de", "port": 10015, "remark": "DE-Out", "path": "/de-out"},
    "Финляндия": {"out_tag": "proxy-fi-ws", "port": 10016, "remark": "FI-Out", "path": "/fi-out"},
}

logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger()

def fetch_sub():
    req = urllib.request.Request(SUB_URL, headers={"User-Agent": "v2box_short", "X-HWID": HWID})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = r.read()
    try:
        return base64.b64decode(data).decode("utf-8")
    except Exception:
        return data.decode("utf-8")

def parse_vless(url):
    match = re.match(r"vless://([^@]+)@([^:]+):(\d+)\?([^#]*)#?(.*)", url)
    if not match: return None
    uuid, host, port, params_str, name = match.groups()
    params = dict(urllib.parse.parse_qsl(params_str))
    return {
        "name": urllib.parse.unquote(name), "uuid": uuid, "host": host, "port": int(port),
        "flow": params.get("flow", ""), "sni": params.get("sni", ""),
        "pbk": params.get("pbk", ""), "sid": params.get("sid", ""), "fp": params.get("fp", "chrome")
    }

def make_outbound(tag, server):
    return {
        "tag": tag, "protocol": "vless",
        "settings": {"vnext": [{"address": server["host"], "port": server["port"], "users": [{"id": server["uuid"], "encryption": "none", "flow": server["flow"]}]}]},
        "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": server["sni"], "fingerprint": server["fp"], "publicKey": server["pbk"], "shortId": server["sid"], "spiderX": "/"}}
    }

def ensure_inbounds_api():
    """Создает Inbound'ы в панели через API, если их еще нет"""
    session = requests.Session()
    r = session.post(f"{PANEL_URL}/login", data={"username": PANEL_USER, "password": PANEL_PASS}, verify=False)
    if not r.json().get("success"):
        log.error("ошибка авторизации API")
        return False
        
    r = session.get(f"{PANEL_URL}/panel/api/inbounds/list", verify=False)
    existing = r.json().get("obj", [])
    existing_remarks = [i.get("remark") for i in existing]
    
    changed = False
    for name, info in TARGETS.items():
        if info["remark"] not in existing_remarks:
            payload = {
                "up": 0, "down": 0, "total": 0, "remark": info["remark"], "enable": "true", "expiryTime": 0,
                "listen": "", "port": info["port"], "protocol": "vless",
                "settings": json.dumps({"clients": [{"id": CLIENT_UUID, "flow": ""}], "decryption": "none", "fallbacks": []}),
                "streamSettings": json.dumps({"network": "ws", "security": "none", "wsSettings": {"acceptProxyProtocol": False, "path": f"/{info['remark'].lower()}", "headers": {}}}),
                "sniffing": json.dumps({"enabled": True, "destOverride": ["http", "tls", "quic", "fakedns"], "metadataOnly": False, "routeOnly": False}),
                "allocate": json.dumps({"strategy": "always", "refresh": 5, "concurrency": 3})
            }
            res = session.post(f"{PANEL_URL}/panel/api/inbounds/add", data=payload, verify=False)
            if res.status_code == 200 and res.json().get("success"):
                log.info(f"создан инбаунд {info['remark']} (порт {info['port']})")
                changed = True
    return changed

def update_xray(new_outbounds):
    """Обновляет исходящие серверы и прописывает Роутинг"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'")
    row = cur.fetchone()
    cfg = json.loads(row[0])
    outbounds = cfg.get("outbounds", [])
    
    changed = False
    
    # Обновляем outbounds
    for tag, ob in new_outbounds.items():
        existing = next((o for o in outbounds if o["tag"] == tag), None)
        if existing:
            old_addr = existing.get("settings", {}).get("vnext", [{}])[0].get("address", "")
            new_addr = ob["settings"]["vnext"][0]["address"]
            if old_addr != new_addr:
                outbounds[outbounds.index(existing)] = ob
                log.info(f"обновлён {tag}: {old_addr} → {new_addr}")
                changed = True
        else:
            outbounds.append(ob)
            log.info(f"добавлен outbound {tag}")
            changed = True
            
    cfg["outbounds"] = outbounds
    
    # Обновляем Routing (привязываем порты к странам)
    if "routing" not in cfg: cfg["routing"] = {"domainStrategy": "AsIs", "rules": []}
    if "rules" not in cfg["routing"]: cfg["routing"]["rules"] = []
    rules = cfg["routing"]["rules"]
    
    for name, info in TARGETS.items():
        inbound_tag = f"inbound-{info['port']}"
        outbound_tag = info["out_tag"]
        
        rule_exists = any(r.get("outboundTag") == outbound_tag and inbound_tag in r.get("inboundTag", []) for r in rules)
        if not rule_exists:
            rules.insert(0, {"type": "field", "inboundTag": [inbound_tag], "outboundTag": outbound_tag})
            log.info(f"добавлен роутинг {inbound_tag} → {outbound_tag}")
            changed = True

    if changed:
        cur.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'", (json.dumps(cfg, indent=4, ensure_ascii=False),))
        conn.commit()
        
    conn.close()
    return changed

def run_once():
    try:
        inbounds_added = ensure_inbounds_api()
        
        raw = fetch_sub()
        found = {}
        for line in raw.split("\n"):
            if not line.strip() or "0.0.0.0" in line or not line.startswith("vless://"): continue
            parsed = parse_vless(line)
            if not parsed: continue
            for keyword, info in TARGETS.items():
                if keyword in parsed["name"]:
                    found[info["out_tag"]] = make_outbound(info["out_tag"], parsed)
        
        config_changed = update_xray(found) if found else False

        # парсим whitelist серверы и сохраняем как готовые vless ссылки
        wl_links = []
        for line in raw.split("\n"):
            if not line.strip() or not line.startswith("vless://"):
                continue
            parsed = parse_vless(line)
            if not parsed:
                continue
            if any(kw in parsed["name"] for kw in WL_KEYWORDS):
                host = parsed["host"]
                # пропускаем невалидные хосты (не IP и не домен)
                if not host or host.lower().startswith("whitelist"):
                    continue
                # пропускаем серверы с лимитом трафика
                if "расходует" in parsed["name"].lower():
                    continue
                remark = urllib.parse.quote(parsed["name"].strip())
                link = (
                    f"vless://{parsed['uuid']}@{host}:{parsed['port']}"
                    f"?security=reality&type=tcp&flow={parsed['flow']}"
                    f"&sni={parsed['sni']}&pbk={parsed['pbk']}&sid={parsed['sid']}"
                    f"&fp=chrome#{remark}"
                )
                wl_links.append(link)

        # мержим авто-ссылки из подписки с ручными из whitelist_manual.txt
        manual_file = WL_FILE.replace("whitelist_links.txt", "whitelist_manual.txt")
        manual_links = []
        try:
            with open(manual_file) as f:
                manual_links = [l.strip() for l in f.readlines() if l.strip()]
        except FileNotFoundError:
            pass
        all_wl = manual_links + [l for l in wl_links if l not in manual_links]
        with open(WL_FILE, "w") as f:
            f.write("\n".join(all_wl) + "\n")
        log.info(f"whitelist обновлён: {len(manual_links)} ручных + {len(wl_links)} авто = {len(all_wl)} итого")

        if inbounds_added or config_changed:
            subprocess.run(["systemctl", "restart", "x-ui"], capture_output=True)
            log.info("x-ui перезапущен с новыми настройками")
            
    except Exception as e:
        log.error(f"ошибка: {e}")

if __name__ == "__main__":
    run_once() # Делаем первый запуск принудительно
    while True:
        time.sleep(INTERVAL)
        run_once()
