#!/usr/bin/env python3
"""P.15 GOIDA reserve -> FIN surgical apply (prod-safe).

Live profile uses GOIDA_* tags; full apply_remna_routing_spec rewrites to RU_WS_* and breaks prod.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time

PROFILE_UUID = os.environ.get("REMNA_RU_PROFILE_UUID", "11111111-7443-4000-8000-000000000001")
SMART = "GOIDA_SMART"
RESERVE = "GOIDA_RESERVE"
BAL = "GOIDA_BALANCER_SMART"
FIN = "REMNA_FI"
RESERVE_FIN = "reserve-fin-catch-all"
KEEP = {
    "direct-goida-cluster-domain",
    "direct-goida-cluster-ip",
    "direct-gaming-supercell",
    RESERVE_FIN,
}


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


def load_profile() -> dict:
    raw = psql(f"select config::text from config_profiles where uuid={sql_str(PROFILE_UUID)};").strip()
    return json.loads(raw)


def save_profile(cfg: dict, reason: str) -> str:
    backup = "config_profiles_bak_" + time.strftime("%Y%m%d_%H%M%S") + "_" + reason
    psql(f"create table {backup} as table config_profiles;")
    psql(
        "update config_profiles set "
        f"config={sql_json(cfg)}, updated_at=now() "
        f"where uuid={sql_str(PROFILE_UUID)};"
    )
    return backup


def summarize_reserve(rules: list[dict]) -> list[str]:
    lines: list[str] = []
    for idx, rule in enumerate(rules):
        tag = rule.get("ruleTag") or ""
        inbound = rule.get("inboundTag") or []
        if RESERVE in inbound or tag in (RESERVE_FIN, "goida-foreign-smart-catch-all"):
            target = rule.get("outboundTag") or rule.get("balancerTag") or ""
            lines.append(f"{idx:02d} | {tag} | in={','.join(inbound)} | to={target}")
    return lines


def apply_p15(cfg: dict) -> list[str]:
    rules = cfg.setdefault("routing", {}).setdefault("rules", [])
    changed: list[str] = []
    for rule in rules:
        tag = str(rule.get("ruleTag") or "")
        if tag in KEEP:
            continue
        inbound = list(rule.get("inboundTag") or [])
        if RESERVE not in inbound:
            continue
        if rule.get("balancerTag") == BAL or SMART in inbound:
            inbound.remove(RESERVE)
            rule["inboundTag"] = inbound
            changed.append(f"remove {RESERVE} from {tag or '<rule>'}")

    rf = next((r for r in rules if r.get("ruleTag") == RESERVE_FIN), None)
    insert_at = next(
        (i for i, r in enumerate(rules) if r.get("ruleTag") == "goida-foreign-smart-catch-all"),
        len(rules),
    )
    if rf is None:
        rules.insert(
            insert_at,
            {
                "type": "field",
                "ruleTag": RESERVE_FIN,
                "inboundTag": [RESERVE],
                "network": "tcp,udp",
                "outboundTag": FIN,
            },
        )
        changed.append(f"insert {RESERVE_FIN}: {RESERVE} -> {FIN}")
    else:
        if rf.get("inboundTag") != [RESERVE]:
            rf["inboundTag"] = [RESERVE]
            changed.append(f"set {RESERVE_FIN} inboundTag [{RESERVE}]")
        if rf.get("outboundTag") != FIN:
            rf["outboundTag"] = FIN
            changed.append(f"set {RESERVE_FIN} outboundTag {FIN}")

    for rule in rules:
        if rule.get("ruleTag") == "goida-foreign-smart-catch-all" and rule.get("inboundTag") != [SMART]:
            rule["inboundTag"] = [SMART]
            changed.append("goida-foreign-smart-catch-all: GOIDA_SMART only")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reason", default="reserve_fin_only")
    args = parser.parse_args()

    cfg = load_profile()
    rules = cfg.get("routing", {}).get("rules", [])
    print("BEFORE:")
    print("\n".join(summarize_reserve(rules)) or "(none)")
    changed = apply_p15(cfg)
    print("CHANGES:")
    print("\n".join(changed) if changed else "no changes")
    print("AFTER:")
    print("\n".join(summarize_reserve(cfg["routing"]["rules"])) or "(none)")
    if not args.apply:
        print("DRY RUN: no writes")
        return 0
    if not changed:
        print("nothing to apply")
        return 0
    backup = save_profile(cfg, args.reason)
    print(f"profile backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
