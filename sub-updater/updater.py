#!/usr/bin/env python3
import base64, json, logging, os, re, socket, sqlite3, subprocess, tempfile, time, urllib.parse, urllib.request

SUB_URL   = "https://sub.whitestore.club/zPFyxgNrQGy2ekY7"
DB_PATH   = "/etc/x-ui/x-ui.db"
LOG_PATH  = os.environ.get("SUB_UPDATER_LOG_PATH", "/var/log/sub-updater-ru.log")
WL_FILE        = "/opt/sub-updater/whitelist_links.txt"
WL_MANUAL      = "/opt/sub-updater/whitelist_manual.txt"
WL_REGISTRY_URL = "https://ru.goida.fun/wl/list"
WL_KEYWORDS    = ["Whitelist", "РЕЗЕРВ"]
XRAY_BIN      = os.environ.get("XRAY_BIN", "/usr/local/x-ui/bin/xray-linux-amd64")
WL_PROBE_URL  = os.environ.get("WL_PROBE_URL", "https://www.gstatic.com/generate_204")
WL_PROBE_ATTEMPTS = int(os.environ.get("WL_PROBE_ATTEMPTS", "3"))
WL_PROBE_MIN_OK   = int(os.environ.get("WL_PROBE_MIN_OK", "2"))
INTERVAL     = 600
HWID         = "up8jf5kjyrzi0013"
SMART_IB_ID  = int(os.environ.get("SMART_IB_ID", "5"))  # id инбаунда smart на RU
HYDRA_PORT_BASE = 10100  # порты 10101..10110 для hydra inbound'ов
NGINX_CONF      = os.environ.get("NGINX_CONF", "/etc/nginx/sites-enabled/ru.goida.fun")
WL_FAIL_THRESHOLD = 3
# режимы:
#   MANAGE_HYDRA    — lekanta-стиль: динамические inbound-hydra-N + outbound hydra-N + balancer
#   MANAGE_HYDRA_RU — RU-стиль: фиксированные slot'ы по странам (inbound-100XX), outbound hydra-proxy-{cc},
#                      socks-proxy-{cc} для smart-pro per-country, чистый routing rebuild
MANAGE_HYDRA    = os.path.exists("/opt/sub-updater/manage_hydra")
MANAGE_HYDRA_RU = os.path.exists("/opt/sub-updater/manage_hydra_ru")

# RU country slots: фиксированный mapping country_code → port/path/inbound/outbound/socks-proxy-tag
RU_COUNTRY_SLOTS = {
    "usa": {"port":10011, "path":"/usa-out", "ib_tag":"inbound-10011",
            "ob_tag":"hydra-proxy-usa", "socks_tag":"socks-proxy-usa", "socks_port":20003,
            "remark":"ru(tls)-hydra-usa"},
    "pol": {"port":10012, "path":"/pol-out", "ib_tag":"inbound-10012",
            "ob_tag":"hydra-proxy-pol", "socks_tag":"socks-proxy-pol", "socks_port":20004,
            "remark":"ru(tls)-hydra-pol"},
    "tur": {"port":10013, "path":"/tur-out", "ib_tag":"inbound-10013",
            "ob_tag":"hydra-proxy-tur", "socks_tag":"socks-proxy-tur", "socks_port":20005,
            "remark":"ru(tls)-hydra-tur"},
    "nl":  {"port":10014, "path":"/nl-out",  "ib_tag":"inbound-10014",
            "ob_tag":"hydra-proxy-nl",  "socks_tag":"socks-proxy-nl",  "socks_port":20006,
            "remark":"ru(tls)-hydra-nl"},
    "de":  {"port":10015, "path":"/de-out",  "ib_tag":"inbound-10015",
            "ob_tag":"hydra-proxy-de",  "socks_tag":"socks-proxy-de",  "socks_port":20007,
            "remark":"ru(tls)-hydra-de"},
}
# имена из подписки → country code (None = игнор)
RU_NAME_TO_CC = {
    "США": "usa", "USA": "usa", "United States": "usa", "America": "usa",
    "Польша": "pol", "Poland": "pol",
    "Турция": "tur", "Turkey": "tur", "Türkiye": "tur",
    "Нидерланды": "nl", "Netherlands": "nl",
    "Германия": "de", "Германия-2": "de", "Germany": "de", "Germany-2": "de",
}
RU_FLAG_TO_CC = {
    "🇺🇸": "usa",
    "🇵🇱": "pol",
    "🇹🇷": "tur",
    "🇳🇱": "nl",
    "🇩🇪": "de",
    "🇫🇮": "fiws",
}
# балансер-smart фиксированный selector (по country) — кого оставлять в smart-балансере
RU_BALANCER_SMART_HYDRA_CC = set()  # hydra в balancer-smart отключена; только proxy-fi/proxy-se
AUTO_RULE_TAGS = {
    "goida-block-youtube-quic-20260512",
    "custom-ru-direct",
}

# pbk → реальный IP для placeholder-хостов в whitelist подписке
WL_REAL_IPS = {
    "ZC4DzWDW73W4FCu3wnkG4eTbOLDRcHnutTyqbn-XWFo": "158.160.220.55",
    "S9wjXFiaNV25ogTVg_jxSN3_sZMKvky7QEaMazEBslM": "51.250.12.101",
    "CAlp9qO94iFo9e_lZ_WtmlF4nJSQlBNJk-etZhXouxY": "84.201.149.107",
}

WL_FAIL_COUNTS: dict = {}

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger()


def clean_name(name: str) -> str:
    """убирает суффиксы вида [Безлимит∞], [Расходует трафик] и ведущие RIS-флаги (🇷🇺)"""
    name = re.sub(r"\s*\[.*?\]", "", name)
    # ведущие пары regional indicator symbols (U+1F1E6..U+1F1FF) — флаги
    while len(name) >= 2 and 0x1F1E6 <= ord(name[0]) <= 0x1F1FF and 0x1F1E6 <= ord(name[1]) <= 0x1F1FF:
        name = name[2:].lstrip()
    return name.strip()


def leading_flag(name: str) -> str | None:
    """возвращает ведущий unicode flag, если он есть."""
    name = name.strip()
    if len(name) >= 2 and 0x1F1E6 <= ord(name[0]) <= 0x1F1FF and 0x1F1E6 <= ord(name[1]) <= 0x1F1FF:
        return name[:2]
    return None


def check_wl_port_alive(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_port(host: str, port: int, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check_wl_port_alive(host, port, timeout=0.2):
            return True
        time.sleep(0.1)
    return False


def _wl_probe_config(s: dict, host: str, socks_port: int) -> dict:
    user = {"id": s["uuid"], "encryption": "none"}
    if s.get("flow"):
        user["flow"] = s["flow"]
    stream = {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
            "serverName": s["sni"],
            "fingerprint": s["fp"],
            "publicKey": s["pbk"],
            "shortId": s["sid"],
            "spiderX": "/",
        },
    }
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "tag": "wl-probe-in",
            "listen": "127.0.0.1",
            "port": socks_port,
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": False},
        }],
        "outbounds": [{
            "tag": "wl-probe-out",
            "protocol": "vless",
            "settings": {"vnext": [{
                "address": host,
                "port": s["port"],
                "users": [user],
            }]},
            "streamSettings": stream,
        }],
        "routing": {"rules": [{
            "type": "field",
            "inboundTag": ["wl-probe-in"],
            "outboundTag": "wl-probe-out",
        }]},
    }


def check_wl_alive_once(s: dict, timeout: float = 10.0) -> bool:
    """одна глубокая проверка VLESS/Reality через временный socks."""
    host = wl_host(s) or s["host"]
    if not check_wl_port_alive(host, s["port"]):
        return False
    if not s.get("pbk"):
        return True
    if not os.path.exists(XRAY_BIN):
        log.warning(f"WL deep-check: xray binary не найден ({XRAY_BIN}), fallback to tcp-check")
        return True

    socks_port = _free_local_port()
    cfg = _wl_probe_config(s, host, socks_port)
    cfg_path = None
    proc = None
    try:
        with tempfile.NamedTemporaryFile("w", prefix="wl-probe-", suffix=".json", delete=False) as f:
            cfg_path = f.name
            json.dump(cfg, f, ensure_ascii=False)
        proc = subprocess.Popen(
            [XRAY_BIN, "run", "-config", cfg_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not _wait_port("127.0.0.1", socks_port, timeout=3.0):
            return False
        res = subprocess.run(
            [
                "curl", "-fsS", "--socks5-hostname", f"127.0.0.1:{socks_port}",
                "--connect-timeout", "5", "--max-time", str(int(timeout)),
                "-o", "/dev/null", WL_PROBE_URL,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return res.returncode == 0
    except Exception as e:
        log.warning(f"WL deep-check error {host}:{s['port']}: {e}")
        return False
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        if cfg_path:
            try:
                os.unlink(cfg_path)
            except OSError:
                pass


def check_wl_alive(s: dict, timeout: float = 10.0) -> bool:
    """несколько deep-check попыток: флапающий WL не попадает в подписку."""
    ok_count = 0
    attempts = max(1, WL_PROBE_ATTEMPTS)
    min_ok = min(max(1, WL_PROBE_MIN_OK), attempts)
    for attempt in range(1, attempts + 1):
        if check_wl_alive_once(s, timeout=timeout):
            ok_count += 1
            if ok_count >= min_ok:
                return True
        remaining = attempts - attempt
        if ok_count + remaining < min_ok:
            break
    host = wl_host(s) or s["host"]
    log.warning(f"WL {host}:{s['port']}: deep-check ok={ok_count}/{attempts}, min_ok={min_ok}")
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
    raw_name = urllib.parse.unquote(name)
    return {
        "name": clean_name(raw_name),
        "raw_name": raw_name,
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


# ─── RU-режим: фиксированные country slots ──────────────────────────────────

def _ru_outbound_tag(slot: dict, idx: int) -> str:
    """первый сервер страны сохраняет старый tag, дубль получает -2/-3."""
    return slot["ob_tag"] if idx == 0 else f"{slot['ob_tag']}-{idx + 1}"


def _ru_balancer_tag(cc: str) -> str:
    return f"balancer-{RU_COUNTRY_SLOTS[cc]['ob_tag']}"


def _ru_route_target(cc: str, multi_ccs: set) -> dict:
    if cc in multi_ccs:
        return {"balancerTag": _ru_balancer_tag(cc)}
    return {"outboundTag": RU_COUNTRY_SLOTS[cc]["ob_tag"]}


def _ru_manual_rule_kind(rule: dict) -> str | None:
    """определяет ручное правило бота direct/home/foreign или возвращает None."""
    tag = rule.get("outboundTag") or rule.get("balancerTag")
    if tag == "home-mac-exit":
        return "home"
    if tag == "balancer-smart":
        return "foreign"
    if tag == "direct":
        return "direct"
    return None


def _ru_extract_manual_domain_rules(rules: list[dict]) -> list[dict]:
    """сохраняет ручные domain-правила бота, чтобы routing rebuild их не стирал."""
    by_kind: dict[str, list[str]] = {"home": [], "foreign": [], "direct": []}
    seen: set[tuple[str, str]] = set()

    for rule in rules:
        inbounds = set(rule.get("inboundTag") or [])
        if not ({"inbound-10003", "inbound-10005"} & inbounds):
            continue
        if rule.get("ruleTag") in AUTO_RULE_TAGS:
            continue
        domains = rule.get("domain") or []
        if not domains:
            continue
        # Skip canonical/system groups generated by this updater.
        if any(d.startswith(("geosite:", "ext:")) for d in domains):
            continue
        kind = _ru_manual_rule_kind(rule)
        if not kind:
            continue
        for domain in domains:
            if not domain.startswith("domain:"):
                continue
            # Static generated domains are already represented in _ru_build_routing_rules.
            if domain in _RU_STATIC_GENERATED_DOMAINS:
                continue
            key = (kind, domain)
            if key in seen:
                continue
            seen.add(key)
            by_kind[kind].append(domain)

    manual_rules = []
    for kind in ("home", "direct", "foreign"):
        domains = by_kind[kind]
        if not domains:
            continue
        rule = {
            "type": "field",
            "ruleTag": f"manual-{kind}",
            "inboundTag": ["inbound-10003", "inbound-10005"],
            "domain": domains,
        }
        if kind == "home":
            rule["outboundTag"] = "home-mac-exit"
        elif kind == "direct":
            rule["outboundTag"] = "direct"
        else:
            rule["balancerTag"] = "balancer-smart"
        manual_rules.append(rule)
    return manual_rules


def _ru_make_outbound(slot_key: str, s: dict, slot: dict, idx: int = 0) -> dict:
    """строит outbound hydra-proxy-{cc} из server-данных подписки"""
    ob = {
        "tag": _ru_outbound_tag(slot, idx),
        "_remark": s["name"],
        "protocol": "vless",
        "settings": {"vnext": [{
            "address": s["host"], "port": s["port"],
            "users": [{"id": s["uuid"], "encryption": "none", "flow": s["flow"]}],
        }]},
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
        ob["streamSettings"] = {"network":"tcp","security":"tls","tlsSettings":{"serverName":s["sni"]}}
    return ob


_RU_HOME_MAC_DOMAINS = [
    "domain:gosuslugi.ru","domain:esia.gosuslugi.ru","domain:mos.ru",
    "domain:2ip.ru","domain:2ip.io","domain:whoer.net",
    "domain:sberbank.ru","domain:tinkoff.ru","domain:vtb.ru",
    "domain:wildberries.ru","domain:wbcdn.ru","domain:wb.ru","domain:wbbasket.ru",
    "domain:wbx.ru","domain:wildberries-seller.ru","domain:wbstatic.net",
    "domain:ozon.ru","domain:ozonusercontent.com","domain:ozone.ru",
    "domain:avito.ru","domain:avito.st","domain:lamoda.ru",
    "domain:lemanapro.ru","domain:vseinstrumenty.ru",
    "domain:sbrf.ru","domain:sber.ru","domain:tbank.ru","domain:alfabank.ru",
    "domain:raiffeisen.ru","domain:gazprombank.ru","domain:gu-st.ru",
    "domain:nalog.ru","domain:lkfl.nalog.ru","domain:ivi.ru","domain:okko.tv",
    "domain:wink.ru","domain:more.tv","domain:hh.ru","domain:headhunter.ru",
    "domain:cian.ru","domain:litres.ru","domain:2gis.ru","domain:2gis.com",
    "domain:gismeteo.ru","domain:rambler.ru","domain:tutu.ru","domain:vkusvill.ru",
    "domain:lenta.com","domain:gorzdrav.spb.ru","domain:lk.sut.ru",
    "domain:gov.spb.ru","domain:mplusdeti.ru","domain:msk.cloud.vk.com",
    "domain:dixy.ru","domain:213.180.193.226","domain:213.180.193.135",
    "domain:84.252.149.208","domain:188.68.217.194","domain:46.243.227.98",
    "domain:boosty.to",
]
_RU_IP_LEAK_DOMAINS = [
    "domain:ipinfo.io","domain:ipapi.co","domain:ipapi.com","domain:ipapi.is",
    "domain:ipify.org","domain:api.ipify.org","domain:ip-api.com",
    "domain:maxmind.com","domain:geoip.maxmind.com","domain:myip.ru","domain:myip.com",
    "domain:2ip.ru","domain:2ip.io","domain:whoer.net","domain:db-ip.com",
    "domain:ip2location.com","domain:ipgeolocation.io","domain:abstractapi.com",
    "domain:bigdatacloud.net","domain:ipdata.co","domain:ipv4.icanhazip.com",
    "domain:icanhazip.com","domain:ifconfig.me","domain:ifconfig.co",
    "domain:checkip.amazonaws.com","domain:ident.me",
]
_RU_YT_DISCORD_DOMAINS = ["geosite:youtube","domain:googlevideo.com","geosite:discord"]
_RU_CUSTOM_DIRECT_DOMAINS = [
    "domain:sbermarket.com","domain:avito.ru","domain:wildberries.ru",
    "domain:ozon.ru","domain:vk.com","domain:funpay.com",
    "domain:mangalib.org","domain:api.cdnlibs.org",
]
_RU_STATIC_GENERATED_DOMAINS = set(
    _RU_HOME_MAC_DOMAINS
    + _RU_IP_LEAK_DOMAINS
    + _RU_YT_DISCORD_DOMAINS
    + _RU_CUSTOM_DIRECT_DOMAINS
    + ["domain:www.happ.su", "ext:itdog_geosite.dat:russia-inside@geoblock", "geosite:category-ru"]
)


def _ru_build_routing_rules(active_ccs: set, multi_ccs: set | None = None, manual_rules: list[dict] | None = None) -> list:
    """строит routing.rules под текущий набор живых country slots."""
    multi_ccs = multi_ccs or set()
    manual_rules = manual_rules or []
    HOME_MAC_IPS = [
        "5.45.192.0/18","5.255.192.0/18","37.9.64.0/18","37.140.128.0/18",
        "77.88.0.0/18","84.252.128.0/18","87.250.224.0/19","93.158.128.0/18",
        "95.108.128.0/17","141.8.128.0/18","178.154.128.0/18","185.71.76.0/22",
        "213.180.192.0/21","84.201.0.0/16","51.250.0.0/16","130.193.0.0/16",
    ]
    R = []
    add = R.append

    add({"type":"field","inboundTag":["api"],"outboundTag":"api"})
    add({"type":"field","protocol":["bittorrent"],"outboundTag":"blocked"})
    add({"type":"field","ip":["geoip:private"],"outboundTag":"direct"})
    add({"type":"field","ruleTag":"goida-block-youtube-quic-20260512",
         "inboundTag":["inbound-10003","inbound-10004"],
         "domain":["geosite:youtube","domain:googlevideo.com"],
         "port":"443","network":"udp","outboundTag":"blocked"})
    add({"type":"field","inboundTag":["inbound-10004"],"outboundTag":"direct-zapret"})

    # ручные правила из бота должны быть выше любых auto/system правил.
    R.extend(manual_rules)

    # smart/smart-pro: RU-сервисы (домены/IP) → home-mac-exit (ДО universal RU)
    add({"type":"field","inboundTag":["inbound-10003","inbound-10005"],
         "domain": _RU_HOME_MAC_DOMAINS, "outboundTag":"home-mac-exit"})
    add({"type":"field","inboundTag":["inbound-10003","inbound-10005"],
         "ip": HOME_MAC_IPS, "outboundTag":"home-mac-exit"})

    # smart: yt/discord → direct (на RU без zapret)
    add({"type":"field","inboundTag":["inbound-10003"],
         "domain": _RU_YT_DISCORD_DOMAINS, "network":"tcp","outboundTag":"direct"})
    add({"type":"field","inboundTag":["inbound-10003"],
         "domain": _RU_YT_DISCORD_DOMAINS, "port":"443","network":"udp","outboundTag":"direct"})

    # smart-pro: port:53 → dns-out (ДО universal RU)
    add({"type":"field","inboundTag":["inbound-10005"],"port":"53","outboundTag":"dns-out"})

    # IP-leak protection (ДО universal RU чтобы перебивало RU geoip)
    add({"type":"field","domain": _RU_IP_LEAK_DOMAINS,"outboundTag":"direct"})

    # universal RU → direct
    add({"type":"field","domain":["geosite:category-ru"],"outboundTag":"direct"})
    add({"type":"field","ip":["ext:ru_geoip.dat:ru","139.45.0.0/16"],"outboundTag":"direct"})
    add({"type":"field","ruleTag":"custom-ru-direct","domain": _RU_CUSTOM_DIRECT_DOMAINS,"outboundTag":"direct"})

    # 1:1 inbounds → outbounds (только для активных стран)
    add({"type":"field","inboundTag":["inbound-10001"],"outboundTag":"proxy-fi"})
    add({"type":"field","inboundTag":["inbound-10002"],"outboundTag":"proxy-se"})
    for cc, slot in RU_COUNTRY_SLOTS.items():
        if cc in active_ccs:
            add({"type":"field","inboundTag":[slot["ib_tag"]], **_ru_route_target(cc, multi_ccs)})

    # smart-pro
    add({"type":"field","inboundTag":["inbound-10005"],"outboundTag":"smart-pro-out"})

    # smart probe + balancer
    add({"type":"field","inboundTag":["inbound-10003"],
         "domain":["domain:www.happ.su"],"balancerTag":"balancer-smart"})
    add({"type":"field","inboundTag":["inbound-10003"],
         "domain":["ext:itdog_geosite.dat:russia-inside@geoblock"],
         "balancerTag":"balancer-smart"})
    add({"type":"field","inboundTag":["inbound-10003"],"balancerTag":"balancer-smart"})

    # socks-proxy-* → outbound (только активные)
    socks_pairs = [("socks-proxy-fi","proxy-fi"),("socks-proxy-se","proxy-se")]
    for cc, slot in RU_COUNTRY_SLOTS.items():
        if cc in active_ccs:
            socks_pairs.append((slot["socks_tag"], cc))
    for sk, ob in socks_pairs:
        if ob in RU_COUNTRY_SLOTS:
            add({"type":"field","inboundTag":[sk],"port":"0-65535", **_ru_route_target(ob, multi_ccs)})
        else:
            add({"type":"field","inboundTag":[sk],"port":"0-65535","outboundTag": ob})

    return R


def manage_hydra_ru(hydra_servers, conn) -> bool:
    """RU-режим. Возвращает True если template/inbounds изменились (нужен restart x-ui)."""
    # 1. сопоставление имени/флага → country code
    cc_to_servers = {}
    unknown_names = []
    for s in hydra_servers:
        name = s["name"]
        flag = leading_flag(s.get("raw_name", "")) or leading_flag(name)
        cc = RU_NAME_TO_CC.get(name) or (RU_FLAG_TO_CC.get(flag) if flag else None)
        if cc is None:
            unknown_names.append(s.get("raw_name", name))
            continue
        if cc not in RU_COUNTRY_SLOTS:
            log.warning(f"RU hydra: cc={cc!r} нет в RU_COUNTRY_SLOTS, добавьте slot")
            continue
        cc_to_servers.setdefault(cc, []).append(s)
    if unknown_names:
        log.warning(f"RU hydra: новые/неизвестные имена в подписке (добавьте slot или mapping): {unknown_names}")

    active_ccs = set(cc_to_servers.keys())
    multi_ccs = {cc for cc, servers in cc_to_servers.items() if len(servers) > 1}
    log.info(
        "RU hydra: активные страны = %s, серверы = %s",
        sorted(active_ccs),
        {cc: [s["name"] for s in servers] for cc, servers in cc_to_servers.items()},
    )

    # 2. читаем template
    row = conn.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
    if not row:
        log.error("xrayTemplateConfig не найден")
        return False
    cfg = json.loads(row[0])
    cfg_before = json.dumps(cfg, sort_keys=True)
    manual_rules = _ru_extract_manual_domain_rules(cfg.get("routing", {}).get("rules", []))
    if manual_rules:
        log.info(
            "RU hydra: сохранены ручные правила = %s",
            {r.get("ruleTag"): len(r.get("domain", [])) for r in manual_rules},
        )

    # 3. ensure outbounds для активных + удаление неактивных hydra-proxy-*
    desired_ob_tags = set()
    for cc, servers in cc_to_servers.items():
        slot = RU_COUNTRY_SLOTS[cc]
        for idx, _ in enumerate(servers):
            desired_ob_tags.add(_ru_outbound_tag(slot, idx))
    new_obs = []
    for ob in cfg.get("outbounds", []):
        tag = ob.get("tag", "")
        if tag.startswith("hydra-proxy-") and tag not in desired_ob_tags:
            continue  # удаляем неактивный
        if tag in desired_ob_tags:
            continue  # будем перезаписывать ниже
        new_obs.append(ob)
    for cc, servers in cc_to_servers.items():
        slot = RU_COUNTRY_SLOTS[cc]
        for idx, s in enumerate(servers):
            new_obs.append(_ru_make_outbound(cc, s, slot, idx))
    cfg["outbounds"] = new_obs

    # 3.1 country balancers для стран с 2+ серверами
    balancers = cfg.setdefault("routing", {}).setdefault("balancers", [])
    balancers = [b for b in balancers if not b.get("tag", "").startswith("balancer-hydra-proxy-")]
    for cc in sorted(multi_ccs):
        slot = RU_COUNTRY_SLOTS[cc]
        selectors = [_ru_outbound_tag(slot, idx) for idx, _ in enumerate(cc_to_servers[cc])]
        balancers.append({
            "tag": _ru_balancer_tag(cc),
            "selector": selectors,
            "fallbackTag": selectors[0],
            "strategy": {"type": "roundRobin"},
        })
    cfg["routing"]["balancers"] = balancers

    # 4. balancer-smart: сохраняем ручные записи (не hydra-proxy-*), обновляем только hydra-часть
    balancer = next((b for b in cfg.get("routing", {}).get("balancers", []) if b.get("tag") == "balancer-smart"), None)
    if balancer:
        manual = [s for s in balancer.get("selector", []) if not s.startswith("hydra-proxy-")]
        hydra = []
        for cc in RU_BALANCER_SMART_HYDRA_CC:
            if cc in active_ccs:
                slot = RU_COUNTRY_SLOTS[cc]
                for idx, _ in enumerate(cc_to_servers[cc]):
                    hydra.append(_ru_outbound_tag(slot, idx))
        sel = manual + hydra
        balancer["selector"] = sel
        if balancer.get("fallbackTag") not in sel:
            balancer["fallbackTag"] = "proxy-fi"

    # 5. burstObservatory subjectSelector — то же что balancer-smart
    bo = cfg.get("burstObservatory")
    if bo:
        bo["subjectSelector"] = list(balancer["selector"]) if balancer else ["proxy-fi","proxy-se"]

    # 6. routing.rules — пересоберём с нуля, сохранив ручные правила бота
    cfg.setdefault("routing", {})["rules"] = _ru_build_routing_rules(active_ccs, multi_ccs, manual_rules)

    # 7. policy stats outbound = True (на всякий случай)
    pol = cfg.setdefault("policy", {}).setdefault("system", {})
    pol["statsInboundUplink"] = True
    pol["statsInboundDownlink"] = True
    pol["statsOutboundUplink"] = True
    pol["statsOutboundDownlink"] = True

    cfg.setdefault("stats", {})
    cfg.setdefault("api", {"tag":"api","services":["HandlerService","LoggerService","StatsService"]})

    # 8. inbounds в x-ui таблице: enable/disable согласно active_ccs
    db_changes = 0
    for cc, slot in RU_COUNTRY_SLOTS.items():
        port = slot["port"]
        row_in = conn.execute("SELECT id, enable, remark FROM inbounds WHERE port=?", (port,)).fetchone()
        if not row_in:
            log.warning(f"RU hydra: inbound port={port} ({cc}) НЕ существует в x-ui — пропуск")
            continue
        ib_id, enable_cur, remark_cur = row_in
        want_enable = 1 if cc in active_ccs else 0
        if enable_cur != want_enable:
            conn.execute("UPDATE inbounds SET enable=? WHERE id=?", (want_enable, ib_id))
            db_changes += 1
            log.info(f"RU hydra: inbound port={port} ({cc}) enable {enable_cur}→{want_enable}")

    # 9. сохраняем template
    cfg_after = json.dumps(cfg, sort_keys=True)
    template_changed = cfg_before != cfg_after
    if template_changed:
        conn.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
                     (json.dumps(cfg, indent=2, ensure_ascii=False),))

    if template_changed or db_changes:
        log.info(f"RU hydra: template_changed={template_changed} db_changes={db_changes}")
    return template_changed or db_changes > 0


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

        # ── whitelist: сборка ручного списка (реестр → локальный fallback) ──
        manual_lines = []
        try:
            req = urllib.request.Request(
                WL_REGISTRY_URL, headers={"User-Agent": "sub-updater/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                manual_lines = [l.strip() for l in r.read().decode().splitlines() if l.strip()]
            log.info(f"registry: получено {len(manual_lines)} WL конфигов")
        except Exception as e:
            log.warning(f"registry недоступен ({e}), читаем локальный файл")
            try:
                with open(WL_MANUAL) as f:
                    manual_lines = [l.strip() for l in f if l.strip()]
            except FileNotFoundError:
                pass

        # парсим manual + дедуп с auto WL (host:port уникален) — в порядке появления
        all_wl_servers = []
        seen_hosts = set()
        for line in manual_lines:
            s = parse_vless(line)
            if not s:
                continue
            host = wl_host(s) or s["host"]
            key = f"{host}:{s['port']}"
            if key in seen_hosts:
                continue
            seen_hosts.add(key)
            all_wl_servers.append(s)
        for s in wl_servers:
            host = wl_host(s) or s["host"]
            key = f"{host}:{s['port']}"
            if key in seen_hosts:
                continue
            seen_hosts.add(key)
            all_wl_servers.append(s)

        # health-check: только реально живые в подписку (без grace-периода)
        alive = []
        for s in all_wl_servers:
            host = wl_host(s) or s["host"]
            key = f"{host}:{s['port']}"
            if check_wl_alive(s):
                alive.append(s)
            else:
                log.warning(f"WL {key}: deep-check failed, исключён")

        # последовательная нумерация: whitelist #N 🇷🇺
        stable_alive = []
        for s in alive:
            probe = dict(s)
            probe["name"] = "whitelist probe"
            lnk = make_wl_link(probe)
            probe_s = parse_vless(lnk) if lnk else None
            if probe_s and check_wl_alive(probe_s):
                stable_alive.append(s)
            else:
                host = wl_host(s) or s["host"]
                log.warning(f"WL {host}:{s['port']}: final-link deep-check failed, исключён")

        final_lines = []
        for i, s in enumerate(stable_alive, start=1):
            s2 = dict(s)
            s2["name"] = f"whitelist #{i} 🇷🇺"
            lnk = make_wl_link(s2)
            if lnk:
                final_lines.append(lnk)

        with open(WL_FILE, "w") as f:
            f.write("\n".join(final_lines) + ("\n" if final_lines else ""))
        excluded = len(all_wl_servers) - len(stable_alive)
        log.info(
            f"whitelist: {len(stable_alive)} живых "
            f"(manual={len(manual_lines)}, auto={len(wl_servers)})"
            + (f", {excluded} исключено" if excluded else "")
        )

        if not hydra_servers:
            log.warning("hydra серверов не найдено, пропускаем обновление xray")
            return

        # RU-режим: фиксированные country slots, ensure outbounds + routing rebuild
        if MANAGE_HYDRA_RU:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            try:
                changed = manage_hydra_ru(hydra_servers, conn)
                if changed:
                    conn.commit()
                    log.info("RU hydra: изменения, перезапускаем x-ui")
                    subprocess.run(["systemctl","restart","x-ui"], capture_output=True)
                else:
                    log.info("RU hydra: без изменений, x-ui не трогаем")
            finally:
                conn.close()
            return

        # на RU hydra-инбаунды управляются напрямую через бот/ручную правку шаблона —
        # не дёргаем DB, не рестартим x-ui, не правим nginx
        if not MANAGE_HYDRA:
            log.info(f"MANAGE_HYDRA=False — пропускаем создание hydra inbound/outbound ({len(hydra_servers)} в источнике)")
            return

        # ── hydra inbound'ы и routing в x-ui (lekanta-режим) ──
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
