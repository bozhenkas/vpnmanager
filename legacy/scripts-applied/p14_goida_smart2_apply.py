#!/usr/bin/env python3
"""P.14: создаёт GOIDA_SMART2 inbound + routing на prod (GOIDA tags)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from subscription.xhttp import build_xhttp_settings

PROFILE_UUID = os.environ.get("REMNA_RU_PROFILE_UUID", "11111111-7443-4000-8000-000000000001")
SMART = "GOIDA_SMART"
SMART2 = "GOIDA_SMART2"
RESERVE = "GOIDA_RESERVE"
SMART2_PORT = 7443
SMART2_LISTEN = os.environ.get("SMART2_LISTEN", "45.91.53.93")
SMART2_LOOPBACK = int(os.environ.get("SMART2_LOOPBACK", "17449"))
SMART2_SNI = os.environ.get("SMART2_REALITY_SNI", "ok.ru")


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


def gen_reality_keys() -> tuple[str, str, str]:
    out = run(["docker", "exec", "remnanode", "xray", "x25519"]).stdout.strip().splitlines()
    priv = pub = ""
    for line in out:
        if line.startswith("PrivateKey:"):
            priv = line.split(":", 1)[1].strip()
        elif "PublicKey" in line and ":" in line:
            pub = line.split(":", 1)[1].strip()
    if not priv or not pub:
        raise RuntimeError(f"x25519 failed: {out}")
    short_id = format(int.from_bytes(os.urandom(4), "big") & 0xFFFFFFFFFFFF, "x")
    return priv, pub, short_id


def smart2_inbound(private_key: str, short_id: str) -> dict:
    return {
        "tag": SMART2,
        "port": SMART2_PORT,
        "listen": SMART2_LISTEN,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none", "flow": ""},
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
        "streamSettings": {
            "network": "xhttp",
            "security": "reality",
            "xhttpSettings": build_xhttp_settings(path="/smart2", host=SMART2_SNI, mode="stream-one"),
            "realitySettings": {
                "show": False,
                "xver": 0,
                "target": f"{SMART2_SNI}:443",
                "shortIds": [short_id],
                "privateKey": private_key,
                "serverNames": [SMART2_SNI],
                "fingerprint": "chrome",
            },
        },
    }


def add_smart2_inbound(inbound: dict, smart_uuid: str, node_uuid: str) -> str:
    existing = psql(f"SELECT uuid FROM config_profile_inbounds WHERE tag={sql_str(SMART2)};").strip()
    if existing:
        print(f"GOIDA_SMART2 already exists: {existing}")
        return existing
    inbound_uuid = str(uuid.uuid4())
    psql(
        "INSERT INTO config_profile_inbounds "
        "(uuid, profile_uuid, tag, type, network, security, port, raw_inbound) VALUES ("
        f"{sql_str(inbound_uuid)}, {sql_str(PROFILE_UUID)}, {sql_str(SMART2)}, 'vless', 'xhttp', 'reality', "
        f"{SMART2_PORT}, {sql_json(inbound)});"
    )
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
    return inbound_uuid


def patch_routing_add_smart2(cfg: dict) -> list[str]:
    changed: list[str] = []
    rules = cfg.setdefault("routing", {}).setdefault("rules", [])
    skip_tags = {"reserve-fin-catch-all"}
    for rule in rules:
        tag = str(rule.get("ruleTag") or "")
        if tag in skip_tags:
            continue
        inbound = list(rule.get("inboundTag") or [])
        if SMART in inbound and SMART2 not in inbound:
            inbound.append(SMART2)
            rule["inboundTag"] = inbound
            changed.append(f"add {SMART2} to {tag or '<rule>'}")
        elif RESERVE in inbound and SMART2 in inbound:
            inbound.remove(SMART2)
            rule["inboundTag"] = inbound
            changed.append(f"remove {SMART2} from reserve rule {tag}")
    return changed


def load_profile() -> dict:
    raw = psql(f"select config::text from config_profiles where uuid={sql_str(PROFILE_UUID)};").strip()
    return json.loads(raw)


def save_profile(cfg: dict) -> None:
    psql(f"UPDATE config_profiles SET config={sql_json(cfg)}, updated_at=now() WHERE uuid={sql_str(PROFILE_UUID)};")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reason", default="goida_smart2")
    args = parser.parse_args()

    smart_uuid = psql(f"SELECT uuid FROM config_profile_inbounds WHERE tag={sql_str(SMART)};").strip()
    node_uuid = psql(
        f"SELECT node_uuid FROM config_profile_inbounds_to_nodes WHERE config_profile_inbound_uuid={sql_str(smart_uuid)} LIMIT 1;"
    ).strip()
    if not smart_uuid or not node_uuid:
        raise RuntimeError("GOIDA_SMART inbound/node mapping not found")

    priv, pub, sid = gen_reality_keys()
    inbound = smart2_inbound(priv, sid)
    cfg = load_profile()
    routing_changes = patch_routing_add_smart2(cfg)

    print(f"SMART2 listen={SMART2_LISTEN}:{SMART2_PORT} path=/smart2 sni={SMART2_SNI}")
    print(f"SMART2_REALITY_PBK={pub}")
    print(f"SMART2_REALITY_SID={sid}")
    print(f"routing changes: {routing_changes or ['none']}")

    if not args.apply:
        print("DRY RUN")
        return 0

    backup = backup_tables(args.reason)
    print(f"backup suffix: {backup}")
    inbound_uuid = add_smart2_inbound(inbound, smart_uuid, node_uuid)
    save_profile(cfg)
    print(f"created inbound {inbound_uuid}")
    run(["docker", "restart", "remnawave"])
    time.sleep(8)
    run(["docker", "restart", "remnanode"])
    print("restarted remnawave + remnanode")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
