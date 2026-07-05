#!/usr/bin/env python3
"""GOIDA_SMART2: grpc+reality на backup IP :7443 (443 занят nginx, Happ не тянет xhttp)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time

PROFILE = os.environ.get("REMNA_RU_PROFILE_UUID", "11111111-7443-4000-8000-000000000001")
SMART2 = "GOIDA_SMART2"
SMART2_LISTEN = os.environ.get("SMART2_LISTEN", "45.91.53.93")
SMART2_PORT = int(os.environ.get("SMART2_PORT", "7443"))
SMART2_SNI = os.environ.get("SMART2_REALITY_SNI", "ok.ru")
SMART2_SERVICE = os.environ.get("SMART2_GRPC_SERVICE", "smart2")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
    for table in ("config_profile_inbounds", "config_profiles"):
        psql(f"CREATE TABLE {table}_bak_{suffix} AS TABLE {table};")
    return suffix


def patch_smart2_inbound(inbound: dict) -> list[str]:
    changed: list[str] = []
    ss = inbound.setdefault("streamSettings", {})
    if ss.get("network") != "grpc":
        ss["network"] = "grpc"
        changed.append("network->grpc")
    if ss.get("security") != "reality":
        ss["security"] = "reality"
        changed.append("security->reality")
    ss.pop("xhttpSettings", None)
    if "xhttpSettings" not in ss:
        changed.append("drop-xhttpSettings")
    want_grpc = {"serviceName": SMART2_SERVICE, "multiMode": False}
    if ss.get("grpcSettings") != want_grpc:
        ss["grpcSettings"] = want_grpc
        changed.append(f"grpcSettings->{SMART2_SERVICE}")
    reality = ss.setdefault("realitySettings", {})
    target = f"{SMART2_SNI}:443"
    if reality.get("target") != target:
        reality["target"] = target
        changed.append(f"reality.target->{target}")
    names = [SMART2_SNI]
    if reality.get("serverNames") != names:
        reality["serverNames"] = names
        changed.append(f"reality.serverNames->{names}")
    if inbound.get("port") != SMART2_PORT:
        inbound["port"] = SMART2_PORT
        changed.append(f"port->{SMART2_PORT}")
    if inbound.get("listen") != SMART2_LISTEN:
        inbound["listen"] = SMART2_LISTEN
        changed.append(f"listen->{SMART2_LISTEN}")
    inbound.setdefault("settings", {})["flow"] = ""
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reason", default="smart2_grpc")
    args = parser.parse_args()

    inbound_uuid = psql(f"SELECT uuid FROM config_profile_inbounds WHERE tag={sql_str(SMART2)};").strip()
    if not inbound_uuid:
        raise RuntimeError(f"{SMART2} inbound not found")

    inbound = json.loads(psql(
        f"SELECT raw_inbound::text FROM config_profile_inbounds WHERE uuid={sql_str(inbound_uuid)};"
    ).strip())
    inbound_changes = patch_smart2_inbound(inbound)

    cfg = json.loads(psql(f"SELECT config::text FROM config_profiles WHERE uuid={sql_str(PROFILE)};").strip())
    profile_changes: list[str] = []
    for ib in cfg.get("inbounds", []):
        if ib.get("tag") != SMART2:
            continue
        profile_changes.extend(patch_smart2_inbound(ib))

    print(f"listen={SMART2_LISTEN}:{SMART2_PORT} grpc service={SMART2_SERVICE} sni={SMART2_SNI}")
    print(f"inbound changes: {inbound_changes or ['none']}")
    print(f"profile changes: {profile_changes or ['none']}")
    if not args.apply:
        print("DRY RUN")
        return 0

    backup = backup_tables(args.reason)
    print(f"backup suffix: {backup}")
    psql(
        f"UPDATE config_profile_inbounds SET raw_inbound={sql_json(inbound)}, "
        f"network='grpc', security='reality', port={SMART2_PORT} "
        f"WHERE uuid={sql_str(inbound_uuid)};"
    )
    psql(
        f"UPDATE config_profiles SET config={sql_json(cfg)}, updated_at=now() "
        f"WHERE uuid={sql_str(PROFILE)};"
    )
    run(["docker", "restart", "remnawave"])
    time.sleep(8)
    run(["docker", "restart", "remnanode"])
    print("restarted remnawave + remnanode")
    print(f"set SMART2_PORT={SMART2_PORT} in /root/vpn-bot/.env and restart vpn-bot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
