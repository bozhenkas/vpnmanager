#!/usr/bin/env python3
"""Restore GOIDA_SMART2 xhttp from pre-grpc DB backup (only SMART2, nothing else)."""
from __future__ import annotations

import argparse
import json
import subprocess
import time

PROFILE = "11111111-7443-4000-8000-000000000001"
SMART2 = "GOIDA_SMART2"
# последний xhttp backup до grpc-миграции
INBOUND_BAK = "config_profile_inbounds_bak_20260614_015739_smart2_neo_deploy2"
PROFILE_BAK = "config_profiles_bak_20260614_015739_smart2_neo_deploy2"


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    restored = json.loads(psql(
        f"SELECT raw_inbound::text FROM {INBOUND_BAK} WHERE tag={sql_str(SMART2)};"
    ).strip())
    net, sec, port = psql(
        f"SELECT network, security, port FROM {INBOUND_BAK} WHERE tag={sql_str(SMART2)};"
    ).strip().split("|")

    cfg = json.loads(psql(f"SELECT config::text FROM config_profiles WHERE uuid={sql_str(PROFILE)};"))
    bak_cfg = json.loads(psql(f"SELECT config::text FROM {PROFILE_BAK} WHERE uuid={sql_str(PROFILE)};"))
    bak_ib = next(ib for ib in bak_cfg.get("inbounds", []) if ib.get("tag") == SMART2)
    merged = False
    for ib in cfg.get("inbounds", []):
        if ib.get("tag") == SMART2:
            ib.clear()
            ib.update(bak_ib)
            merged = True
    if not merged:
        raise RuntimeError(f"{SMART2} not found in live config_profiles")

    print(f"restore {SMART2} from {INBOUND_BAK}")
    print(f"network={net} security={sec} port={port} listen={restored.get('listen')}")
    xh = restored.get("streamSettings", {}).get("xhttpSettings", {})
    print(f"xhttp path={xh.get('path')} mode={xh.get('mode')} host={xh.get('host')}")

    if not args.apply:
        print("DRY RUN")
        return 0

    suffix = backup_tables("smart2_xhttp_restore")
    print(f"pre-restore backup suffix: {suffix}")
    psql(
        f"UPDATE config_profile_inbounds SET "
        f"raw_inbound={sql_json(restored)}, network={sql_str(net)}, security={sql_str(sec)}, port={int(port)} "
        f"WHERE tag={sql_str(SMART2)};"
    )
    psql(f"UPDATE config_profiles SET config={sql_json(cfg)}, updated_at=now() WHERE uuid={sql_str(PROFILE)};")
    run(["docker", "restart", "remnawave"])
    time.sleep(8)
    run(["docker", "restart", "remnanode"])
    print("restarted remnawave + remnanode")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
