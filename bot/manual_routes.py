#!/usr/bin/env python3
"""ручные domain-правила бота для legacy x-ui и remnawave."""

from __future__ import annotations

import datetime
import json
import sqlite3
import subprocess
from pathlib import Path
import ipaddress


TABLE_SQL = """
CREATE TABLE IF NOT EXISTS manual_domain_rules (
    domain TEXT PRIMARY KEY,
    rule TEXT NOT NULL CHECK(rule IN ('direct','home','foreign')),
    updated_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'telegram-bot'
)
"""

REMNA_PROFILE = "ru-ws-ingress"
REMNA_SMART = "RU_WS_SMART"
REMNA_FIN = "RU_WS_FIN"
REMNA_FRA = "RU_WS_FRA"
REMNA_SWE = "RU_WS_SWE"
REMNA_DIRECT = "RU_WS_DIRECT"
REMNA_HOME = "RU_WS_HOME"
REMNA_HOME_OUT = "REMNA_HOME"
REMNA_RESERVE = "RU_REALITY_GRPC_RESERVE"
# P.15: reserve не в smart-балансере, catch-all -> REMNA_FI
REMNA_SMART_INBOUNDS = [REMNA_SMART]
REMNA_FIXED_INBOUNDS = [REMNA_FIN, REMNA_FRA, REMNA_SWE]
REMNA_CLIENT_INBOUNDS = REMNA_SMART_INBOUNDS + REMNA_FIXED_INBOUNDS + [REMNA_DIRECT]
REMNA_CLUSTER_INBOUNDS = REMNA_CLIENT_INBOUNDS + [REMNA_RESERVE]
RU_SAFETY_IPS = [
    "45.91.54.152/32",
    "83.147.255.0/24",
    "194.117.80.94/32",
    "78.107.88.21/32",
]
CLUSTER_DIRECT_DOMAINS = [
    "domain:ru.goida.fun",
    "domain:web.goida.fun",
    "domain:reserve.goida.fun",
    "domain:ru-4.goida.fun",
    "domain:fin.goida.fun",
    "domain:swe.goida.fun",
    "domain:fra.goida.fun",
]


def normalize_domain(domain: str) -> str:
    domain = domain.strip().lower()
    return domain.split(":", 1)[1] if domain.startswith("domain:") else domain


def xray_domain(domain: str) -> str:
    domain = normalize_domain(domain)
    return domain if domain.startswith(("geosite:", "regexp:", "keyword:", "full:")) else f"domain:{domain}"


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(TABLE_SQL)


def upsert_rule(db_path: str, domain: str, rule: str, *, source: str = "telegram-bot") -> None:
    if rule not in {"direct", "home", "foreign"}:
        raise ValueError(f"unknown manual route: {rule}")
    domain = normalize_domain(domain)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(db_path, timeout=30)
    ensure_table(conn)
    conn.execute(
        """
        INSERT INTO manual_domain_rules(domain, rule, updated_at, source)
        VALUES(?,?,?,?)
        ON CONFLICT(domain) DO UPDATE SET
            rule=excluded.rule,
            updated_at=excluded.updated_at,
            source=excluded.source
        """,
        (domain, rule, now, source),
    )
    conn.commit()
    conn.close()


def get_rule(db_path: str, domain: str) -> str | None:
    domain = normalize_domain(domain)
    conn = sqlite3.connect(db_path, timeout=30)
    ensure_table(conn)
    row = conn.execute("SELECT rule FROM manual_domain_rules WHERE domain=?", (domain,)).fetchone()
    conn.close()
    return row[0] if row else None


def grouped_rules(db_path: str) -> dict[str, list[str]]:
    conn = sqlite3.connect(db_path, timeout=30)
    ensure_table(conn)
    rows = conn.execute("SELECT domain, rule FROM manual_domain_rules ORDER BY domain").fetchall()
    conn.close()
    grouped: dict[str, list[str]] = {"home": [], "direct": [], "foreign": []}
    for domain, rule in rows:
        if rule in grouped:
            grouped[rule].append(xray_domain(domain))
    return grouped


def split_xray_domains_and_ips(items: list[str]) -> tuple[list[str], list[str]]:
    domains: list[str] = []
    ips: list[str] = []
    for item in items:
        value = normalize_domain(item)
        try:
            ipaddress.ip_network(value, strict=False)
            ips.append(value)
        except ValueError:
            domains.append(xray_domain(value))
    return domains, ips


def import_legacy_manual_rules(db_path: str, xui_db: str) -> dict[str, int]:
    xui = sqlite3.connect(xui_db, timeout=30)
    row = xui.execute("SELECT value FROM settings WHERE key='xrayTemplateConfig'").fetchone()
    xui.close()
    if not row:
        return {"home": 0, "direct": 0, "foreign": 0}
    cfg = json.loads(row[0])
    imported = {"home": 0, "direct": 0, "foreign": 0}
    for item in cfg.get("routing", {}).get("rules", []):
        if not str(item.get("ruleTag", "")).startswith("manual-"):
            continue
        target = item.get("outboundTag") or item.get("balancerTag") or ""
        if target == "home-mac-exit":
            rule = "home"
        elif target == "balancer-smart" or str(target).startswith("proxy-"):
            rule = "foreign"
        elif "direct" in str(target):
            rule = "direct"
        else:
            continue
        for domain in item.get("domain") or []:
            upsert_rule(db_path, domain, rule, source="legacy-xui-import")
            imported[rule] += 1
    return imported


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, text=True, **kwargs)


def _load_remna_profile() -> dict:
    proc = _run(
        [
            "docker",
            "exec",
            "remnawave-db",
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-Atc",
            f"SELECT config::text FROM config_profiles WHERE name='{REMNA_PROFILE}'",
        ],
        capture_output=True,
    )
    return json.loads(proc.stdout)


def _build_remna_rules(grouped: dict[str, list[str]]) -> list[dict]:
    smart = list(REMNA_SMART_INBOUNDS)
    client_inbounds = list(REMNA_CLIENT_INBOUNDS)
    rules: list[dict] = [
        {"type": "field", "ip": ["geoip:private"], "outboundTag": "DIRECT"},
        {"type": "field", "domain": ["geosite:private"], "outboundTag": "DIRECT"},
        {"type": "field", "protocol": ["bittorrent"], "outboundTag": "BLOCK"},
        {"type": "field", "inboundTag": [REMNA_HOME], "outboundTag": REMNA_HOME_OUT},
    ]

    home_domains, home_ips = split_xray_domains_and_ips(grouped["home"])
    direct_domains, direct_ips = split_xray_domains_and_ips(grouped["direct"])
    foreign_domains, _foreign_ips = split_xray_domains_and_ips(grouped["foreign"])
    if home_domains:
        rules.append({"type": "field", "ruleTag": "manual-home", "inboundTag": client_inbounds, "domain": home_domains, "outboundTag": REMNA_HOME_OUT})
    if home_ips:
        rules.append({"type": "field", "ruleTag": "manual-home-ip", "inboundTag": client_inbounds, "ip": home_ips, "outboundTag": REMNA_HOME_OUT})
    if direct_domains:
        rules.append({"type": "field", "ruleTag": "manual-direct", "inboundTag": client_inbounds, "domain": direct_domains, "outboundTag": "DIRECT"})
    if direct_ips:
        rules.append({"type": "field", "ruleTag": "manual-direct-ip", "inboundTag": client_inbounds, "ip": direct_ips, "outboundTag": "DIRECT"})
    service_domains = [
        "geosite:youtube", "geosite:discord", "geosite:telegram",
        "domain:googlevideo.com", "domain:youtube.com", "domain:ytimg.com", "domain:youtu.be",
        "domain:t.me", "domain:telegram.org",
        "domain:discord.com", "domain:discord.gg", "domain:discordapp.com", "domain:discordapp.net",
    ]
    telegram_ips = [
        "91.108.4.0/22", "91.108.8.0/22", "91.108.12.0/22",
        "91.108.16.0/22", "91.108.20.0/22", "91.108.56.0/22",
        "95.161.64.0/20", "149.154.160.0/20", "185.76.151.0/24",
    ]
    rules.extend(
        [
            {"type": "field", "ruleTag": "block-youtube-quic", "inboundTag": smart + [REMNA_DIRECT], "domain": ["geosite:youtube", "domain:googlevideo.com", "domain:youtube.com", "domain:ytimg.com", "domain:youtu.be"], "network": "udp", "port": "443", "outboundTag": "BLOCK"},
            {"type": "field", "ruleTag": "direct-zapret-services-domain", "inboundTag": smart, "domain": service_domains, "outboundTag": "DIRECT"},
            {"type": "field", "ruleTag": "direct-telegram-ip", "inboundTag": smart, "ip": telegram_ips, "outboundTag": "DIRECT"},
            {"type": "field", "ruleTag": "direct-discord-voice-1", "inboundTag": smart, "network": "udp", "port": "19294-19344", "outboundTag": "DIRECT"},
            {"type": "field", "ruleTag": "direct-discord-voice-2", "inboundTag": smart, "network": "udp", "port": "50000-65535", "outboundTag": "DIRECT"},
        ]
    )
    if foreign_domains:
        rules.append({"type": "field", "ruleTag": "manual-foreign", "inboundTag": smart, "domain": foreign_domains, "balancerTag": "BALANCER_FOREIGN_SMART"})
    rules.extend(
        [
            {"type": "field", "ruleTag": "direct-goida-cluster-domain", "inboundTag": REMNA_CLUSTER_INBOUNDS, "domain": CLUSTER_DIRECT_DOMAINS, "outboundTag": "DIRECT"},
            {"type": "field", "ruleTag": "direct-goida-cluster-ip", "inboundTag": REMNA_CLUSTER_INBOUNDS, "ip": RU_SAFETY_IPS, "outboundTag": "DIRECT"},
            {"type": "field", "ruleTag": "direct-ru-domain", "inboundTag": client_inbounds, "domain": ["geosite:category-ru", "geosite:private"], "outboundTag": "DIRECT"},
            {"type": "field", "ruleTag": "direct-ru-ip", "inboundTag": client_inbounds, "ip": ["geoip:ru", "139.45.0.0/16"], "outboundTag": "DIRECT"},
            {"type": "field", "ruleTag": "direct-catch-all", "inboundTag": [REMNA_DIRECT], "network": "tcp,udp", "outboundTag": "DIRECT"},
            {"type": "field", "inboundTag": [REMNA_FIN], "network": "tcp,udp", "outboundTag": "REMNA_FI"},
            {"type": "field", "inboundTag": [REMNA_FRA], "network": "tcp,udp", "outboundTag": "REMNA_FRA"},
            {"type": "field", "inboundTag": [REMNA_SWE], "network": "tcp,udp", "outboundTag": "REMNA_SWE"},
            {"type": "field", "ruleTag": "reserve-fin-catch-all", "inboundTag": [REMNA_RESERVE], "network": "tcp,udp", "outboundTag": "REMNA_FI"},
            {"type": "field", "ruleTag": "foreign-smart-catch-all", "inboundTag": smart, "network": "tcp,udp", "balancerTag": "BALANCER_FOREIGN_SMART"},
        ]
    )
    return rules


def _is_hydra_rule(rule: dict) -> bool:
    tags = rule.get("inboundTag") or []
    target = str(rule.get("outboundTag") or rule.get("balancerTag") or "")
    return (
        any(str(tag).startswith("RU_WS_HYDRA_") for tag in tags)
        or target.startswith("HYDRA_")
        or target.startswith("BALANCER_HYDRA_")
    )


def sync_remnawave(db_path: str) -> bool:
    if not Path("/usr/bin/docker").exists() and not Path("/bin/docker").exists():
        return False
    grouped = grouped_rules(db_path)
    config = _load_remna_profile()
    routing = config.setdefault("routing", {})
    hydra_rules = [rule for rule in routing.get("rules", []) if _is_hydra_rule(rule)]
    routing["rules"] = _build_remna_rules(grouped) + hydra_rules
    routing["domainStrategy"] = "IPIfNonMatch"
    tmp = "/tmp/remna-ru-profile-bot-sync.json"
    with open(tmp, "w") as f:
        json.dump(config, f, ensure_ascii=False)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"config_profiles_bak_{stamp}_bot_manual_routes"
    _run(["docker", "exec", "remnawave-db", "psql", "-U", "postgres", "-d", "postgres", "-c", f"CREATE TABLE {backup} AS TABLE config_profiles"])
    _run(["docker", "cp", tmp, "remnawave-db:/tmp/remna-ru-profile-bot-sync.json"])
    _run(
        [
            "docker", "exec", "remnawave-db", "psql", "-U", "postgres", "-d", "postgres",
            "-c",
            "UPDATE config_profiles SET config=(pg_read_file('/tmp/remna-ru-profile-bot-sync.json'))::jsonb, updated_at=now() WHERE name='ru-ws-ingress'",
        ]
    )
    return True
