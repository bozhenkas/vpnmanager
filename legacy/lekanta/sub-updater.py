#!/usr/bin/env python3
import base64, json, logging, os, re, socket, sqlite3, subprocess, time, urllib.parse, urllib.request

SUB_URL   = "https://sub.whitestore.club/zPFyxgNrQGy2ekY7"
DB_PATH   = "/etc/x-ui/x-ui.db"
LOG_PATH  = "/var/log/sub-updater-lekanta.log"
WL_FILE        = "/opt/sub-updater/whitelist_links.txt"
WL_MANUAL      = "/opt/sub-updater/whitelist_manual.txt"
WL_REGISTRY_URL = "https://ru.goida.fun/wl/list"
WL_KEYWORDS    = ["Whitelist", "РЕЗЕРВ"]
INTERVAL     = 600
HWID         = "up8jf5kjyrzi0013"
SMART_IB_ID  = 1       # id инбаунда smart в x-ui
HYDRA_PORT_BASE = 10100  # порты 10101..10110 для hydra inbound'ов
NGINX_CONF      = "/etc/nginx/sites-enabled/lekanta.ru"
WL_FAIL_THRESHOLD = 3
WL_FAIL_STATE   = "/opt/sub-updater/wl_fail_counts.json"
# флаг: создавать ли hydra-инбаунды/аутбаунды (lekanta=да, RU=нет — там свои proxy-* через бот)
MANAGE_HYDRA = os.path.exists("/opt/sub-updater/manage_hydra")

# pbk → реальный IP для placeholder-хостов в whitelist подписке
WL_REAL_IPS = {
    "ZC4DzWDW73W4FCu3wnkG4eTbOLDRcHnutTyqbn-XWFo": "158.160.220.55",
    "S9wjXFiaNV25ogTVg_jxSN3_sZMKvky7QEaMazEBslM": "51.250.12.101",
    "CAlp9qO94iFo9e_lZ_WtmlF4nJSQlBNJk-etZhXouxY": "84.201.149.107",
}

def _load_fail_counts() -> dict:
    try:
        with open(WL_FAIL_STATE) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_fail_counts(counts: dict):
    try:
        with open(WL_FAIL_STATE, "w") as f:
            json.dump(counts, f)
    except Exception:
        pass

WL_FAIL_COUNTS: dict = _load_fail_counts()

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger()


def clean_name(name: str) -> str:
    """убирает суффиксы вида [Безлимит∞], [Расходует трафик] из имени сервера"""
    return re.sub(r"\s*\[.*?\]", "", name).strip()


def check_wl_alive(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def fetch_sub():
    req = urllib.request.Request(SUB_URL, headers={"User-Agent": "v2box_short", "X-HWID": HWID})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = r.read()
    try:
        return base64.b64decode(data).decode("utf-8")
    except Exception:
        return data.decode("utf-8")


def parse_vless(url):
    m = re.match(r"vless://([^@]+)@([^:]+):(\d+)\?([^#]*)#?(.*)", url)
    if not m:
        return None
    uid, host, port, params_str, name = m.groups()
    p = dict(urllib.parse.parse_qsl(params_str))
    return {
        "name": clean_name(urllib.parse.unquote(name)),
        "uuid": uid, "host": host, "port": int(port),
        "flow": p.get("flow", ""), "sni": p.get("sni", ""),
        "pbk": p.get("pbk", ""), "sid": p.get("sid", ""), "fp": p.get("fp", "chrome"),
    }


def wl_host(s) -> str | None:
    """реальный хост WL сервера после подстановки IP"""
    host = s["host"]
    if host.lower().startswith("whitelist"):
        return WL_REAL_IPS.get(s["pbk"])
    return host


def make_wl_link(s):
    """vless:// ссылка для whitelist-сервера; подставляет реальный IP по pbk если хост placeholder"""
    host = s["host"]
    if host.lower().startswith("whitelist"):
        real_ip = WL_REAL_IPS.get(s["pbk"])
        if not real_ip:
            return None
        host = real_ip
    flow_part = f"&flow={urllib.parse.quote(s['flow'])}" if s["flow"] else ""
    remark = urllib.parse.quote(s["name"].strip())
    return (
        f"vless://{s['uuid']}@{host}:{s['port']}"
        f"?security=reality&type=tcp{flow_part}"
        f"&sni={s['sni']}&pbk={s['pbk']}&sid={s['sid']}&fp=chrome#{remark}"
    )


# ─── x-ui inbound helpers ────────────────────────────────────────────────────

def _inbound_settings(smart_clients):
    return json.dumps({"clients": smart_clients, "decryption": "none", "fallbacks": []})


def _stream_settings(path):
    return json.dumps({
        "network": "ws", "security": "none",
        "wsSettings": {
            "acceptProxyProtocol": False,
            "path": path,
            "headers": {},
            "heartbeatPeriod": 30,
        },
    })


def _sniffing():
    return json.dumps({"enabled": False, "destOverride": []})


def sync_hydra_inbounds(hydra_servers, conn):
    """создаёт/обновляет x-ui inbound'ы для каждого hydra-сервера; возвращает [{tag,id,port,path,name}]"""
    # читаем текущие гидра-инбаунды
    existing = {tag: ib_id for ib_id, tag
                in conn.execute("SELECT id, tag FROM inbounds WHERE tag LIKE 'inbound-hydra-%'").fetchall()}

    # клиенты из smart инбаунда для синхронизации
    row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (SMART_IB_ID,)).fetchone()
    smart_clients = json.loads(row[0]).get("clients", []) if row else []

    result = []
    for i, s in enumerate(hydra_servers):
        n      = i + 1
        tag    = f"inbound-hydra-{n}"
        port   = HYDRA_PORT_BASE + n
        path   = f"/h/{n}"

        settings_str = _inbound_settings(smart_clients)
        stream_str   = _stream_settings(path)
        sniff_str    = _sniffing()

        if tag not in existing:
            conn.execute(
                "INSERT INTO inbounds "
                "(user_id,up,down,total,remark,enable,expiry_time,listen,port,protocol,settings,stream_settings,tag,sniffing) "
                "VALUES (1,0,0,0,?,1,0,'',?,'vless',?,?,?,?)",
                (s["name"], port, settings_str, stream_str, tag, sniff_str),
            )
            ib_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        else:
            ib_id = existing[tag]
            # синхронизируем клиентов и имя
            conn.execute(
                "UPDATE inbounds SET remark=?, settings=? WHERE id=?",
                (s["name"], settings_str, ib_id),
            )

        # client_traffics — создаём записи для статистики
        for c in smart_clients:
            conn.execute(
                "INSERT OR IGNORE INTO client_traffics "
                "(inbound_id,enable,email,up,down,expiry_time,total,reset) VALUES (?,1,?,0,0,0,0,0)",
                (ib_id, c["email"]),
            )

        result.append({"tag": tag, "id": ib_id, "port": port, "path": path, "name": s["name"]})

    # удаляем лишние hydra inbound'ы (серверов стало меньше)
    active = {ib["tag"] for ib in result}
    for tag, ib_id in existing.items():
        if tag not in active:
            conn.execute("DELETE FROM inbounds WHERE id=?", (ib_id,))
            conn.execute("DELETE FROM client_traffics WHERE inbound_id=?", (ib_id,))

    return result


def update_xray_routing(cfg, hydra_servers, hydra_inbounds):
    """обновляет outbound'ы и routing rules в xrayTemplateConfig"""
    # аутбаунды: убираем старые hydra-*, добавляем новые
    non_hydra = [o for o in cfg.get("outbounds", []) if not o.get("tag", "").startswith("hydra-")]
    new_obs = []
    for i, s in enumerate(hydra_servers):
        n   = i + 1
        tag = f"hydra-{n}"
        ob  = {
            "tag": tag, "_remark": s["name"], "protocol": "vless",
            "settings": {"vnext": [{"address": s["host"], "port": s["port"],
                                     "users": [{"id": s["uuid"], "encryption": "none",
                                                "flow": s["flow"]}]}]},
        }
        if s["pbk"]:
            ob["streamSettings"] = {
                "network": "tcp", "security": "reality",
                "realitySettings": {
                    "serverName": s["sni"], "fingerprint": s["fp"],
                    "publicKey": s["pbk"], "shortId": s["sid"], "spiderX": "/",
                },
            }
        else:
            ob["streamSettings"] = {"network": "tcp", "security": "tls",
                                     "tlsSettings": {"serverName": s["sni"]}}
        new_obs.append(ob)
    cfg["outbounds"] = non_hydra + new_obs

    # routing rules: убираем старые inbound-hydra-* правила
    rules = cfg.get("routing", {}).get("rules", [])
    rules = [r for r in rules
             if not any(t.startswith("inbound-hydra-") for t in r.get("inboundTag", []))]

    # вставляем правила inbound-hydra-N → hydra-N перед последним (balancer-hydra)
    insert_pos = len(rules)
    for r in reversed(rules):
        if r.get("balancerTag") == "balancer-hydra" or r.get("outboundTag") in ("direct", "direct-zapret"):
            break
        insert_pos -= 1
    for ib in hydra_inbounds:
        outbound_tag = ib["tag"].replace("inbound-", "")  # hydra-N
        rules.insert(insert_pos, {
            "type": "field",
            "inboundTag": [ib["tag"]],
            "outboundTag": outbound_tag,
        })

    cfg["routing"]["rules"] = rules
    return cfg


def update_nginx(hydra_inbounds):
    """добавляет /h/N location блоки в nginx конфиг если их там нет"""
    try:
        with open(NGINX_CONF) as f:
            content = f.read()
    except Exception as e:
        log.error(f"nginx conf read error: {e}")
        return

    location_tmpl = (
        "\n    location {path} {{\n"
        "        proxy_pass http://127.0.0.1:{port};\n"
        "        proxy_http_version 1.1;\n"
        "        proxy_set_header Upgrade $http_upgrade;\n"
        "        proxy_set_header Connection \"upgrade\";\n"
        "        proxy_set_header Host $host;\n"
        "        proxy_read_timeout 3600s;\n"
        "        proxy_send_timeout 3600s;\n"
        "    }}"
    )

    changed = False
    for ib in hydra_inbounds:
        marker = f"location {ib['path']}"
        if marker not in content:
            block = location_tmpl.format(path=ib["path"], port=ib["port"])
            # вставляем перед закрывающим } сервера
            insert_at = content.rfind("\n    location /")
            if insert_at == -1:
                insert_at = content.rfind("\n    location")
            content = content[:insert_at] + block + content[insert_at:]
            changed = True

    if changed:
        with open(NGINX_CONF, "w") as f:
            f.write(content)
        subprocess.run(["nginx", "-s", "reload"], capture_output=True)
        log.info(f"nginx перезагружен, добавлено {len(hydra_inbounds)} hydra location'ов")


# ─── main loop ───────────────────────────────────────────────────────────────

def run_once():
    try:
        raw = fetch_sub()
        hydra_servers, wl_servers = [], []
        for line in raw.splitlines():
            if not line.strip() or not line.startswith("vless://") or "0.0.0.0" in line:
                continue
            s = parse_vless(line)
            if not s:
                continue
            is_wl = any(kw in s["name"] for kw in WL_KEYWORDS)
            if is_wl:
                wl_servers.append(s)
            else:
                hydra_servers.append(s)

        # ── whitelist: ручной список из реестра (или локального файла) ──
        manual = []
        try:
            req = urllib.request.Request(
                WL_REGISTRY_URL, headers={"User-Agent": "sub-updater/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                manual = [l.strip() for l in r.read().decode().splitlines() if l.strip()]
            log.info(f"registry: получено {len(manual)} WL конфигов")
        except Exception as e:
            log.warning(f"registry недоступен ({e}), читаем локальный файл")
            try:
                with open(WL_MANUAL) as f:
                    manual = [l.strip() for l in f if l.strip()]
            except FileNotFoundError:
                pass

        def _wl_check_link(vless_url: str) -> bool:
            """TCP-проверка vless:// ссылки из ручного реестра"""
            m = re.match(r"vless://[^@]+@([^:]+):(\d+)", vless_url)
            if not m:
                return True  # не смогли распарсить — оставляем
            host, port = m.group(1), int(m.group(2))
            key = f"{host}:{port}"
            if check_wl_alive(host, port):
                WL_FAIL_COUNTS[key] = 0
                return True
            WL_FAIL_COUNTS[key] = WL_FAIL_COUNTS.get(key, 0) + 1
            cnt = WL_FAIL_COUNTS[key]
            if cnt < WL_FAIL_THRESHOLD:
                log.warning(f"WL {key}: недоступен ({cnt}/{WL_FAIL_THRESHOLD}), ещё в списке")
                return True
            log.warning(f"WL {key}: исключён из подписки (недоступен {cnt} раз)")
            return False

        # ── health check: ручные (реестр) + авто (из подписки) ──
        alive_manual = [lnk for lnk in manual if _wl_check_link(lnk)]
        manual_excluded = len(manual) - len(alive_manual)

        alive_wl = []
        for s in wl_servers:
            host = wl_host(s)
            if not host:
                continue
            key = f"{host}:{s['port']}"
            if check_wl_alive(host, s["port"]):
                WL_FAIL_COUNTS[key] = 0
                alive_wl.append(s)
            else:
                WL_FAIL_COUNTS[key] = WL_FAIL_COUNTS.get(key, 0) + 1
                cnt = WL_FAIL_COUNTS[key]
                if cnt < WL_FAIL_THRESHOLD:
                    alive_wl.append(s)
                    log.warning(f"WL {key}: недоступен ({cnt}/{WL_FAIL_THRESHOLD}), ещё в списке")
                else:
                    log.warning(f"WL {key}: исключён из подписки (недоступен {cnt} раз)")

        auto_wl = [lnk for s in alive_wl
                   if (lnk := make_wl_link(s)) and lnk not in alive_manual]
        raw_wl = alive_manual + auto_wl

        # дедупликация по host:port + последовательные имена "🇷🇺 Whitelist N"
        seen_hosts: set = set()
        all_wl = []
        n = 0
        for link in raw_wl:
            m = re.match(r"(vless://[^@]+@([^:]+:\d+)\?[^#]*)#?.*", link)
            if not m:
                continue
            base, hostport = m.group(1), m.group(2)
            if hostport in seen_hosts:
                continue
            seen_hosts.add(hostport)
            n += 1
            all_wl.append(f"{base}#{urllib.parse.quote(f'🇷🇺 Whitelist {n}')}")

        _save_fail_counts(WL_FAIL_COUNTS)
        with open(WL_FILE, "w") as f:
            f.write("\n".join(all_wl) + ("\n" if all_wl else ""))
        excl_auto = len(wl_servers) - len(alive_wl)
        total_excl = manual_excluded + excl_auto
        log.info(f"whitelist: {n} серверов ({len(alive_manual)} ручных + {len(auto_wl)} авто)" +
                 (f", {total_excl} исключено" if total_excl else ""))

        if not hydra_servers:
            log.warning("hydra серверов не найдено, пропускаем обновление xray")
            return

        # на RU hydra-инбаунды управляются напрямую через бот/ручную правку шаблона —
        # не дёргаем DB, не рестартим x-ui, не правим nginx
        if not MANAGE_HYDRA:
            log.info(f"MANAGE_HYDRA=False — пропускаем создание hydra inbound/outbound ({len(hydra_servers)} в источнике)")
            return

        # ── hydra inbound'ы и routing в x-ui ──
        # timeout=30 — чтобы не падать с 'database is locked' при конкуренции с x-ui/vpn-bot
        conn = sqlite3.connect(DB_PATH, timeout=30)
        hydra_inbounds = sync_hydra_inbounds(hydra_servers, conn)

        row = conn.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
        if not row:
            log.error("xrayTemplateConfig не найден в БД")
            conn.close()
            return
        cfg = json.loads(row[0])
        cfg = update_xray_routing(cfg, hydra_servers, hydra_inbounds)
        conn.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
                     (json.dumps(cfg, indent=2, ensure_ascii=False),))
        conn.commit()
        conn.close()

        log.info(f"обновлено {len(hydra_servers)} hydra серверов, перезапускаем x-ui")
        subprocess.run(["systemctl", "restart", "x-ui"], capture_output=True)

        # ── nginx ──
        update_nginx(hydra_inbounds)

    except Exception as e:
        log.error(f"ошибка: {e}", exc_info=True)


if __name__ == "__main__":
    run_once()
    while True:
        time.sleep(INTERVAL)
        run_once()
