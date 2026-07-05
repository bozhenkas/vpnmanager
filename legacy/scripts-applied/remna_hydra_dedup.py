#!/usr/bin/env python3
"""remove duplicate RU_WS_HYDRA inbounds after GOIDA tag migration."""

from __future__ import annotations

import json
import subprocess
import sys
import time

PROFILE_UUID = "11111111-7443-4000-8000-000000000001"


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
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


def main() -> None:
    dry = "--apply" not in sys.argv
    stamp = time.strftime("%Y%m%d_%H%M%S")
    raw = psql(f"select config::text from config_profiles where uuid={sql_str(PROFILE_UUID)};").strip()
    cfg = json.loads(raw)
    before = [i.get("tag") for i in cfg.get("inbounds", []) if "HYDRA" in str(i.get("tag", ""))]
    cfg["inbounds"] = [
        i for i in cfg.get("inbounds", [])
        if not str(i.get("tag", "")).startswith("RU_WS_HYDRA_")
    ]
    rules = cfg.get("routing", {}).get("rules", [])
    for rule in rules:
        tags = rule.get("inboundTag") or []
        if isinstance(tags, list):
            rule["inboundTag"] = [t for t in tags if not str(t).startswith("RU_WS_HYDRA_")]
    after = [i.get("tag") for i in cfg.get("inbounds", []) if "HYDRA" in str(i.get("tag", ""))]
    dup_rows = psql(
        "select count(*) from config_profile_inbounds "
        f"where profile_uuid={sql_str(PROFILE_UUID)} and tag like 'RU_WS_HYDRA_%';"
    ).strip()
    print(f"hydra inbounds before: {before}")
    print(f"hydra inbounds after:  {after}")
    print(f"RU_WS_HYDRA rows to delete: {dup_rows}")
    if dry:
        return
    psql(f"create table config_profiles_bak_{stamp}_hydra_dedup as table config_profiles;")
    psql(f"create table config_profile_inbounds_bak_{stamp}_hydra_dedup as table config_profile_inbounds;")
    psql(
        "update config_profiles set "
        f"config={sql_json(cfg)}, updated_at=now() "
        f"where uuid={sql_str(PROFILE_UUID)};"
    )
    psql(
        "delete from config_profile_inbounds_to_nodes itn using config_profile_inbounds i "
        f"where i.uuid=itn.config_profile_inbound_uuid and i.profile_uuid={sql_str(PROFILE_UUID)} "
        "and i.tag like 'RU_WS_HYDRA_%';"
    )
    psql(
        "delete from internal_squad_inbounds si using config_profile_inbounds i "
        f"where i.uuid=si.inbound_uuid and i.profile_uuid={sql_str(PROFILE_UUID)} "
        "and i.tag like 'RU_WS_HYDRA_%';"
    )
    psql(
        f"delete from config_profile_inbounds where profile_uuid={sql_str(PROFILE_UUID)} "
        "and tag like 'RU_WS_HYDRA_%';"
    )
    print(f"backup stamp: {stamp}")
    print("next: docker restart remnanode")


if __name__ == "__main__":
    main()
