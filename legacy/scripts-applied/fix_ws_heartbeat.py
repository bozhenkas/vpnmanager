#!/usr/bin/env python3
"""добавляет heartbeatPeriod=30 в активные ws inbounds 3x-ui."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


TARGET_HEARTBEAT = 30


def load_stream_settings(raw: str | None, inbound_id: int) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"inbound id={inbound_id}: bad stream_settings json: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"inbound id={inbound_id}: stream_settings is not an object")
    return data


def fix_ws_heartbeat(db_path: str, *, dry_run: bool = False) -> list[tuple[int, str]]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, remark, stream_settings FROM inbounds WHERE enable=1 ORDER BY id"
        ).fetchall()
        changed: list[tuple[int, str]] = []
        for inbound_id, remark, raw_stream_settings in rows:
            settings = load_stream_settings(raw_stream_settings, int(inbound_id))
            if settings.get("network") != "ws":
                continue
            if settings.get("heartbeatPeriod") == TARGET_HEARTBEAT:
                continue
            settings["heartbeatPeriod"] = TARGET_HEARTBEAT
            changed.append((int(inbound_id), str(remark or "")))
            if not dry_run:
                conn.execute(
                    "UPDATE inbounds SET stream_settings=? WHERE id=?",
                    (json.dumps(settings, ensure_ascii=False, separators=(",", ":")), inbound_id),
                )
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        return changed
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ensure heartbeatPeriod=30 on active 3x-ui ws inbounds")
    parser.add_argument("db_path", help="path to x-ui sqlite db, e.g. /etc/x-ui/x-ui.db")
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        parser.error(f"db not found: {db_path}")

    changed = fix_ws_heartbeat(str(db_path), dry_run=args.dry_run)
    for inbound_id, remark in changed:
        print(f"{inbound_id}\t{remark}")
    prefix = "DRY RUN: " if args.dry_run else ""
    print(f"{prefix}{len(changed)} inbound(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
