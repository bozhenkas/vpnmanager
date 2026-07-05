#!/usr/bin/env python3
"""controlled tag migration for RU Remnawave ingress (Phase C2).

usage:
  python3 remna_tag_migration.py --dry-run
  python3 remna_tag_migration.py --apply
  python3 remna_tag_migration.py --rollback 20260606_120000_tag_migration
"""

from __future__ import annotations

import json
import subprocess
import sys
import time


PROFILE_UUID = "11111111-7443-4000-8000-000000000001"

RENAMES = {
    "RU_WS_SMART": "GOIDA_SMART",
    "RU_WS_FIN": "GOIDA_FIN",
    "RU_WS_FRA": "GOIDA_FRA",
    "RU_WS_SWE": "GOIDA_SWE",
    "RU_WS_DIRECT": "GOIDA_RU",
    "RU_REALITY_GRPC_RESERVE": "GOIDA_RESERVE",
    "RU_WS_HYDRA_DE": "GOIDA_HYDRA_DE",
    "RU_WS_HYDRA_NL": "GOIDA_HYDRA_NL",
    "RU_WS_HYDRA_POL": "GOIDA_HYDRA_POL",
    "RU_WS_HYDRA_TUR": "GOIDA_HYDRA_TUR",
    "BALANCER_FOREIGN_SMART": "GOIDA_BALANCER_SMART",
    "foreign-smart-catch-all": "goida-foreign-smart-catch-all",
    "direct-catch-all": "goida-direct-catch-all",
    # foreign profile — apply only when FOREIGN_PROFILE_UUID env set / verified live
    "REMNA_VLESS_TCP_REALITY_7443": "GOIDA_FOREIGN_REALITY",
}

KEEP_TAGS = frozenset({"RU_WS_HOME", "HOME_VLESS_TCP_REALITY_7443"})

BACKUP_TABLES = (
    "config_profiles",
    "config_profile_inbounds",
    "internal_squad_inbounds",
    "config_profile_inbounds_to_nodes",
)


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
        if value in KEEP_TAGS:
            return value
        return RENAMES.get(value, value)
    if isinstance(value, list):
        return [replace_value(item) for item in value]
    if isinstance(value, dict):
        return {key: replace_value(item) for key, item in value.items()}
    return value


def collect_inbound_updates() -> list[tuple[str, str, str, dict]]:
    rows_raw = psql(
        "select uuid || E'\\t' || tag || E'\\t' || raw_inbound::text "
        f"from config_profile_inbounds where profile_uuid={sql_str(PROFILE_UUID)} order by port;"
    ).splitlines()
    updates = []
    for row in rows_raw:
        if not row.strip():
            continue
        uuid, tag, raw_inbound = row.split("\t", 2)
        if tag in KEEP_TAGS:
            continue
        new_tag = RENAMES.get(tag, tag)
        new_raw = replace_value(json.loads(raw_inbound))
        if new_tag != tag or new_raw != json.loads(raw_inbound):
            updates.append((uuid, tag, new_tag, new_raw))
    return updates


def backup(stamp: str) -> list[str]:
    names = [f"{table}_bak_{stamp}" for table in BACKUP_TABLES]
    for table, backup in zip(BACKUP_TABLES, names):
        psql(f"create table {backup} as table {table};")
    return names


def apply_migration(stamp: str) -> None:
    raw = psql(f"select config::text from config_profiles where uuid={sql_str(PROFILE_UUID)};").strip()
    if not raw:
        raise SystemExit(f"profile not found: {PROFILE_UUID}")
    cfg = replace_value(json.loads(raw))
    inbound_updates = collect_inbound_updates()

    print(f"migration stamp: {stamp}")
    for _, old, new, _ in inbound_updates:
        if old != new:
            print(f"inbound: {old} -> {new}")

    backups = backup(stamp)
    psql(
        "update config_profiles set "
        f"config={sql_json(cfg)}, updated_at=now() "
        f"where uuid={sql_str(PROFILE_UUID)};"
    )
    for uuid, _, new_tag, new_raw in inbound_updates:
        psql(
            "update config_profile_inbounds set "
            f"tag={sql_str(new_tag)}, raw_inbound={sql_json(new_raw)} "
            f"where uuid={sql_str(uuid)};"
        )
    print("backups:")
    for name in backups:
        print(name)
    print("next: docker restart remnawave; shadow-tests")


def rollback(stamp: str) -> None:
    for table in BACKUP_TABLES:
        backup = f"{table}_bak_{stamp}"
        psql(f"delete from {table};")
        psql(f"insert into {table} select * from {backup};")
    print(f"rolled back from {stamp}")


def main() -> None:
    if "--rollback" in sys.argv:
        idx = sys.argv.index("--rollback")
        stamp = sys.argv[idx + 1]
        rollback(stamp)
        return

    dry_run = "--dry-run" in sys.argv
    stamp = time.strftime("%Y%m%d_%H%M%S") + "_tag_migration"

    raw = psql(f"select config::text from config_profiles where uuid={sql_str(PROFILE_UUID)};").strip()
    if not raw:
        raise SystemExit(f"profile not found: {PROFILE_UUID}")
    cfg = replace_value(json.loads(raw))
    inbound_updates = collect_inbound_updates()

    changed_rules = sum(
        1 for rule in cfg.get("routing", {}).get("rules", [])
        if any(
            replace_value(rule.get(k)) != rule.get(k)
            for k in ("ruleTag", "outboundTag", "balancerTag", "inboundTag")
            if rule.get(k) is not None
        )
    )
    print(f"profile uuid: {PROFILE_UUID}")
    print(f"inbound tag changes: {len([u for u in inbound_updates if u[1] != u[2]])}")
    print(f"routing touched (approx): {changed_rules}")
    for _, old, new, _ in inbound_updates:
        if old != new:
            print(f"  {old} -> {new}")

    if dry_run:
        return
    if "--apply" not in sys.argv:
        raise SystemExit("pass --dry-run or --apply")
    apply_migration(stamp)


if __name__ == "__main__":
    main()
