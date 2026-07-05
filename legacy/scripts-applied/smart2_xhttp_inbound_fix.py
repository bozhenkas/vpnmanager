#!/usr/bin/env python3
"""Hotfix GOIDA_SMART2: xhttp stream-one + host/extra + ok.ru SNI на prod."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for candidate in (Path("/root/vpn-bot"),):
    if (candidate / "subscription" / "xhttp.py").exists():
        sys.path.insert(0, str(candidate))
        break
from subscription.xhttp import build_xhttp_settings

PROFILE = os.environ.get("REMNA_RU_PROFILE_UUID", "11111111-7443-4000-8000-000000000001")
SMART2 = "GOIDA_SMART2"
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
    for table in ("config_profile_inbounds", "config_profiles"):
        psql(f"CREATE TABLE {table}_bak_{suffix} AS TABLE {table};")
    return suffix


def patch_smart2_inbound(inbound: dict) -> list[str]:
    changed: list[str] = []
    ss = inbound.setdefault("streamSettings", {})
    ss["network"] = "xhttp"
    ss["security"] = "reality"
    ss.pop("grpcSettings", None)
    want_xhttp = build_xhttp_settings(path="/smart2", host=SMART2_SNI, mode="stream-one")
    if ss.get("xhttpSettings") != want_xhttp:
        ss["xhttpSettings"] = want_xhttp
        changed.append("xhttpSettings->stream-one+extra")
    reality = ss.setdefault("realitySettings", {})
    target = f"{SMART2_SNI}:443"
    if reality.get("target") != target:
        reality["target"] = target
        changed.append(f"reality.target->{target}")
    names = [SMART2_SNI]
    if reality.get("serverNames") != names:
        reality["serverNames"] = names
        changed.append(f"reality.serverNames->{names}")
    if inbound.get("port") != 7443:
        inbound["port"] = 7443
        changed.append("port->7443")
    if inbound.get("listen") != "45.91.53.93":
        inbound["listen"] = "45.91.53.93"
        changed.append("listen->45.91.53.93")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reason", default="smart2_xhttp_fix")
    args = parser.parse_args()

    inbound_uuid = psql(f"SELECT uuid FROM config_profile_inbounds WHERE tag={sql_str(SMART2)};").strip()
    if not inbound_uuid:
        raise RuntimeError(f"{SMART2} inbound not found")

    raw_inbound = psql(
        f"SELECT raw_inbound::text FROM config_profile_inbounds WHERE uuid={sql_str(inbound_uuid)};"
    ).strip()
    inbound = json.loads(raw_inbound)
    inbound_changes = patch_smart2_inbound(inbound)

    cfg = json.loads(psql(f"SELECT config::text FROM config_profiles WHERE uuid={sql_str(PROFILE)};").strip())
    profile_changes: list[str] = []
    for ib in cfg.get("inbounds", []):
        if ib.get("tag") != SMART2:
            continue
        profile_changes.extend(patch_smart2_inbound(ib))

    print(f"sni={SMART2_SNI}")
    print(f"inbound changes: {inbound_changes or ['none']}")
    print(f"profile changes: {profile_changes or ['none']}")
    if not args.apply:
        print("DRY RUN")
        return 0

    backup = backup_tables(args.reason)
    print(f"backup suffix: {backup}")
    psql(
        f"UPDATE config_profile_inbounds SET raw_inbound={sql_json(inbound)} "
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
    print(f"sync SMART2_REALITY_SNI={SMART2_SNI} in /root/vpn-bot/.env and restart vpn-bot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
