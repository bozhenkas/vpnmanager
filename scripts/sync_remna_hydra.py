#!/usr/bin/env python3
"""sync hydra subscription into the RU Remnawave ingress profile."""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def _load_source_env(path: str) -> dict[str, str]:
    """Parse a simple KEY=VALUE env file (one var per line, `#` comments)."""
    values: dict[str, str] = {}
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return values


# Bot-managed override (goida-infra-backend /api/hydra/url): if present, its
# SUB_URL takes priority over the HYDRA_SUB_URL env var and the hardcoded
# default below. File is absent by default — fully backward-compatible.
HYDRA_SOURCE_ENV_PATH = os.environ.get("HYDRA_SOURCE_ENV_PATH", "/etc/goida-hydra/source.env")
_SOURCE_ENV = _load_source_env(HYDRA_SOURCE_ENV_PATH)

SUB_URL = _SOURCE_ENV.get("SUB_URL") or os.environ.get("HYDRA_SUB_URL", "https://sub.whitestore.club/Mk93Kvj6vcJMUakG")
SUB_UA = os.environ.get("HYDRA_SUB_UA", "v2box_short")
# JSON-подписка отдаётся upstream на Happ-UA (полный Xray streamSettings: xhttp/extra/xmux/tls/reality).
SUB_UA_JSON = os.environ.get("HYDRA_SUB_UA_JSON", "")  # пусто → строим из SUB_HWID
SUB_HWID = os.environ.get("HYDRA_SUB_HWID", "up8jf5kjyrzi0013")

# WL — прямые client→WL ссылки (без RU-хопа): vpn-bot читает этот файл (WL_FILE в vpn-bot.py).
WL_FILE = os.environ.get("WL_FILE", "/opt/sub-updater/whitelist_links.txt")
WL_JSON_PREFIX = "#goida-wl-json:"

PROFILE_UUID = os.environ.get("REMNA_RU_PROFILE_UUID", "11111111-7443-4000-8000-000000000001")
RU_NODE_NAME = os.environ.get("REMNA_RU_NODE_NAME", "ru-smart-goida")
PUBLIC_HOST = os.environ.get("REMNA_PUBLIC_HOST", "ru.goida.fun")
PUBLIC_PORT = int(os.environ.get("REMNA_PUBLIC_PORT", "443"))
USERNAME = os.environ.get("REMNA_TARGET_USER", "bozhenkas")
BOT_DB = os.environ.get("BOT_DB", "/root/vpn-bot/bot.db")
NGINX_CONF = os.environ.get("REMNA_NGINX_CONF", "/etc/nginx/sites-enabled/ru.goida.fun")

NAMESPACE = uuid.UUID("9b170c1d-1905-412e-8900-79c5015844d1")

COUNTRIES = {
    "nl": {"title": "Нидерланды", "flag": "🇳🇱", "names": {"Нидерланды", "Netherlands"}, "port": 17460, "path": "/hydra-nl"},
    "de": {"title": "Германия", "flag": "🇩🇪", "names": {"Германия", "Германия-2", "Germany", "Germany-2"}, "port": 17461, "path": "/hydra-de"},
    "usa": {"title": "США", "flag": "🇺🇸", "names": {"США", "USA", "United States"}, "port": 17462, "path": "/hydra-usa"},
    "pol": {"title": "Польша", "flag": "🇵🇱", "names": {"Польша", "Poland"}, "port": 17463, "path": "/hydra-pol"},
    "tur": {"title": "Турция", "flag": "🇹🇷", "names": {"Турция", "Turkey", "Türkiye"}, "port": 17464, "path": "/hydra-tur"},
}
FLAGS = {meta["flag"]: cc for cc, meta in COUNTRIES.items()}
ORDER = ["usa", "pol", "tur", "nl", "de"]


def run(cmd: list[str], *, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{proc.stderr or proc.stdout}")
    return proc


def psql(sql: str) -> str:
    return run([
        "docker", "exec", "-i", "remnawave-db",
        "psql", "-U", "postgres", "-d", "postgres", "-At", "-F", "\t", "-c", sql,
    ]).stdout


def sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_json(value: object) -> str:
    return "$json$" + json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "$json$::jsonb"


def stable_uuid(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def clean_name(name: str) -> str:
    name = re.sub(r"\s*\[.*?\]", "", name)
    while len(name) >= 2 and 0x1F1E6 <= ord(name[0]) <= 0x1F1FF and 0x1F1E6 <= ord(name[1]) <= 0x1F1FF:
        name = name[2:].lstrip()
    return name.strip()


def leading_flag(name: str) -> str:
    name = name.strip()
    if len(name) >= 2 and 0x1F1E6 <= ord(name[0]) <= 0x1F1FF and 0x1F1E6 <= ord(name[1]) <= 0x1F1FF:
        return name[:2]
    return ""


def fetch_sub() -> str:
    req = urllib.request.Request(SUB_URL, headers={"User-Agent": SUB_UA, "X-HWID": SUB_HWID})
    data = urllib.request.urlopen(req, timeout=20).read()
    try:
        return base64.b64decode(data + b"===").decode("utf-8")
    except Exception:
        return data.decode("utf-8")


def fetch_sub_json() -> list[dict]:
    """JSON-подписка: upstream на Happ-UA отдаёт массив полных Xray-профилей."""
    ua = SUB_UA_JSON or f"Happ/1.0/iOS/{SUB_HWID}"
    req = urllib.request.Request(SUB_URL, headers={"User-Agent": ua, "X-HWID": SUB_HWID})
    raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8")
    profiles = json.loads(raw)
    if isinstance(profiles, dict):
        profiles = [profiles]
    if not isinstance(profiles, list):
        raise ValueError("unexpected JSON sub shape")
    return profiles


def _vless_outbounds(profile: dict) -> list[dict]:
    return [
        o for o in (profile.get("outbounds") or [])
        if isinstance(o, dict) and o.get("protocol") == "vless"
        and (o.get("settings", {}).get("vnext") or [])
    ]


def _first_uid(profiles: list[dict]) -> str:
    for p in profiles:
        for o in _vless_outbounds(p):
            for vn in o.get("settings", {}).get("vnext", []) or []:
                for u in vn.get("users", []) or []:
                    if u.get("id"):
                        return str(u["id"])
    return ""


def load_subscription() -> tuple[dict[str, list[dict]], list[dict], str]:
    """returns (grouped_hydra, wl_profiles, sub_uid).

    Источник правды — JSON-подписка (полный транспорт). При сбое JSON
    деградируем на base64-vless (только tcp+reality, без XHTTP, без WL)."""
    try:
        profiles = fetch_sub_json()
    except Exception as exc:
        print(f"json sub failed ({exc}); fallback to base64 vless (tcp-reality only)", file=sys.stderr)
        return hydra_servers_vless_fallback(), [], _fallback_uid()
    return classify_profiles(profiles, probe=True)


def classify_profiles(profiles: list[dict], *, probe: bool = False) -> tuple[dict[str, list[dict]], list[dict], str]:
    """чистая классификация JSON-профилей подписки → (grouped_hydra, wl_profiles, sub_uid)."""
    sub_uid = _first_uid(profiles)
    grouped: dict[str, list[dict]] = {cc: [] for cc in COUNTRIES}
    wl_profiles: list[dict] = []
    for p in profiles:
        remarks = str(p.get("remarks") or "")
        obs = _vless_outbounds(p)
        if not obs:
            continue
        if "Whitelist" in remarks:
            wl_profiles.append(p)
            continue
        ob = obs[0]
        vn = (ob.get("settings", {}).get("vnext") or [{}])[0]
        user = (vn.get("users") or [{}])[0]
        server = {
            "raw_name": remarks,
            "name": clean_name(remarks),
            "host": vn.get("address", ""),
            "port": int(vn.get("port", 443)),
            "uuid": user.get("id", ""),
            "outbound": ob,
        }
        cc = country_for(server)
        if not cc:
            print(f"skip unknown hydra line: {remarks}", file=sys.stderr)
            continue
        if probe and not tcp_alive(server["host"], server["port"]):
            print(f"warn hydra probe failed {cc}: {server['host']}:{server['port']}", file=sys.stderr)
        grouped[cc].append(server)
    grouped = {cc: grouped[cc] for cc in ORDER if grouped[cc]}
    return grouped, wl_profiles, sub_uid


def _fallback_uid() -> str:
    for line in fetch_sub().splitlines():
        s = parse_vless(line)
        if s and s.get("uuid"):
            return s["uuid"]
    return ""


def hydra_servers_vless_fallback() -> dict[str, list[dict]]:
    """деградированный путь: только tcp+reality из base64-vless (без outbound-fidelity)."""
    grouped: dict[str, list[dict]] = {cc: [] for cc in COUNTRIES}
    for line in fetch_sub().splitlines():
        if not line.strip().startswith("vless://"):
            continue
        server = parse_vless(line)
        if not server:
            continue
        cc = country_for(server)
        if not cc:
            continue
        if not tcp_alive(server["host"], server["port"]):
            print(f"warn hydra probe failed {cc}: {server['host']}:{server['port']}", file=sys.stderr)
        grouped[cc].append(server)
    return {cc: grouped[cc] for cc in ORDER if grouped[cc]}


def parse_vless(line: str) -> dict | None:
    parsed = urllib.parse.urlsplit(line.strip())
    if parsed.scheme != "vless" or not parsed.hostname or not parsed.port:
        return None
    params = dict(urllib.parse.parse_qsl(parsed.query))
    raw_name = urllib.parse.unquote(parsed.fragment or "")
    name = clean_name(raw_name)
    if "Whitelist" in raw_name or "Whitelist" in name:
        return None
    return {
        "raw_name": raw_name,
        "name": name,
        "uuid": parsed.username or "",
        "host": parsed.hostname,
        "port": int(parsed.port),
        "flow": params.get("flow", ""),
        "sni": params.get("sni", ""),
        "fp": params.get("fp", "chrome"),
        "pbk": params.get("pbk", ""),
        "sid": params.get("sid", ""),
    }


def country_for(server: dict) -> str:
    flag = leading_flag(server.get("raw_name", "")) or leading_flag(server.get("name", ""))
    if flag in FLAGS:
        return FLAGS[flag]
    name = server.get("name", "")
    for cc, meta in COUNTRIES.items():
        if name in meta["names"]:
            return cc
    return ""


def existing_hydra_servers(cfg: dict) -> dict[str, list[dict]]:
    active_tags = {str(i.get("tag", "")) for i in cfg.get("inbounds", [])}
    grouped: dict[str, list[dict]] = {cc: [] for cc in COUNTRIES}
    for outbound in cfg.get("outbounds", []):
        tag = str(outbound.get("tag", ""))
        match = re.fullmatch(r"HYDRA_([A-Z]+)(?:_(\d+))?", tag)
        if not match:
            continue
        cc = match.group(1).lower()
        if cc not in COUNTRIES or inbound_tag(cc) not in active_tags:
            continue
        vnext = (outbound.get("settings", {}).get("vnext") or [{}])[0]
        user = (vnext.get("users") or [{}])[0]
        reality = outbound.get("streamSettings", {}).get("realitySettings", {})
        grouped[cc].append({
            "raw_name": outbound.get("_remark") or COUNTRIES[cc]["title"],
            "name": clean_name(outbound.get("_remark") or COUNTRIES[cc]["title"]),
            "uuid": user.get("id", ""),
            "host": vnext.get("address", ""),
            "port": int(vnext.get("port", 443)),
            "flow": user.get("flow", ""),
            "sni": reality.get("serverName", ""),
            "fp": reality.get("fingerprint", "chrome"),
            "pbk": reality.get("publicKey", ""),
            "sid": reality.get("shortId", ""),
            # полный транспорт сохранённого outbound — чтобы preserve-flap не терял XHTTP/tls/extra
            "outbound": copy.deepcopy(outbound),
        })
    return {cc: grouped[cc] for cc in ORDER if grouped[cc]}


def preserve_existing_on_probe_flap(grouped: dict[str, list[dict]], cfg: dict) -> dict[str, list[dict]]:
    """не удаляем опубликованную страну из-за одиночного сетевого флапа hydra."""
    existing = existing_hydra_servers(cfg)
    merged = {cc: list(grouped[cc]) for cc in ORDER if grouped.get(cc)}
    for cc in ORDER:
        if cc not in merged and cc in existing:
            merged[cc] = existing[cc]
            hosts = ", ".join(server["host"] for server in existing[cc])
            print(f"keep existing hydra {cc} after failed probe: {hosts}", file=sys.stderr)
    return {cc: merged[cc] for cc in ORDER if cc in merged}


def tcp_alive(host: str, port: int, timeout: float = 4.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def inbound_tag(cc: str) -> str:
    return f"GOIDA_HYDRA_{cc.upper()}"


def outbound_tag(cc: str, idx: int = 0) -> str:
    return f"HYDRA_{cc.upper()}" if idx == 0 else f"HYDRA_{cc.upper()}_{idx + 1}"


def balancer_tag(cc: str) -> str:
    return f"BALANCER_HYDRA_{cc.upper()}"


def squad_name(cc: str) -> str:
    return f"HYDRA_{cc.upper()}_REMNA"


def make_inbound(cc: str) -> dict:
    meta = COUNTRIES[cc]
    return {
        "tag": inbound_tag(cc),
        "listen": "127.0.0.1",
        "port": meta["port"],
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
        "streamSettings": {
            "network": "ws",
            "security": "none",
            "wsSettings": {"path": meta["path"], "headers": {"Host": PUBLIC_HOST}, "heartbeatPeriod": 30},
        },
    }


def make_outbound(cc: str, idx: int, server: dict, sub_uid: str) -> dict:
    # JSON-источник: точная репликация транспорта (tcp/ws/xhttp/grpc, reality/tls, extra/xmux/mux).
    raw = server.get("outbound")
    if raw:
        outbound = copy.deepcopy(raw)
        outbound["tag"] = outbound_tag(cc, idx)
        outbound["_remark"] = server.get("raw_name") or COUNTRIES[cc]["title"]
        outbound.setdefault("protocol", "vless")
        for vn in outbound.get("settings", {}).get("vnext", []) or []:
            for u in vn.get("users", []) or []:
                u["id"] = sub_uid
                u.setdefault("encryption", "none")
        return outbound
    # fallback (base64-vless): только tcp+reality
    user = {"id": sub_uid, "encryption": "none"}
    if server.get("flow"):
        user["flow"] = server["flow"]
    return {
        "tag": outbound_tag(cc, idx),
        "_remark": server["raw_name"],
        "protocol": "vless",
        "settings": {"vnext": [{"address": server["host"], "port": server["port"], "users": [user]}]},
        "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
            "serverName": server["sni"],
            "fingerprint": server["fp"],
            "publicKey": server["pbk"],
            "shortId": server["sid"],
            "spiderX": "/",
        }},
    }


def load_profile_config() -> dict:
    raw = psql(f"select config::text from config_profiles where uuid={sql_str(PROFILE_UUID)};").strip()
    if not raw:
        raise RuntimeError("RU Remnawave profile not found")
    return json.loads(raw)


def save_profile_config(cfg: dict) -> None:
    backup = "config_profiles_bak_" + time.strftime("%Y%m%d_%H%M%S") + "_hydra_remna"
    psql(f"create table {backup} as table config_profiles;")
    psql(
        "update config_profiles set "
        f"config={sql_json(cfg)}, updated_at=now() "
        f"where uuid={sql_str(PROFILE_UUID)};"
    )
    print(f"profile backup: {backup}")


def update_profile_config(grouped: dict[str, list[dict]], cfg: dict | None = None, sub_uid: str = "") -> dict:
    if cfg is None:
        cfg = load_profile_config()
    else:
        cfg = json.loads(json.dumps(cfg, ensure_ascii=False))
    cfg["inbounds"] = [
        i for i in cfg.get("inbounds", [])
        if not str(i.get("tag", "")).startswith(("RU_WS_HYDRA_", "GOIDA_HYDRA_"))
    ]
    cfg["outbounds"] = [o for o in cfg.get("outbounds", []) if not str(o.get("tag", "")).startswith("HYDRA_")]
    rules = cfg.setdefault("routing", {}).setdefault("rules", [])
    rules = [
        r for r in rules
        if not any(str(t).startswith(("RU_WS_HYDRA_", "GOIDA_HYDRA_")) for t in r.get("inboundTag", []) or [])
        and not str(r.get("outboundTag", "")).startswith("HYDRA_")
        and not str(r.get("balancerTag", "")).startswith("BALANCER_HYDRA_")
    ]
    balancers = [
        b for b in cfg.setdefault("routing", {}).setdefault("balancers", [])
        if not str(b.get("tag", "")).startswith("BALANCER_HYDRA_")
    ]

    hydra_inbound_tags = [inbound_tag(cc) for cc in grouped]
    for rule in rules:
        if rule.get("ruleTag") in {"direct-ru-ip", "direct-ru-domain"}:
            tags = list(rule.get("inboundTag", []) or [])
            for tag in hydra_inbound_tags:
                if tag not in tags:
                    tags.append(tag)
            rule["inboundTag"] = tags

    balanced_tags: list[str] = []
    for cc, servers in grouped.items():
        cfg["inbounds"].append(make_inbound(cc))
        selectors = []
        for idx, server in enumerate(servers):
            cfg["outbounds"].append(make_outbound(cc, idx, server, sub_uid))
            selectors.append(outbound_tag(cc, idx))
        if len(selectors) > 1:
            balancers.append({
                "tag": balancer_tag(cc),
                "selector": selectors,
                "fallbackTag": selectors[0],
                "strategy": {"type": "leastPing"},
            })
            rules.append({"type": "field", "network": "tcp,udp", "inboundTag": [inbound_tag(cc)], "balancerTag": balancer_tag(cc)})
            balanced_tags.extend(selectors)
        else:
            rules.append({"type": "field", "network": "tcp,udp", "inboundTag": [inbound_tag(cc)], "outboundTag": selectors[0]})

    cfg["routing"]["rules"] = rules
    cfg["routing"]["balancers"] = balancers
    # observatory кормит leastPing-балансеры hydra. Управляем ТОЛЬКО своим (subjectSelector = hydra tags).
    # существующий burstObservatory (REMNA_*) не трогаем — это независимая фича.
    if balanced_tags:
        cfg["observatory"] = {
            "subjectSelector": sorted(set(balanced_tags)),
            "probeUrl": "http://www.gstatic.com/generate_204",
            "probeInterval": "30s",
            "enableConcurrency": True,
        }
    else:
        cfg.pop("observatory", None)
    return cfg


def update_inbound_tables(grouped: dict[str, list[dict]]) -> None:
    node_uuid = psql(f"select uuid from nodes where name={sql_str(RU_NODE_NAME)} limit 1;").strip()
    if not node_uuid:
        raise RuntimeError(f"node not found: {RU_NODE_NAME}")
    active_tags = {inbound_tag(cc) for cc in grouped}
    active_squads = {squad_name(cc) for cc in grouped}
    active_tags_sql = ",".join(sql_str(tag) for tag in active_tags) or "''"
    active_squads_sql = ",".join(sql_str(name) for name in active_squads) or "''"
    psql(
        "delete from internal_squad_members m using internal_squads s "
        "where s.uuid=m.internal_squad_uuid and s.name like 'HYDRA\\_%\\_REMNA' "
        f"and s.name not in ({active_squads_sql});"
    )
    psql(
        "delete from internal_squad_inbounds si using internal_squads s "
        "where s.uuid=si.internal_squad_uuid and s.name like 'HYDRA\\_%\\_REMNA' "
        f"and s.name not in ({active_squads_sql});"
    )
    psql(
        "delete from config_profile_inbounds_to_nodes itn using config_profile_inbounds i "
        "where i.uuid=itn.config_profile_inbound_uuid and (i.tag like 'RU_WS_HYDRA\\_%' or i.tag like 'GOIDA_HYDRA\\_%') "
        f"and i.tag not in ({active_tags_sql});"
    )
    psql(
        "delete from config_profile_inbounds where (tag like 'RU_WS_HYDRA\\_%' or tag like 'GOIDA_HYDRA\\_%') "
        f"and tag not in ({active_tags_sql});"
    )
    for cc in grouped:
        meta = COUNTRIES[cc]
        tag = inbound_tag(cc)
        inbound_uuid = psql(f"select uuid from config_profile_inbounds where tag={sql_str(tag)} limit 1;").strip()
        if not inbound_uuid:
            inbound_uuid = stable_uuid(f"inbound:{tag}")
        squad_uuid = stable_uuid(f"squad:{squad_name(cc)}")
        raw = make_inbound(cc)
        psql(
            "insert into config_profile_inbounds "
            "(uuid,profile_uuid,tag,type,network,security,port,raw_inbound) values "
            f"({sql_str(inbound_uuid)}, {sql_str(PROFILE_UUID)}, {sql_str(tag)}, 'vless', 'ws', 'none', {meta['port']}, {sql_json(raw)}) "
            "on conflict (uuid) do update set "
            "tag=excluded.tag,type=excluded.type,network=excluded.network,security=excluded.security,"
            "port=excluded.port,raw_inbound=excluded.raw_inbound;"
        )
        psql(
            "insert into config_profile_inbounds_to_nodes (config_profile_inbound_uuid,node_uuid) values "
            f"({sql_str(inbound_uuid)}, {sql_str(node_uuid)}) on conflict do nothing;"
        )
        psql(
            "insert into internal_squads (uuid,name,created_at,updated_at,view_position) values "
            f"({sql_str(squad_uuid)}, {sql_str(squad_name(cc))}, now(), now(), 500) "
            "on conflict (uuid) do update set name=excluded.name, updated_at=now();"
        )
        psql(
            "insert into internal_squad_inbounds (internal_squad_uuid,inbound_uuid) values "
            f"({sql_str(squad_uuid)}, {sql_str(inbound_uuid)}) on conflict do nothing;"
        )
        psql(
            "insert into internal_squad_members (internal_squad_uuid,user_id) "
            f"select {sql_str(squad_uuid)}, t_id from users where username={sql_str(USERNAME)} "
            "on conflict do nothing;"
        )
    backfill_hydra_members(active_squads)


def hydra_enabled_usernames() -> set[str]:
    """vpn-bot /hydra — единственный источник правды для hydra membership."""
    if not Path(BOT_DB).exists():
        return set()
    try:
        conn = sqlite3.connect(BOT_DB, timeout=30)
        rows = conn.execute("SELECT name FROM users WHERE hydra_enabled=1").fetchall()
        conn.close()
        return {row[0] for row in rows if row and row[0]}
    except sqlite3.OperationalError:
        return set()


def backfill_hydra_members(active_squads: set[str]) -> None:
    """синхронизирует squads только пользователям с hydra_enabled=1 в bot.db."""
    if not active_squads:
        return
    enabled = hydra_enabled_usernames()
    if not enabled:
        return
    active_squads_sql = ",".join(sql_str(name) for name in active_squads)
    users_sql = ",".join(sql_str(name) for name in sorted(enabled))
    inserted = psql(
        "with inserted as ("
        "  insert into internal_squad_members (internal_squad_uuid,user_id) "
        "  select s.uuid, u.t_id "
        "  from users u "
        "  cross join internal_squads s "
        f"  where u.username in ({users_sql}) and s.name in ({active_squads_sql}) "
        "  on conflict do nothing "
        "  returning 1"
        ") select count(*) from inserted;"
    ).strip()
    if inserted and inserted != "0":
        print(f"hydra members backfilled: {inserted}")


def ensure_nginx(grouped: dict[str, list[dict]]) -> None:
    path = Path(NGINX_CONF)
    content = path.read_text()
    missing = [cc for cc in grouped if f"location ^~ {COUNTRIES[cc]['path']}" not in content]
    if missing:
        print(f"nginx hydra locations missing, manual review required: {missing}", file=sys.stderr)
    run(["nginx", "-t"])


def vless_link(vless_uuid: str, cc: str) -> str:
    meta = COUNTRIES[cc]
    remark = urllib.parse.quote(f"{meta['title']} {meta['flag']}")
    return (
        f"vless://{vless_uuid}@{PUBLIC_HOST}:{PUBLIC_PORT}/"
        f"?type=ws&security=tls&encryption=none&sni={PUBLIC_HOST}&host={PUBLIC_HOST}"
        f"&path={urllib.parse.quote(meta['path'], safe='')}#{remark}"
    )


def restore_device() -> None:
    row = run([
        "sqlite3", BOT_DB,
        "select replace(ip,'hwid:','') || char(9) || user_agent from user_ips "
        f"where token=(select token from users where name='{USERNAME}') and ip like 'hwid:%' "
        "and user_agent like 'Happ/4.10.1/ios/%' order by last_seen desc limit 1;",
    ]).stdout.strip()
    if not row:
        return
    hwid, ua = row.split("\t", 1)
    psql(
        "insert into hwid_user_devices "
        "(hwid,user_uuid,platform,os_version,device_model,user_agent,created_at,updated_at) "
        f"select {sql_str(hwid)}, uuid, 'iOS', '', 'Happ', {sql_str(ua)}, now(), now() "
        f"from users where username={sql_str(USERNAME)} "
        "on conflict (hwid,user_uuid) do update set user_agent=excluded.user_agent, updated_at=now();"
    )
    print(f"restored device: {hwid} {ua}")


# ── whitelists (прямые client→WL ссылки, БЕЗ RU-хопа; vpn-bot читает WL_FILE) ──

def build_wl_lines(wl_profiles: list[dict], sub_uid: str) -> list[str]:
    """каждый WL-профиль из подписки → одна `#goida-wl-json:` строка.

    Сохраняем client-side балансер/observatory/routing из подписки as-is
    (WL должен работать напрямую при жёстких блокировках мобильной связи).
    Нормализуем uid, валидируем живость бэкендов, мёртвые WL пропускаем.
    После фильтрации переназываем оставшиеся по порядку: Whitelist 1🇷🇺, Whitelist 2🇷🇺..."""
    alive: list[dict] = []
    for prof in wl_profiles:
        prof = copy.deepcopy(prof)
        prof.pop("log", None)  # убрать клиентский (macOS) путь логов
        targets: list[tuple[str, int]] = []
        for o in prof.get("outbounds", []) or []:
            if not isinstance(o, dict) or o.get("protocol") != "vless":
                continue
            for vn in o.get("settings", {}).get("vnext", []) or []:
                for u in vn.get("users", []) or []:
                    if sub_uid:
                        u["id"] = sub_uid
                    u.setdefault("encryption", "none")
                host = str(vn.get("address") or "").strip()
                try:
                    port = int(vn.get("port") or 443)
                except (TypeError, ValueError):
                    continue
                if host and 0 < port <= 65535:
                    targets.append((host, port))
        if not targets:
            continue
        if not any(tcp_alive(h, p) for h, p in targets):
            print(f"wl dead, skip: {prof.get('remarks')}", file=sys.stderr)
            continue
        alive.append(prof)

    lines: list[str] = []
    for i, prof in enumerate(alive, 1):
        prof["remarks"] = f"Whitelist {i}\U0001f1f7\U0001f1fa"
        payload = base64.urlsafe_b64encode(
            json.dumps(prof, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).decode().rstrip("=")
        lines.append(WL_JSON_PREFIX + payload)
    return lines


def existing_wl_lines() -> list[str]:
    try:
        return [ln.strip() for ln in Path(WL_FILE).read_text().splitlines() if ln.strip()]
    except FileNotFoundError:
        return []


def write_wl_file(lines: list[str]) -> None:
    path = Path(WL_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    Path(tmp).write_text("\n".join(lines) + "\n")
    os.replace(tmp, WL_FILE)


def main() -> int:
    parser = argparse.ArgumentParser(description="sync hydra+whitelists from subscription into Remnawave/WL-file")
    parser.add_argument("--dry-run", action="store_true", help="печатать план, ничего не мутировать")
    args = parser.parse_args()

    grouped, wl_profiles, sub_uid = load_subscription()
    if not grouped:
        raise RuntimeError("no hydra servers parsed")
    if not sub_uid:
        raise RuntimeError("no subscription uid parsed")

    old_cfg = load_profile_config()
    grouped = preserve_existing_on_probe_flap(grouped, old_cfg)
    print("hydra countries:", {cc: [s["host"] for s in servers] for cc, servers in grouped.items()}, "uid:", sub_uid)

    cfg = update_profile_config(grouped, old_cfg, sub_uid)
    profile_changed = cfg != old_cfg

    wl_lines = build_wl_lines(wl_profiles, sub_uid)
    wl_changed = bool(wl_lines) and set(wl_lines) != set(existing_wl_lines())
    wl_empty_flap = not wl_lines and bool(wl_profiles)  # все WL отвалились по probe → не обнулять файл

    if args.dry_run:
        print(f"[dry-run] profile_changed={profile_changed} wl_changed={wl_changed} wl_entries={len(wl_lines)}")
        for o in cfg.get("outbounds", []):
            t = str(o.get("tag", ""))
            if t.startswith("HYDRA_"):
                ss = o.get("streamSettings", {})
                print(f"  [dry] {t}: net={ss.get('network')} sec={ss.get('security')}")
        for b in cfg.get("routing", {}).get("balancers", []):
            if str(b.get("tag", "")).startswith("BALANCER_HYDRA_"):
                print(f"  [dry] {b['tag']} strategy={b.get('strategy')} sel={b.get('selector')}")
        print("  [dry] observatory:", json.dumps(cfg.get("observatory"), ensure_ascii=False))
        print(f"  [dry] wl backends: {[ (lambda p: p.get('remarks'))(json.loads(base64.urlsafe_b64decode(l[len(WL_JSON_PREFIX):]+'=='))) for l in wl_lines ]}")
        return 0

    # ── hydra актуализация (только при изменении профиля) ──
    if profile_changed:
        update_inbound_tables(grouped)
        save_profile_config(cfg)
        ensure_nginx(grouped)
        restore_device()
        run(["docker", "restart", "remnawave"])
        print("remnawave restarted")
    else:
        print("hydra/profile unchanged")

    # ── whitelists актуализация (вне профиля — рестарт не нужен) ──
    if wl_changed:
        write_wl_file(wl_lines)
        print(f"wl updated: {len(wl_lines)} entries")
    elif wl_empty_flap:
        print("wl empty after probe flap; keeping previous WL file", file=sys.stderr)
    else:
        print("wl unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
