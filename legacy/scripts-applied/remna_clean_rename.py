#!/usr/bin/env python3
"""clean-name migration for RU Remnawave ingress tags."""

from __future__ import annotations

import json
import subprocess
import sys
import time


PROFILE_UUID = "11111111-7443-4000-8000-000000000001"
OLD_PROFILE_NAME = "ru-xhttp-ingress-7443"
NEW_PROFILE_NAME = "ru-ws-ingress"

RENAMES = {
    "REMNA_XHTTP_SMART_7443": "RU_WS_SMART",
    "REMNA_XHTTP_DIRECT_7443": "RU_WS_DIRECT",
    "REMNA_XHTTP_FIN_7443": "RU_WS_FIN",
    "REMNA_XHTTP_FRA_7443": "RU_WS_FRA",
    "REMNA_XHTTP_SWE_7443": "RU_WS_SWE",
    "REMNA_XHTTP_HOME_PLACEHOLDER_7443": "RU_WS_HOME",
    "SMART_REMNA_RU_REALITY_7443": "RU_REALITY_GRPC_RESERVE",
    "REMNA_HYDRA_NL_7443": "RU_WS_HYDRA_NL",
    "REMNA_HYDRA_DE_7443": "RU_WS_HYDRA_DE",
    "REMNA_HYDRA_POL_7443": "RU_WS_HYDRA_POL",
    "REMNA_HYDRA_TUR_7443": "RU_WS_HYDRA_TUR",
    "BALANCER_REMNA_SMART": "BALANCER_FOREIGN_SMART",
    "goida-block-youtube-quic-remna": "block-youtube-quic",
    "remna-smart-zapret-services-domain": "direct-zapret-services-domain",
    "remna-smart-telegram-ip-zapret": "direct-telegram-ip",
    "remna-smart-discord-voice-zapret-1": "direct-discord-voice-1",
    "remna-smart-discord-voice-zapret-2": "direct-discord-voice-2",
    "remna-cluster-ru-domain-direct": "direct-goida-cluster-domain",
    "remna-cluster-ru-ip-direct": "direct-goida-cluster-ip",
    "remna-user-ru-domain-direct": "direct-ru-domain",
    "remna-user-ru-ip-direct": "direct-ru-ip",
    "remna-direct-zapret-catch-all": "direct-catch-all",
    "remna-smart-catch-all": "foreign-smart-catch-all",
    "manual-home-remna": "manual-home",
    "manual-home-ip-remna": "manual-home-ip",
    "manual-direct-remna": "manual-direct",
    "manual-direct-ip-remna": "manual-direct-ip",
    "manual-foreign-remna": "manual-foreign",
    "manual-foreign-ip-remna": "manual-foreign-ip",
}


def run(cmd: list[str], *, input_text: str | None = None) -> str:
    proc = subprocess.run(cmd, input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{proc.stderr or proc.stdout}")
    return proc.stdout


def psql(sql: str) -> str:
    return run([
        "docker", "exec", "-i", "remnawave-db",
        "psql", "-U", "postgres", "-d", "postgres", "-At", "-c", sql,
    ])


def sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_json(value: object) -> str:
    return "$json$" + json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "$json$::jsonb"


def replace_value(value):
    if isinstance(value, str):
        return RENAMES.get(value, value)
    if isinstance(value, list):
        return [replace_value(item) for item in value]
    if isinstance(value, dict):
        return {key: replace_value(item) for key, item in value.items()}
    return value


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    stamp = time.strftime("%Y%m%d_%H%M%S")
    raw = psql(f"select config::text from config_profiles where uuid={sql_str(PROFILE_UUID)};").strip()
    if not raw:
        raise SystemExit(f"profile not found: {PROFILE_UUID}")
    cfg = replace_value(json.loads(raw))

    rows_raw = psql(
        "select uuid || E'\\t' || tag || E'\\t' || raw_inbound::text "
        f"from config_profile_inbounds where profile_uuid={sql_str(PROFILE_UUID)} order by port;"
    ).splitlines()
    inbound_updates = []
    for row in rows_raw:
        uuid, tag, raw_inbound = row.split("\t", 2)
        new_tag = RENAMES.get(tag, tag)
        new_raw = replace_value(json.loads(raw_inbound))
        inbound_updates.append((uuid, tag, new_tag, new_raw))

    print(f"profile: {OLD_PROFILE_NAME} -> {NEW_PROFILE_NAME}")
    for _, old, new, _ in inbound_updates:
        if old != new:
            print(f"inbound: {old} -> {new}")
    if dry_run:
        return

    backups = [
        f"config_profiles_bak_{stamp}_clean_names",
        f"config_profile_inbounds_bak_{stamp}_clean_names",
        f"internal_squad_inbounds_bak_{stamp}_clean_names",
        f"config_profile_inbounds_to_nodes_bak_{stamp}_clean_names",
    ]
    psql(f"create table {backups[0]} as table config_profiles;")
    psql(f"create table {backups[1]} as table config_profile_inbounds;")
    psql(f"create table {backups[2]} as table internal_squad_inbounds;")
    psql(f"create table {backups[3]} as table config_profile_inbounds_to_nodes;")

    psql(
        "update config_profiles set "
        f"name={sql_str(NEW_PROFILE_NAME)}, config={sql_json(cfg)}, updated_at=now() "
        f"where uuid={sql_str(PROFILE_UUID)};"
    )
    for uuid, _, new_tag, new_raw in inbound_updates:
        psql(
            "update config_profile_inbounds set "
            f"tag={sql_str(new_tag)}, raw_inbound={sql_json(new_raw)} "
            f"where uuid={sql_str(uuid)};"
        )
    print("backups:")
    for backup in backups:
        print(backup)


if __name__ == "__main__":
    main()
