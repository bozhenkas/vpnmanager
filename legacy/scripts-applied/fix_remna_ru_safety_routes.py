#!/usr/bin/env python3
"""чинит RU safety IP/domain и P.15 reserve-policy в Remnawave profile.

P.15: reserve (RU_REALITY_GRPC_RESERVE) не в smart-балансере — catch-all -> REMNA_FI.
Cluster safety IP/domain rules по-прежнему покрывают reserve для DIRECT к goida infra.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time


PROFILE_UUID = os.environ.get("REMNA_RU_PROFILE_UUID", "11111111-7443-4000-8000-000000000001")
SMART_TAG = "RU_WS_SMART"
RESERVE_TAG = "RU_REALITY_GRPC_RESERVE"
FIN_OUT = "REMNA_FI"
RESERVE_FIN_RULE_TAG = "reserve-fin-catch-all"
SAFETY_RULE_TAG = "remna-smart-cluster-ru-ip-direct"
DOMAIN_RULE_TAG = "remna-smart-cluster-ru-domain-direct"
DEFAULT_SAFETY_IPS = [
    "45.91.54.152/32",
    "83.147.255.0/24",
    "194.117.80.94/32",
    "78.107.88.21/32",
]
DEFAULT_SAFETY_DOMAINS = [
    "domain:ru.goida.fun",
    "domain:web.goida.fun",
    "domain:reserve.goida.fun",
    "domain:ru-4.goida.fun",
    "domain:fin.goida.fun",
    "domain:swe.goida.fun",
    "domain:fra.goida.fun",
]
SMART_RULE_PREFIXES = (
    "manual-home",
    "manual-direct",
    "manual-foreign",
    "goida-block-youtube-quic",
    "remna-smart-",
)


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


def smart_rule(rule: dict) -> bool:
    inbound = rule.get("inboundTag") or []
    if SMART_TAG not in inbound:
        return False
    tag = str(rule.get("ruleTag") or "")
    if tag.startswith(SMART_RULE_PREFIXES):
        return True
    return bool(rule.get("balancerTag") == "BALANCER_FOREIGN_SMART" and rule.get("network") == "tcp,udp")


def remove_reserve_from_smart_rules(rules: list[dict]) -> list[str]:
    changed: list[str] = []
    for rule in rules:
        if not smart_rule(rule):
            continue
        inbound = list(rule.get("inboundTag") or [])
        if RESERVE_TAG not in inbound:
            continue
        inbound.remove(RESERVE_TAG)
        rule["inboundTag"] = inbound
        changed.append(f"remove {RESERVE_TAG} from {rule.get('ruleTag') or '<smart-catch-all>'}")
    return changed


def find_reserve_fin_insert_index(rules: list[dict]) -> int:
    for idx, rule in enumerate(rules):
        if rule.get("balancerTag") == "BALANCER_FOREIGN_SMART" and SMART_TAG in (rule.get("inboundTag") or []):
            return idx
    return len(rules)


def ensure_reserve_fin_catch_all(rules: list[dict]) -> list[str]:
    changed: list[str] = []
    rule = next((item for item in rules if item.get("ruleTag") == RESERVE_FIN_RULE_TAG), None)
    if rule is None:
        rules.insert(
            find_reserve_fin_insert_index(rules),
            {
                "type": "field",
                "ruleTag": RESERVE_FIN_RULE_TAG,
                "inboundTag": [RESERVE_TAG],
                "network": "tcp,udp",
                "outboundTag": FIN_OUT,
            },
        )
        return [f"insert {RESERVE_FIN_RULE_TAG}: {RESERVE_TAG} -> {FIN_OUT}"]

    if rule.get("inboundTag") != [RESERVE_TAG]:
        rule["inboundTag"] = [RESERVE_TAG]
        changed.append(f"set {RESERVE_FIN_RULE_TAG} inboundTag [{RESERVE_TAG}]")
    if rule.get("outboundTag") != FIN_OUT:
        rule["outboundTag"] = FIN_OUT
        changed.append(f"set {RESERVE_FIN_RULE_TAG} outboundTag {FIN_OUT}")
    if rule.get("network") != "tcp,udp":
        rule["network"] = "tcp,udp"
        changed.append(f"set {RESERVE_FIN_RULE_TAG} network tcp,udp")
    return changed


def find_insert_index(rules: list[dict]) -> int:
    for idx, rule in enumerate(rules):
        if rule.get("ruleTag") in {"remna-smart-ru-domain-direct", "remna-smart-ru-geoip-direct"}:
            return idx
    for idx, rule in enumerate(rules):
        if rule.get("balancerTag") == "BALANCER_FOREIGN_SMART" and SMART_TAG in (rule.get("inboundTag") or []):
            return idx
    return len(rules)


def ensure_safety_ips(rules: list[dict], safety_ips: list[str]) -> list[str]:
    changed: list[str] = []
    rule = next((item for item in rules if item.get("ruleTag") == SAFETY_RULE_TAG), None)
    if rule is None:
        rule = {
            "type": "field",
            "ruleTag": SAFETY_RULE_TAG,
            "inboundTag": [SMART_TAG, RESERVE_TAG],
            "ip": safety_ips,
            "outboundTag": "DIRECT",
        }
        rules.insert(find_insert_index(rules), rule)
        return [f"insert {SAFETY_RULE_TAG}: {', '.join(safety_ips)}"]

    inbound = list(rule.get("inboundTag") or [])
    for tag in (SMART_TAG, RESERVE_TAG):
        if tag not in inbound:
            inbound.append(tag)
            changed.append(f"add {tag} to {SAFETY_RULE_TAG}")
    ips = list(rule.get("ip") or [])
    for ip in safety_ips:
        if ip not in ips:
            ips.append(ip)
            changed.append(f"add {ip} to {SAFETY_RULE_TAG}")
    if rule.get("outboundTag") != "DIRECT":
        rule["outboundTag"] = "DIRECT"
        changed.append(f"set {SAFETY_RULE_TAG} outboundTag DIRECT")
    rule["inboundTag"] = inbound
    rule["ip"] = ips
    return changed


def ensure_safety_domains(rules: list[dict], safety_domains: list[str]) -> list[str]:
    changed: list[str] = []
    rule = next((item for item in rules if item.get("ruleTag") == DOMAIN_RULE_TAG), None)
    if rule is None:
        rule = {
            "type": "field",
            "ruleTag": DOMAIN_RULE_TAG,
            "inboundTag": [SMART_TAG, RESERVE_TAG],
            "domain": safety_domains,
            "outboundTag": "DIRECT",
        }
        rules.insert(find_insert_index(rules), rule)
        return [f"insert {DOMAIN_RULE_TAG}: {', '.join(safety_domains)}"]

    inbound = list(rule.get("inboundTag") or [])
    for tag in (SMART_TAG, RESERVE_TAG):
        if tag not in inbound:
            inbound.append(tag)
            changed.append(f"add {tag} to {DOMAIN_RULE_TAG}")
    current = list(rule.get("domain") or [])
    for domain in safety_domains:
        if domain not in current:
            current.append(domain)
            changed.append(f"add {domain} to {DOMAIN_RULE_TAG}")
    if rule.get("outboundTag") != "DIRECT":
        rule["outboundTag"] = "DIRECT"
        changed.append(f"set {DOMAIN_RULE_TAG} outboundTag DIRECT")
    rule["inboundTag"] = inbound
    rule["domain"] = current
    return changed


def fix_profile(cfg: dict, safety_ips: list[str], safety_domains: list[str]) -> list[str]:
    rules = cfg.setdefault("routing", {}).setdefault("rules", [])
    changed = remove_reserve_from_smart_rules(rules)
    changed.extend(ensure_reserve_fin_catch_all(rules))
    changed.extend(ensure_safety_domains(rules, safety_domains))
    changed.extend(ensure_safety_ips(rules, safety_ips))
    return changed


def save_profile(cfg: dict) -> str:
    backup = "config_profiles_bak_" + time.strftime("%Y%m%d_%H%M%S") + "_reserve_safety"
    psql(f"create table {backup} as table config_profiles;")
    psql(
        "update config_profiles set "
        f"config={sql_json(cfg)}, updated_at=now() "
        f"where uuid={sql_str(PROFILE_UUID)};"
    )
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P.15 reserve -> FIN-only + explicit RU cluster safety IP/domain rules",
    )
    parser.add_argument("--apply", action="store_true", help="write changes to Remnawave Postgres")
    parser.add_argument("--extra-ip", action="append", default=[], help="additional CIDR/IP for smart -> DIRECT safety")
    parser.add_argument("--extra-domain", action="append", default=[], help="additional domain:*/geosite:* for smart -> DIRECT safety")
    args = parser.parse_args()

    safety_ips = list(dict.fromkeys(DEFAULT_SAFETY_IPS + args.extra_ip))
    safety_domains = list(dict.fromkeys(DEFAULT_SAFETY_DOMAINS + args.extra_domain))
    cfg = load_profile()
    changed = fix_profile(cfg, safety_ips, safety_domains)
    if not changed:
        print("no changes")
        return 0
    for item in changed:
        print(item)
    if not args.apply:
        print("DRY RUN: no writes")
        return 0
    backup = save_profile(cfg)
    print(f"profile backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
