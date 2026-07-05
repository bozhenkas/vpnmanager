#!/usr/bin/env python3
"""Dry-run first helper for primary/backup ingress IP migration.

Scope is intentionally narrow:
- primary: 45.91.54.152
- backup-ip: 45.91.53.93

Default mode only reports repository refs.  --apply backs up every touched file
before rewriting.  For backup-ip, --apply is blocked unless the new IP is
verified for SMART2/Lite ports 7443/tcp and 8443/udp, or explicitly confirmed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path


PRIMARY_IP = "45.91.54.152"
BACKUP_IP = "45.91.53.93"

SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
    "tmp",
    "legacy",
}

TEXT_EXTS = {
    "",
    ".conf",
    ".env",
    ".example",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".service",
    ".sql",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass
class Hit:
    path: Path
    line_no: int
    line: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("primary", "backup-ip"), required=True)
    parser.add_argument("--new-ip", required=True)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", default="")
    parser.add_argument("--verify-timeout", type=float, default=3.0)
    parser.add_argument("--verified-7443", action="store_true")
    parser.add_argument("--verified-8443", action="store_true")
    parser.add_argument(
        "--force-backup-ip",
        action="store_true",
        help="allow backup-ip apply without live probes; use only after external verification",
    )
    return parser.parse_args()


def old_ip_for_mode(mode: str) -> str:
    if mode == "primary":
        return PRIMARY_IP
    if mode == "backup-ip":
        return BACKUP_IP
    raise ValueError(mode)


def is_text_candidate(path: Path) -> bool:
    if path.name.startswith(".") and path.name not in {".gitignore"}:
        return False
    if path.suffix not in TEXT_EXTS:
        return False
    return True


def iter_files(root: Path):
    for base, dirs, files in os.walk(root):
        dirs[:] = [item for item in dirs if item not in SKIP_DIRS]
        base_path = Path(base)
        for name in files:
            path = base_path / name
            if is_text_candidate(path):
                yield path


def scan(root: Path, old_ip: str) -> list[Hit]:
    hits: list[Hit] = []
    for path in iter_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            if old_ip in line:
                hits.append(Hit(path=path, line_no=idx, line=line.strip()))
    return hits


def tcp_ok(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def udp_send_ok(host: str, port: int, timeout: float) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.send(b"\x00")
        return True
    except OSError:
        return False
    finally:
        sock.close()


def backup_apply_guard(args: argparse.Namespace) -> None:
    if args.mode != "backup-ip" or not args.apply:
        return
    if args.force_backup_ip:
        return
    ok_7443 = args.verified_7443 or tcp_ok(args.new_ip, 7443, args.verify_timeout)
    ok_8443 = args.verified_8443 or udp_send_ok(args.new_ip, 8443, args.verify_timeout)
    if not (ok_7443 and ok_8443):
        raise SystemExit(
            "refusing --mode backup-ip --apply: new IP must be verified for "
            "7443/tcp and 8443/udp, or pass --verified-7443 --verified-8443 "
            "after an external check"
        )


def backup_path(root: Path, backup_dir: str) -> Path:
    if backup_dir:
        return Path(backup_dir)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    return root / ".claude" / "backups" / f"ip-migration-{stamp}"


def apply_rewrite(root: Path, hits: list[Hit], old_ip: str, new_ip: str, backup_dir: Path) -> None:
    files = sorted({hit.path for hit in hits})
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in files:
        rel = path.relative_to(root)
        dst = backup_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(old_ip, new_ip), encoding="utf-8")


def print_live_notes(mode: str, old_ip: str, new_ip: str) -> None:
    print()
    print("live checklist:")
    print("- home: /etc/ip-watchdog/watchdog.env, /etc/rkn-checker/rkn-checker.env")
    print("- home: /var/lib/ip-watchdog/state and state.manual")
    print("- RU: /root/vpn-bot/vpn-bot.py env/overrides, nginx stream/server configs")
    print("- Cloudflare: A records for ru.goida.fun and related ingress aliases")
    print("- Remnawave: node host/listen values only via API/panel-safe flow, no direct SQL tag rename")
    if mode == "backup-ip":
        print("- backup guard: verify SMART2 7443/tcp and Smart Lite HY2 8443/udp before apply")
    print(f"- selected migration: {mode} {old_ip} -> {new_ip}")


def main() -> int:
    args = parse_args()
    root = Path(args.repo_root).resolve()
    old_ip = old_ip_for_mode(args.mode)
    hits = scan(root, old_ip)

    print(f"mode={args.mode} old_ip={old_ip} new_ip={args.new_ip} apply={args.apply}")
    print(f"repo={root}")
    print(f"matches={len(hits)}")
    for hit in hits:
        rel = hit.path.relative_to(root)
        print(f"{rel}:{hit.line_no}: {hit.line}")

    print_live_notes(args.mode, old_ip, args.new_ip)

    if not args.apply:
        print()
        print("dry-run only; rerun with --apply after reviewing matches and live checklist")
        return 0

    backup_apply_guard(args)
    bdir = backup_path(root, args.backup_dir)
    print()
    print(f"backup_dir={bdir}")
    apply_rewrite(root, hits, old_ip, args.new_ip, bdir)
    print(f"rewritten_files={len(set(hit.path for hit in hits))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
