#!/usr/bin/env python3
"""
rkn-dpi-auto-updater.py — тестовый планировщик auto zapret/direct-zapret правил.

ничего не применяет в prod: запускает rkn-check по пулу целей и пишет json-план.
будущая идея: dpi-only блокировки можно добавлять в zapret host/ip list и вести через
direct-zapret, но ручные правила из бота direct/home/foreign всегда выше авто.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_TARGETS = Path(__file__).with_name("rkn-dpi-targets.example.txt")
DEFAULT_REPORT = Path("/tmp/goida-rkn-dpi-plan.json")
DEFAULT_DB = Path("/etc/x-ui/x-ui.db")
MANUAL_OUTBOUNDS = {
    "direct": "manual-direct",
    "direct-zapret": "manual-direct-zapret",
    "home-mac-exit": "manual-home",
    "balancer-smart": "manual-foreign",
    "proxy-fi": "manual-foreign",
    "proxy-se": "manual-foreign",
}


@dataclass
class Target:
    name: str
    url: str
    host: str
    ip: str | None = None


@dataclass
class ProbeResult:
    target: dict
    status: str
    verdict: str
    manual_override: str | None
    action: str
    reason: str
    zapret_candidates: list[str]
    routing_candidate: dict | None
    raw_excerpt: list[str]


def normalize_host(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("empty target")
    if "://" in value:
        parsed = urlparse(value)
        host = parsed.hostname or ""
    else:
        host = value.split("/", 1)[0].split(":", 1)[0]
    host = host.strip(".").lower()
    if not host:
        raise ValueError(f"cannot parse host from {value!r}")
    return host


def make_url(value: str) -> str:
    value = value.strip()
    if "://" in value:
        return value
    try:
        ipaddress.ip_address(value)
        return f"https://{value}/"
    except ValueError:
        return f"https://{value}/"


def read_targets(path: Path) -> list[Target]:
    targets: list[Target] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 1:
            name = normalize_host(parts[0])
            url = make_url(parts[0])
        else:
            name = parts[0]
            url = make_url(parts[1])
        host = normalize_host(url)
        key = f"{name}:{url}"
        if key in seen:
            continue
        seen.add(key)
        ip = None
        try:
            ipaddress.ip_address(host)
            ip = host
        except ValueError:
            pass
        targets.append(Target(name=name, url=url, host=host, ip=ip))
    if not targets:
        raise SystemExit(f"no targets in {path}")
    return targets


def run_rkn_check(rkn_check: str, target: Target, timeout: int) -> tuple[int, str]:
    cmd = [rkn_check, "--url", target.url]
    proc = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout


def classify_rkn_output(text: str, rc: int) -> tuple[str, str]:
    """возвращает status + короткое объяснение."""
    low = text.lower()
    verdict_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("=") and not line.startswith("-")
    ]
    joined = "\n".join(verdict_lines)

    has_dpi = "dpi" in low or "тспу" in low
    has_dns = re.search(r"\bdns\b|dns block|dns-блок|dns блок", low) is not None
    has_ip = re.search(r"\bip\b.*block|block.*\bip\b|реестр|registry", low) is not None
    has_tls_cert = "certificate_verify_failed" in low or "cert" in low and "verify" in low
    has_tcp_fail = any(s in low for s in ("tcp timeout", "connection timed out", "connection reset"))
    has_ok = re.search(r"\bok\b|available|доступ", low) is not None

    if has_dns or has_ip:
        return "dns_or_ip_block", "rkn-check reports DNS/IP/registry style blocking"
    if has_dpi and not has_dns and not has_ip:
        return "dpi_only", "rkn-check reports DPI/TSPU without DNS/IP block markers"
    if has_tls_cert and has_dpi:
        return "dpi_suspect_tls", "TLS failed and rkn-check suspects DPI; needs manual review"
    if rc != 0 or has_tcp_fail:
        return "unknown_fail", "probe failed without clean DPI-only verdict"
    if has_ok:
        return "ok", "no block marker found"
    return "unknown", f"cannot classify output: {joined[:160]}"


def load_manual_overrides(db_path: Path) -> dict[str, str]:
    """читает live/template xray rules и собирает домены, заданные вручную."""
    if not db_path.exists():
        return {}
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "select value from settings where key='xrayTemplateConfig'"
        ).fetchone()
    finally:
        con.close()
    if not row:
        return {}
    cfg = json.loads(row[0])
    overrides: dict[str, str] = {}
    for rule in cfg.get("routing", {}).get("rules", []):
        tag = rule.get("outboundTag") or rule.get("balancerTag")
        label = MANUAL_OUTBOUNDS.get(tag)
        if not label:
            continue
        rule_tag = rule.get("ruleTag", "")
        # auto/system rules should not suppress future auto decisions.
        if rule_tag.startswith("goida-auto-") or rule_tag.startswith("goida-block-youtube-quic"):
            continue
        domains = rule.get("domain") or []
        for item in domains:
            if not item.startswith("domain:"):
                continue
            host = item.removeprefix("domain:").lower()
            overrides.setdefault(host, label)
    return overrides


def match_manual_override(host: str, overrides: dict[str, str]) -> str | None:
    labels = []
    parts = host.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in overrides:
            labels.append(overrides[candidate])
    return labels[0] if labels else None


def build_result(target: Target, rc: int, raw: str, overrides: dict[str, str]) -> ProbeResult:
    status, reason = classify_rkn_output(raw, rc)
    manual = match_manual_override(target.host, overrides)
    zapret_candidates: list[str] = []
    routing_candidate: dict | None = None
    action = "observe"

    if manual:
        action = "skip-manual-override"
        reason = f"{reason}; manual rule has priority: {manual}"
    elif status == "dpi_only":
        action = "candidate"
        zapret_candidates = [target.ip or target.host]
        routing_candidate = {
            "type": "field",
            "ruleTag": "goida-auto-dpi-direct-zapret",
            "inboundTag": ["inbound-10003"],
            "domain": [f"domain:{target.host}"] if not target.ip else None,
            "ip": [target.ip] if target.ip else None,
            "outboundTag": "direct-zapret",
        }
        routing_candidate = {k: v for k, v in routing_candidate.items() if v}
        reason = f"{reason}; safe candidate for zapret/direct-zapret test plan"
    elif status in ("dns_or_ip_block", "unknown_fail"):
        action = "skip-not-dpi-only"

    excerpt = [line.rstrip() for line in raw.splitlines() if line.strip()][:30]
    return ProbeResult(
        target=asdict(target),
        status=status,
        verdict=reason,
        manual_override=manual,
        action=action,
        reason=reason,
        zapret_candidates=zapret_candidates,
        routing_candidate=routing_candidate,
        raw_excerpt=excerpt,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="test-only RKN DPI auto updater planner")
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--rkn-check", default="rkn-check")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--no-probe", action="store_true", help="only parse targets/manual overrides")
    args = parser.parse_args()

    targets = read_targets(args.targets)
    overrides = load_manual_overrides(args.db)
    results: list[ProbeResult] = []

    for target in targets:
        if args.no_probe:
            rc, raw = 0, "probe skipped by --no-probe"
        else:
            try:
                rc, raw = run_rkn_check(args.rkn_check, target, args.timeout)
            except FileNotFoundError:
                raise SystemExit(f"rkn-check not found: {args.rkn_check}")
            except subprocess.TimeoutExpired as exc:
                rc, raw = 124, (exc.stdout or "") + "\nprobe timeout"
        results.append(build_result(target, rc, raw, overrides))

    plan = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry-run-plan-only",
        "notes": [
            "prod is not modified by this tool",
            "manual bot rules direct/home/foreign have priority over auto candidates",
            "only dpi_only results become zapret/direct-zapret candidates",
        ],
        "targets": len(targets),
        "candidates": [asdict(r) for r in results if r.action == "candidate"],
        "results": [asdict(r) for r in results],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"targets: {len(targets)}")
    print(f"candidates: {len(plan['candidates'])}")
    print(f"report: {args.report}")
    for result in results:
        print(f"{result.target['name']}: {result.status} -> {result.action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
