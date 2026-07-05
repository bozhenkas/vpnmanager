#!/usr/bin/env python3
"""Apply RU Remnawave routing hotfix: Telegram DIRECT, gaming DIRECT, balancer tune.

One-shot prod patch used during the 2026-06-05 incident on 45.91.54.152.
Run on the RU host with remnawave-db Docker access; backs up profile JSON
under /root/deploy-backups/ before writing config_profiles.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

PROFILE_UUID = "11111111-7443-4000-8000-000000000001"


def psql(sql: str) -> str:
    return subprocess.check_output(
        [
            "docker",
            "exec",
            "remnawave-db",
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-At",
            "-c",
            sql,
        ],
        text=True,
    )


def main() -> None:
    ts = time.strftime("%Y%m%d%H%M%S")
    backup_dir = f"/root/deploy-backups/{ts}-routing-telegram-gaming"
    os.makedirs(backup_dir, exist_ok=True)

    raw = psql(f"select config::text from config_profiles where uuid='{PROFILE_UUID}';")
    with open(f"{backup_dir}/config_profiles.json", "w", encoding="utf-8") as fh:
        fh.write(raw)
    print(f"backup={backup_dir}")

    cfg = json.loads(raw)
    rules = cfg.setdefault("routing", {}).setdefault("rules", [])
    balancers = cfg.setdefault("routing", {}).setdefault("balancers", [])
    changes: list[str] = []

    for rule in rules:
        tag = rule.get("ruleTag", "")
        if tag in ("proxy-telegram-domain", "proxy-telegram-ip"):
            if rule.get("outboundTag") != "DIRECT":
                rule["outboundTag"] = "DIRECT"
                changes.append(f"{tag}: REMNA_FRA -> DIRECT")

    gaming_rule_tag = "direct-gaming-supercell"
    gaming_domains = [
        "geosite:supercell",
        "domain:supercell.com",
        "domain:supercell.net",
        "domain:supercellgames.com",
        "domain:clashroyale.com",
        "domain:clashofclans.com",
        "domain:brawlstars.com",
        "domain:game.clashroyale.com",
        "domain:prod.supercell.com",
    ]
    existing = next((r for r in rules if r.get("ruleTag") == gaming_rule_tag), None)
    if existing is None:
        insert_at = 0
        for idx, rule in enumerate(rules):
            if rule.get("ruleTag") in ("proxy-telegram-domain", "proxy-telegram-ip"):
                insert_at = idx
                break
        rules.insert(
            insert_at,
            {
                "type": "field",
                "ruleTag": gaming_rule_tag,
                "inboundTag": ["RU_WS_SMART", "RU_WS_DIRECT", "RU_REALITY_GRPC_RESERVE"],
                "domain": gaming_domains,
                "outboundTag": "DIRECT",
            },
        )
        changes.append("insert direct-gaming-supercell -> DIRECT")

    for bal in balancers:
        if bal.get("tag") == "BALANCER_FOREIGN_SMART":
            old_sel = bal.get("selector", [])
            bal["selector"] = ["REMNA_FRA", "REMNA_SWE", "REMNA_FI"]
            bal["fallbackTag"] = "REMNA_FI"
            bal["strategy"] = {"type": "random"}
            changes.append(f"balancer {old_sel} -> {bal['selector']}, strategy random")

    payload = json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))
    escaped = payload.replace("'", "''")
    psql(
        f"UPDATE config_profiles SET config='{escaped}'::jsonb, updated_at=now() "
        f"WHERE uuid='{PROFILE_UUID}';"
    )
    print("changes:")
    for item in changes:
        print(f" - {item}")


if __name__ == "__main__":
    main()
