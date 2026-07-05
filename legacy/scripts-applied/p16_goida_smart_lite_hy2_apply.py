#!/usr/bin/env python3
"""P.16: создаёт GOIDA_SMART_LITE (Hysteria2+salamander) inbound + routing на prod."""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import ssl
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

PROFILE_UUID = os.environ.get("REMNA_RU_PROFILE_UUID", "11111111-7443-4000-8000-000000000001")
SMART = "GOIDA_SMART"
SMART2 = "GOIDA_SMART2"
LITE = "GOIDA_SMART_LITE"
RESERVE = "GOIDA_RESERVE"
LITE_PORT = int(os.environ.get("SMART_LITE_PORT", "8443"))
LITE_LISTEN = os.environ.get("SMART_LITE_HOST", "45.91.53.93")
LITE_SNI = os.environ.get("SMART_LITE_SNI", "ru.goida.fun")
LITE_OBFS_PASSWORD = os.environ.get("SMART_LITE_OBFS_PASSWORD", "")
CERT_DIR = "/etc/letsencrypt/live/ru.goida.fun"


def run(cmd: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{proc.stderr or proc.stdout}")
    return proc


def psql(sql: str) -> str:
    return run([
        "docker", "exec", "-i", "remnawave-db",
        "psql", "-U", "postgres", "-d", "postgres", "-At", "-c", sql,
    ]).stdout


def sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_json(value: object) -> str:
    return "$json$" + json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "$json$::jsonb"


def backup_tables(reason: str) -> str:
    suffix = time.strftime("%Y%m%d_%H%M%S") + "_" + reason
    for table in ("config_profile_inbounds", "config_profile_inbounds_to_nodes", "config_profiles", "internal_squad_inbounds"):
        psql(f"CREATE TABLE {table}_bak_{suffix} AS TABLE {table};")
    return suffix


def smart_lite_inbound(obfs_password: str) -> dict:
    return {
        "tag": LITE,
        "port": LITE_PORT,
        "listen": LITE_LISTEN,
        "protocol": "hysteria",
        "settings": {
            "version": 2,
            "clients": [],
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
        "streamSettings": {
            "network": "hysteria",
            "security": "tls",
            "tlsSettings": {
                "certificates": [
                    {
                        "certificateFile": f"{CERT_DIR}/fullchain.pem",
                        "keyFile": f"{CERT_DIR}/privkey.pem",
                    }
                ],
                "alpn": ["h3"],
            },
            "hysteriaSettings": {"version": 2},
            "finalmask": {
                "udp": [
                    {
                        "type": "salamander",
                        "settings": {"password": obfs_password},
                    }
                ]
            },
        },
    }


def ensure_node_and_squads(inbound_uuid: str, smart_uuid: str, node_uuid: str) -> None:
    psql(
        "INSERT INTO config_profile_inbounds_to_nodes (config_profile_inbound_uuid, node_uuid) VALUES ("
        f"{sql_str(inbound_uuid)}, {sql_str(node_uuid)}) ON CONFLICT DO NOTHING;"
    )
    squad_rows = psql(
        f"SELECT internal_squad_uuid FROM internal_squad_inbounds WHERE inbound_uuid={sql_str(smart_uuid)};"
    ).strip().splitlines()
    for squad_uuid in squad_rows:
        if squad_uuid:
            psql(
                "INSERT INTO internal_squad_inbounds (internal_squad_uuid, inbound_uuid) VALUES ("
                f"{sql_str(squad_uuid)}, {sql_str(inbound_uuid)}) ON CONFLICT DO NOTHING;"
            )


def add_smart_lite_inbound(inbound: dict, smart_uuid: str, node_uuid: str) -> str:
    existing = psql(f"SELECT uuid FROM config_profile_inbounds WHERE tag={sql_str(LITE)};").strip()
    if existing:
        psql(
            "UPDATE config_profile_inbounds SET "
            f"type='hysteria', network='hysteria', security='tls', port={LITE_PORT}, "
            f"raw_inbound={sql_json(inbound)} WHERE uuid={sql_str(existing)};"
        )
        ensure_node_and_squads(existing, smart_uuid, node_uuid)
        print(f"{LITE} updated: {existing}")
        return existing
    inbound_uuid = str(uuid.uuid4())
    psql(
        "INSERT INTO config_profile_inbounds "
        "(uuid, profile_uuid, tag, type, network, security, port, raw_inbound) VALUES ("
        f"{sql_str(inbound_uuid)}, {sql_str(PROFILE_UUID)}, {sql_str(LITE)}, 'hysteria', 'hysteria', 'tls', "
        f"{LITE_PORT}, {sql_json(inbound)});"
    )
    ensure_node_and_squads(inbound_uuid, smart_uuid, node_uuid)
    return inbound_uuid


def patch_routing_add_lite(cfg: dict) -> list[str]:
    changed: list[str] = []
    rules = cfg.setdefault("routing", {}).setdefault("rules", [])
    skip_tags = {"reserve-fin-catch-all"}
    for rule in rules:
        tag = str(rule.get("ruleTag") or "")
        if tag in skip_tags:
            continue
        inbound = list(rule.get("inboundTag") or [])
        if SMART in inbound and LITE not in inbound:
            inbound.append(LITE)
            rule["inboundTag"] = inbound
            changed.append(f"add {LITE} to {tag or '<rule>'}")
        elif RESERVE in inbound and LITE in inbound:
            inbound.remove(LITE)
            rule["inboundTag"] = inbound
            changed.append(f"remove {LITE} from reserve rule {tag}")
    return changed


def patch_config_inbounds(cfg: dict, inbound: dict) -> None:
    inbounds = [ib for ib in cfg.get("inbounds", []) if ib.get("tag") != LITE]
    inbounds.append(inbound)
    cfg["inbounds"] = inbounds


def restart_ru_node(node_uuid: str) -> None:
    token = os.environ.get("REMNAWAVE_API_TOKEN", "").strip()
    if not token:
        token = psql("SELECT token FROM api_tokens ORDER BY created_at DESC LIMIT 1;").strip()
    basic = os.environ.get("REMNAWAVE_PANEL_BASIC_AUTH", "goida:lGKOvakfRNdZUJrKRM")
    url = f"https://127.0.0.1:30080/api/nodes/{node_uuid}/actions/restart"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if basic:
        headers["X-Goida-Basic-Auth"] = "Basic " + base64.b64encode(basic.encode()).decode()
    req = urllib.request.Request(url, data=b"{}", method="POST", headers=headers)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            print(f"node restart api: {resp.status}")
    except Exception as exc:
        print(f"node restart api failed: {exc}", file=sys.stderr)
        run(["docker", "restart", "remnanode"])


def load_profile() -> dict:
    raw = psql(f"select config::text from config_profiles where uuid={sql_str(PROFILE_UUID)};").strip()
    return json.loads(raw)


def save_profile(cfg: dict) -> None:
    psql(f"UPDATE config_profiles SET config={sql_json(cfg)}, updated_at=now() WHERE uuid={sql_str(PROFILE_UUID)};")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reason", default="goida_smart_lite_hy2")
    args = parser.parse_args()

    obfs_password = LITE_OBFS_PASSWORD or secrets.token_hex(16)
    smart_uuid = psql(f"SELECT uuid FROM config_profile_inbounds WHERE tag={sql_str(SMART)};").strip()
    node_uuid = psql(
        f"SELECT node_uuid FROM config_profile_inbounds_to_nodes WHERE config_profile_inbound_uuid={sql_str(smart_uuid)} LIMIT 1;"
    ).strip()
    if not smart_uuid or not node_uuid:
        raise RuntimeError("GOIDA_SMART inbound/node mapping not found")

    inbound = smart_lite_inbound(obfs_password)
    cfg = load_profile()
    routing_changes = patch_routing_add_lite(cfg)

    print(f"LITE listen={LITE_LISTEN}:{LITE_PORT} sni={LITE_SNI} obfs=salamander")
    print(f"SMART_LITE_OBFS_PASSWORD={obfs_password}")
    print(f"routing changes: {routing_changes or ['none']}")

    if not args.apply:
        print("DRY RUN")
        return 0

    backup = backup_tables(args.reason)
    print(f"backup suffix: {backup}")
    inbound_uuid = add_smart_lite_inbound(inbound, smart_uuid, node_uuid)
    patch_config_inbounds(cfg, inbound)
    save_profile(cfg)
    print(f"inbound uuid: {inbound_uuid}")
    restart_ru_node(node_uuid)
    time.sleep(10)
    print("node restart requested (API, no remnawave restart)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
