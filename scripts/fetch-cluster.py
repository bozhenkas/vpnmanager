#!/usr/bin/env python3
"""
fetch-cluster.py — read-only бутстрап live-инвентаря для ИИ-агента в начале сессии.

Расшифровывает локальный cluster.age agent-ключом (или читает plaintext
cluster.json в dev) и печатает таблицу серверов/статусов. Так первая вещь,
которую видит агент, — АКТУАЛЬНЫЙ инвентарь, а не legacy-IP из памяти.

`--check` дополнительно грепает .claude/memory/*.md на IPv4, которых нет в live-
конфиге, и предупреждает о возможном stale. Скрипт НИЧЕГО не пишет (ни в память,
ни в прод) — только читает и печатает.

Источники (по приоритету):
  --plain PATH | $CLUSTER_PLAIN_PATH         — plaintext cluster.json
  --age PATH --key PATH | $CLUSTER_AGE_PATH/$CLUSTER_KEY_PATH — зашифрованный
  по умолчанию (dev): .claude/cluster/cluster.json, если существует
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from subscription.cluster_config import (  # noqa: E402
    ClusterConfig,
    ClusterConfigError,
    load_cluster,
)

IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
DEFAULT_DEV_PLAIN = ROOT / ".claude" / "cluster" / "cluster.json"
MEMORY_DIR = ROOT / ".claude" / "memory"


def _is_routable_public(ip: str) -> bool:
    """отсеять loopback/private/reserved — это никогда не публичные IP нод."""
    octets = ip.split(".")
    if len(octets) != 4:
        return False
    try:
        a, b, c, d = (int(o) for o in octets)
    except ValueError:
        return False
    if any(o > 255 for o in (a, b, c, d)):
        return False
    if a in (0, 10, 127):
        return False
    if a == 172 and 16 <= b <= 31:
        return False
    if a == 192 and b == 168:
        return False
    if a == 169 and b == 254:
        return False
    if a >= 224:  # multicast/reserved
        return False
    return True


def _resolve_sources(args) -> dict:
    plain = args.plain or os.environ.get("CLUSTER_PLAIN_PATH")
    age = args.age or os.environ.get("CLUSTER_AGE_PATH")
    key = args.key or os.environ.get("CLUSTER_KEY_PATH")
    if not plain and not age and DEFAULT_DEV_PLAIN.exists():
        plain = str(DEFAULT_DEV_PLAIN)
    return {"plain_path": plain, "age_path": age, "key_path": key}


def _print_table(cfg: ClusterConfig) -> None:
    print(f"cluster inventory v{cfg.version} (primary domain: {cfg.primary_domain() or '—'})")
    print(f"  primary_cf_ip: {cfg.primary_cf_ip() or '—'}   backup_cf_ip: {cfg.backup_cf_ip() or '—'}")
    print(f"  {'id':<8} {'role':<11} {'status':<8} {'ip':<16} {'domain':<20} transports")
    print("  " + "-" * 86)
    for sid, s in cfg.servers.items():
        transports = ",".join(s.get("transports", []) or [])
        print(f"  {sid:<8} {str(s.get('role','')):<11} {str(s.get('status','')):<8} "
              f"{str(s.get('ip','')):<16} {str(s.get('domain','—')):<20} {transports}")
    if cfg.legacy_blocklist:
        print(f"  legacy_blocklist: {', '.join(sorted(cfg.legacy_blocklist))}")


def _check_memory(cfg: ClusterConfig) -> int:
    """warn про IPv4 в .claude/memory, которых нет в live-конфиге. Возвращает кол-во."""
    live_ips: set[str] = set()
    for s in cfg.servers.values():
        for key in ("ip", "ssh_host"):
            v = str(s.get(key, "")).strip()
            if v:
                live_ips.add(v)
    live_ips |= set(cfg.legacy_blocklist)  # известный legacy не считаем «новым» дрейфом

    if not MEMORY_DIR.exists():
        print("  (no .claude/memory — skip drift check)")
        return 0

    stale: dict[str, set[str]] = {}
    for md in sorted(MEMORY_DIR.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for ip in set(IPV4_RE.findall(text)):
            if not _is_routable_public(ip):
                continue  # loopback/private/невалидный — не наш публичный IP
            if ip not in live_ips:
                stale.setdefault(ip, set()).add(md.name)

    if not stale:
        print("  drift check: ✓ no unknown IPs in .claude/memory")
        return 0
    print("  drift check: ⚠ IPs in memory NOT present in live cluster config:")
    for ip, files in sorted(stale.items()):
        print(f"    {ip:<16} ← {', '.join(sorted(files))}")
    print("  → live-конфиг авторитетен по IP. Перепроверь источник перед использованием этих адресов.")
    return len(stale)


def main() -> int:
    ap = argparse.ArgumentParser(description="read-only live cluster inventory")
    ap.add_argument("--plain", help="plaintext cluster.json path")
    ap.add_argument("--age", help="encrypted cluster.age path")
    ap.add_argument("--key", help="age private key path")
    ap.add_argument("--check", action="store_true", help="warn about stale IPs in .claude/memory")
    ap.add_argument("--hmac-secret", default=os.environ.get("CLUSTER_HMAC_SECRET") or None)
    args = ap.parse_args()

    sources = _resolve_sources(args)
    if not sources["plain_path"] and not (sources["age_path"] and sources["key_path"]):
        print("cluster: no source available (set --plain/--age+--key or deploy cluster.age).",
              file=sys.stderr)
        return 2

    cfg = load_cluster(hmac_secret=args.hmac_secret, **sources)
    if cfg is None:
        print("cluster: failed to load a usable inventory.", file=sys.stderr)
        return 1

    _print_table(cfg)
    if args.check:
        _check_memory(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
