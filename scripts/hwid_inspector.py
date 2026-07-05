#!/usr/bin/env python3
"""Read-only hourly audit of Remnawave HWID and user-agent data."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
from collections import defaultdict
from datetime import datetime, timezone


BOT_DB = "/root/vpn-bot/bot.db"
REMNA_CONTAINER = "remnawave-db"
VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")
VALID_HWID_RE = re.compile(r"^[A-Za-z0-9_.:-]{4,128}$")
SYNTHETIC_RE = re.compile(
    r"check|test-hwid|incident|strict-verify|header-verify|diag|shadow|smoke|"
    r"vk-route|boosty|codex",
    re.I,
)
HAPP_NON_HWID = {
    "happ", "ios", "android", "windows", "macos", "macos catalyst",
    "apple tv", "android tv",
}


def expected_hwid(user_agent: str) -> str:
    ua = (user_agent or "").strip()
    match = re.search(r"(?:hwid|device[-_ ]?id)[:=/ ]([A-Za-z0-9_.:-]{4,128})", ua, re.I)
    if match:
        return match.group(1)
    if not ua.startswith("Happ/"):
        return ""
    for part in reversed(ua.split("/")[1:]):
        value = part.strip()
        if VERSION_RE.fullmatch(value):
            continue
        if VALID_HWID_RE.fullmatch(value) and value.lower() not in HAPP_NON_HWID:
            return value
    return ""


def inspect(rows: list[dict], limits: dict[str, int]) -> dict:
    issues: list[dict] = []
    by_hwid: dict[str, list[str]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)

    for row in rows:
        username = row["username"]
        hwid = row["hwid"]
        ua = row.get("user_agent") or ""
        counts[username] += 1
        by_hwid[hwid].append(username)

        if VERSION_RE.fullmatch(hwid):
            issues.append({"kind": "version_hwid", "username": username, "hwid": hwid})
        elif not VALID_HWID_RE.fullmatch(hwid):
            issues.append({"kind": "malformed_hwid", "username": username, "hwid": hwid})
        if SYNTHETIC_RE.search(" ".join((hwid, row.get("device_model") or "", ua))):
            issues.append({"kind": "synthetic_hwid", "username": username, "hwid": hwid})

        expected = expected_hwid(ua)
        if expected and expected != hwid:
            issues.append({
                "kind": "ua_hwid_mismatch",
                "username": username,
                "hwid": hwid,
                "ua_hwid": expected,
            })
        elif ua.startswith("Happ/") and not expected:
            issues.append({"kind": "happ_ua_without_hwid", "username": username, "hwid": hwid})

    shared = [
        {"hwid": hwid, "users": sorted(set(users), key=str.lower)}
        for hwid, users in by_hwid.items()
        if len(set(users)) > 1
    ]
    shared.sort(key=lambda item: (-len(item["users"]), item["hwid"]))

    users = []
    for username in sorted(set(counts) | set(limits), key=str.lower):
        limit = int(limits.get(username, 0))
        actual = counts.get(username, 0)
        users.append({
            "username": username,
            "devices": actual,
            "limit": limit,
            "over_limit": bool(limit > 0 and actual > limit),
        })

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "rows": len(rows),
        "unique_hwids": len(by_hwid),
        "issues": issues,
        "shared_hwids": shared,
        "users": users,
    }


def remna_rows() -> list[dict]:
    sql = (
        "select u.username,d.hwid,coalesce(d.platform,''),"
        "coalesce(d.device_model,''),coalesce(d.user_agent,''),"
        "d.created_at,d.updated_at "
        "from hwid_user_devices d join users u on u.uuid=d.user_uuid "
        "order by u.username,d.hwid;"
    )
    proc = subprocess.run(
        [
            "docker", "exec", REMNA_CONTAINER, "psql", "-U", "postgres",
            "-d", "postgres", "-At", "-F", "\t", "-c", sql,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "remnawave query failed")
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        rows.append(dict(zip(
            ("username", "hwid", "platform", "device_model", "user_agent", "created_at", "updated_at"),
            parts,
        )))
    return rows


def local_limits(path: str = BOT_DB) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            username: int(limit or 0)
            for username, limit in conn.execute(
                "select username,device_limit from client_profiles order by username"
            )
        }
    finally:
        conn.close()


def print_report(report: dict, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return
    print(
        "hwid-inspector "
        f"rows={report['rows']} unique_hwids={report['unique_hwids']} "
        f"issues={len(report['issues'])} shared={len(report['shared_hwids'])}"
    )
    for issue in report["issues"]:
        detail = " ".join(f"{key}={value}" for key, value in issue.items())
        print(f"issue {detail}")
    for item in report["shared_hwids"]:
        print(f"shared hwid={item['hwid']} users={','.join(item['users'])}")
    for item in report["users"]:
        marker = " over_limit=true" if item["over_limit"] else ""
        print(
            f"user username={item['username']} devices={item['devices']} "
            f"limit={item['limit']}{marker}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = inspect(remna_rows(), local_limits())
        print_report(report, args.json)
    except Exception as exc:
        # Инспектор ничего не чинит и не влияет на сервисы, даже если аудит не состоялся.
        print(f"hwid-inspector audit_error={type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
