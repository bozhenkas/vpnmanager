#!/usr/bin/env python3
"""delete legacy Remnawave hosts on :7443 except home-exit (Phase C1).

usage:
  python3 remna_host_cleanup.py --dry-run
  python3 remna_host_cleanup.py --apply
"""

from __future__ import annotations

import subprocess
import sys
import time

HOME_ADDRESS = "78.107.88.21"


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{proc.stderr or proc.stdout}")
    return proc.stdout


def psql(sql: str) -> str:
    return run([
        "docker", "exec", "-i", "remnawave-db",
        "psql", "-U", "postgres", "-d", "postgres", "-At", "-F", "\t", "-c", sql,
    ])


def sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def list_candidates() -> list[tuple[str, str, str, str]]:
    rows = psql(
        "select uuid::text, remark, address, port::text from hosts "
        f"where port=7443 and address <> {sql_str(HOME_ADDRESS)} "
        "and remark not ilike '%home%' order by remark;"
    ).splitlines()
    out = []
    for row in rows:
        if not row.strip():
            continue
        parts = row.split("\t")
        if len(parts) >= 4:
            out.append((parts[0], parts[1], parts[2], parts[3]))
    return out


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    apply = "--apply" in sys.argv
    if not dry_run and not apply:
        raise SystemExit("pass --dry-run or --apply")

    candidates = list_candidates()
    print(f"legacy :7443 hosts to delete (except home): {len(candidates)}")
    for uuid, remark, address, port in candidates:
        print(f"  {remark} {address}:{port} ({uuid})")

    if dry_run or not candidates:
        return

    stamp = time.strftime("%Y%m%d_%H%M%S") + "_hosts_cleanup"
    psql(f"create table hosts_bak_{stamp} as table hosts;")
    uuids = ",".join(sql_str(u) for u, *_ in candidates)
    deleted = psql(f"delete from hosts where uuid in ({uuids}) returning uuid;")
    print(f"deleted: {len(deleted.splitlines())}")
    print(f"backup: hosts_bak_{stamp}")


if __name__ == "__main__":
    main()
