#!/usr/bin/env python3
"""применяет декларативный RU routing spec к Remnawave profile.

По умолчанию dry-run: читает live profile, manual_domain_rules из bot.db,
собирает routing.rules, сохраняет HYDRA rules и печатает diff-summary.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from remna_routing_spec import RoutingSpec, build_rules, is_hydra_rule, xray_domain  # noqa: E402


PROFILE_UUID = os.environ.get("REMNA_RU_PROFILE_UUID", "11111111-7443-4000-8000-000000000001")
BOT_DB = os.environ.get("BOT_DB", "/root/vpn-bot/bot.db")


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
    if not raw:
        raise RuntimeError(f"profile not found: {PROFILE_UUID}")
    return json.loads(raw)


def save_profile(cfg: dict, reason: str) -> str:
    safe_reason = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in reason)
    backup = "config_profiles_bak_" + time.strftime("%Y%m%d_%H%M%S") + "_" + safe_reason
    psql(f"create table {backup} as table config_profiles;")
    psql(
        "update config_profiles set "
        f"config={sql_json(cfg)}, updated_at=now() "
        f"where uuid={sql_str(PROFILE_UUID)};"
    )
    return backup


def grouped_manual_rules(db_path: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {"home": [], "direct": [], "foreign": []}
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"bot db not found: {db_path}")
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        rows = conn.execute("SELECT domain, rule FROM manual_domain_rules ORDER BY domain").fetchall()
    finally:
        conn.close()
    for domain, rule in rows:
        if rule in grouped:
            grouped[rule].append(xray_domain(str(domain)))
    return grouped


def rule_key(rule: dict) -> str:
    return str(rule.get("ruleTag") or ",".join(rule.get("inboundTag") or []) or rule.get("outboundTag") or rule.get("balancerTag") or "<rule>")


def summarize_rules(rules: list[dict]) -> list[str]:
    lines: list[str] = []
    for idx, rule in enumerate(rules):
        target = rule.get("outboundTag") or rule.get("balancerTag") or ""
        inbound = ",".join(rule.get("inboundTag") or [])
        parts = [
            f"{idx:02d}",
            rule_key(rule),
            f"in={inbound or '-'}",
            f"to={target}",
        ]
        if rule.get("domain"):
            parts.append(f"domain={len(rule['domain'])}")
        if rule.get("ip"):
            parts.append(f"ip={len(rule['ip'])}")
        if rule.get("network"):
            parts.append(f"net={rule['network']}")
        if rule.get("port"):
            parts.append(f"port={rule['port']}")
        lines.append(" | ".join(parts))
    return lines


def build_updated_profile(profile: dict, grouped: dict[str, list[str]], spec: RoutingSpec) -> dict:
    updated = json.loads(json.dumps(profile, ensure_ascii=False))
    routing = updated.setdefault("routing", {})
    existing_rules = routing.setdefault("rules", [])
    hydra_rules = [rule for rule in existing_rules if is_hydra_rule(rule)]
    routing["domainStrategy"] = "IPIfNonMatch"
    routing["balancers"] = build_balancers(routing.get("balancers", []), spec)
    routing["rules"] = build_rules(grouped, spec) + hydra_rules
    return updated


def build_balancers(existing: list[dict], spec: RoutingSpec) -> list[dict]:
    """обновляет smart-balancer и сохраняет hydra/прочие balancers."""
    smart = {
        "tag": spec.smart_balancer,
        "selector": spec.smart_selector,
        "strategy": {"type": "leastLoad"},
        "fallbackTag": spec.smart_fallback,
    }
    others = [balancer for balancer in existing if balancer.get("tag") != spec.smart_balancer]
    return [smart] + others


def main() -> int:
    parser = argparse.ArgumentParser(description="apply RU Remnawave routing spec")
    parser.add_argument("--apply", action="store_true", help="write profile to Remnawave Postgres")
    parser.add_argument("--bot-db", default=BOT_DB, help="bot.db path with manual_domain_rules")
    parser.add_argument("--direct-zapret-outbound", default="DIRECT", help="outboundTag for direct-zapret semantic routes")
    parser.add_argument("--reason", default="routing_spec", help="backup suffix")
    parser.add_argument("--show-diff", action="store_true", help="print unified diff of rule summary")
    parser.add_argument(
        "--legacy-smart-reserve",
        action="store_true",
        help="legacy pre-P.15: reserve (RU_REALITY_GRPC_RESERVE) в smart-балансере",
    )
    args = parser.parse_args()

    profile = load_profile()
    grouped = grouped_manual_rules(args.bot_db)
    # default P.15: reserve -> REMNA_FI catch-all, не в BALANCER_FOREIGN_SMART
    spec = RoutingSpec(
        direct_zapret_outbound=args.direct_zapret_outbound,
        reserve_fin_only=not args.legacy_smart_reserve,
    )
    updated = build_updated_profile(profile, grouped, spec)

    old_rules = profile.get("routing", {}).get("rules", [])
    new_rules = updated.get("routing", {}).get("rules", [])
    old_summary = summarize_rules(old_rules)
    new_summary = summarize_rules(new_rules)

    print(f"manual rules: home={len(grouped['home'])} direct={len(grouped['direct'])} foreign={len(grouped['foreign'])}")
    print(f"hydra rules preserved: {sum(1 for rule in old_rules if is_hydra_rule(rule))}")
    print(f"rules: {len(old_rules)} -> {len(new_rules)}")

    if old_summary == new_summary:
        print("no routing summary changes")
    else:
        print("routing summary changed")
        if args.show_diff:
            print("\n".join(difflib.unified_diff(old_summary, new_summary, fromfile="live", tofile="spec", lineterm="")))

    if not args.apply:
        print("DRY RUN: no writes")
        return 0

    backup = save_profile(updated, args.reason)
    print(f"profile backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
