#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import time
from collections import defaultdict
from pathlib import Path

TARGET_USERS = ("bozhenkas", "test-sub", "remnatest")
REMNA_CONTAINER = os.environ.get("REMNAWAVE_DB_CONTAINER", "remnawave-db")


SYNTHETIC_RE = re.compile(
    r"(CHECK|TEST|test-hwid|VK-ROUTE|BOOSTY|shadow-smoke|shadow|smoke|"
    r"remnatest|test-sub|incident|strict-verify|header-verify|diag)",
    re.I,
)

KNOWN_SYNTHETIC_HWIDS = {
    "2604271945594",
    "2605221402566",
    "2605221402666",
    "342f7299a7b65741",
    "f7bdgmo86aik45lc",
}


def device_deletes(conn: sqlite3.Connection) -> tuple[list[tuple[str, str]], dict[tuple[str, str], str]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT u.name, d.*
        FROM user_devices d
        LEFT JOIN users u ON u.token = d.token
        ORDER BY u.name, d.last_seen DESC
    """).fetchall()
    deletes: list[tuple[str, str]] = []
    reasons: dict[tuple[str, str], str] = {}
    by_token: dict[str, list[sqlite3.Row]] = defaultdict(list)

    for row in rows:
        key = (row["token"], row["device_id"])
        if not row["name"]:
            deletes.append(key)
            reasons[key] = "orphan token"
            continue
        if not (row["device_id"] or "").startswith("hwid:"):
            deletes.append(key)
            reasons[key] = "legacy non-hwid"
            continue
        if SYNTHETIC_RE.search(row["device_id"] or "") or SYNTHETIC_RE.search(row["user_agent"] or ""):
            deletes.append(key)
            reasons[key] = "synthetic/test"
            continue
        by_token[row["token"]].append(row)

    for token, rows_for_token in by_token.items():
        groups: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
        for row in rows_for_token:
            groups[(row["platform"] or "", row["app_version"] or "", row["app_name"] or "")].append(row)

        for group_rows in groups.values():
            numeric: list[tuple[int, sqlite3.Row]] = []
            for row in group_rows:
                match = re.fullmatch(r"hwid:(\d+)", row["device_id"] or "")
                if match:
                    numeric.append((int(match.group(1)), row))
            numeric.sort(key=lambda item: item[0])
            clusters: list[list[tuple[int, sqlite3.Row]]] = []
            for num, row in numeric:
                if not clusters or num - clusters[-1][-1][0] > 100:
                    clusters.append([(num, row)])
                else:
                    clusters[-1].append((num, row))
            for cluster in clusters:
                if len(cluster) <= 1:
                    continue
                keep = max(cluster, key=lambda item: item[1]["last_seen"])[1]
                for _, row in cluster:
                    if row["device_id"] == keep["device_id"]:
                        continue
                    key = (token, row["device_id"])
                    if key not in reasons:
                        deletes.append(key)
                        reasons[key] = f"duplicate hwid cluster keep {keep['device_id']}"

    return deletes, reasons


def reparse_happ_metadata(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT token, device_id, user_agent FROM user_devices").fetchall()
    for token, device_id, user_agent in rows:
        ua = (user_agent or "").strip()
        if not ua.startswith("Happ/"):
            continue
        parts = ua.split("/")
        app_version = parts[1].strip() if len(parts) > 1 else ""
        platform = parts[2].strip() if len(parts) > 2 else ""
        device_name = parts[3].strip() if len(parts) > 3 else device_id.replace("hwid:", "")
        conn.execute(
            """
            UPDATE user_devices
            SET app_name='Happ', app_version=?, platform=?, device_name=?
            WHERE token=? AND device_id=?
            """,
            (app_version[:60], platform[:60], device_name[:120], token, device_id),
        )


def sync_user_ips(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM user_ips")
    rows = conn.execute("""
        SELECT token, device_id, first_seen, last_seen, user_agent
        FROM user_devices
        WHERE device_id LIKE 'hwid:%'
    """).fetchall()
    conn.executemany(
        "INSERT OR REPLACE INTO user_ips(token, ip, first_seen, last_seen, user_agent) VALUES (?, ?, ?, ?, ?)",
        rows,
    )


def remnawave_hwid_candidates(usernames: tuple[str, ...] = TARGET_USERS) -> list[tuple[str, str, str]]:
    users_sql = ",".join("'" + u.replace("'", "''") + "'" for u in usernames)
    sql = (
        "select u.username, d.hwid, coalesce(d.user_agent, '') "
        "from hwid_user_devices d join users u on u.uuid=d.user_uuid "
        f"where u.username in ({users_sql}) order by u.username, d.created_at desc;"
    )
    proc = subprocess.run(
        ["docker", "exec", REMNA_CONTAINER, "psql", "-U", "postgres", "-d", "postgres", "-At", "-F", "\t", "-c", sql],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        print(f"remnawave hwid query failed: {proc.stderr.strip()}")
        return []
    out = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        username, hwid = parts[0], parts[1]
        ua = parts[2] if len(parts) > 2 else ""
        if hwid in KNOWN_SYNTHETIC_HWIDS or SYNTHETIC_RE.search(hwid) or SYNTHETIC_RE.search(ua):
            out.append((username, hwid, "synthetic/test"))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--remna", action="store_true", help="include Remnawave hwid_user_devices dry-run/apply")
    args = parser.parse_args()

    db_path = Path(args.db)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    deletes, reasons = device_deletes(conn)

    print(f"delete_count={len(deletes)}")
    for token, device_id in deletes:
        row = conn.execute("SELECT name FROM users WHERE token=?", (token,)).fetchone()
        print(f"{row['name'] if row else '<unknown>'}\t{device_id}\t{reasons[(token, device_id)]}")

    remna_deletes: list[tuple[str, str, str]] = []
    if args.remna:
        remna_deletes = remnawave_hwid_candidates()
        print(f"remna_hwid_delete_count={len(remna_deletes)}")
        for username, hwid, reason in remna_deletes:
            print(f"{username}\t{hwid}\t{reason}")

    if not args.apply:
        return

    backup = db_path.with_name(f"{db_path.name}.bak.{time.strftime('%Y%m%d-%H%M%S')}-devices-cleanup")
    shutil.copy2(db_path, backup)
    for token, device_id in deletes:
        conn.execute("DELETE FROM user_devices WHERE token=? AND device_id=?", (token, device_id))
    reparse_happ_metadata(conn)
    sync_user_ips(conn)
    conn.commit()
    print(f"backup={backup}")

    if args.remna and remna_deletes:
        for username, hwid, _ in remna_deletes:
            safe_user = username.replace("'", "''")
            safe_hwid = hwid.replace("'", "''")
            subprocess.run(
                [
                    "docker", "exec", REMNA_CONTAINER, "psql", "-U", "postgres", "-d", "postgres", "-c",
                    "delete from hwid_user_devices d using users u "
                    f"where u.uuid=d.user_uuid and u.username='{safe_user}' and d.hwid='{safe_hwid}';",
                ],
                check=False,
            )
        print("remna hwid cleanup applied")


if __name__ == "__main__":
    main()
