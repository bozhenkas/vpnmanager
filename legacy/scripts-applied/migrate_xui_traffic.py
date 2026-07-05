#!/usr/bin/env python3
"""
migrate_xui_traffic.py — импорт трафика из 3X-UI в Remnawave перед миграцией юзера.

Использование:
  python3 migrate_xui_traffic.py <username>          # один юзер
  python3 migrate_xui_traffic.py --all               # все юзеры из bot.db

Суммирует up+down по всем inbound-email для юзера из /etc/x-ui/x-ui.db
и добавляет к used_traffic_bytes в Remnawave user_traffic (INSERT ON CONFLICT).
Безопасно запускать повторно — сохраняет флаг в bot.db чтобы не задваивать.
"""
import sys
import sqlite3
import subprocess
from datetime import datetime, timezone

XUI_DB  = "/etc/x-ui/x-ui.db"
BOT_DB  = "/root/vpn-bot/bot.db"
CONTAINER = "remnawave-db"

# префиксы email в 3X-UI (из INBOUNDS + HYDRA_INBOUNDS в vpn-bot.py)
EMAIL_PREFIXES = [
    "",          # smart / smart-pro (нет префикса)
    "fin-",
    "swe-",
    "fi2-",
    "zapret-",
    "usa-",
    "pol-",
    "tur-",
    "nl-",
    "de-",
]


def rq(sql: str) -> str:
    p = subprocess.run(
        ["docker", "exec", CONTAINER,
         "psql", "-U", "postgres", "-d", "postgres", "-At", "-c", sql],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
    )
    return p.stdout.strip() if p.returncode == 0 else ""


def pg_q(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def xui_traffic(username: str) -> tuple[int, int]:
    """возвращает (total_up, total_down) для юзера по всем inbound-email."""
    emails = [f"{pref}{username}" for pref in EMAIL_PREFIXES]
    placeholders = ",".join("?" * len(emails))
    conn = sqlite3.connect(XUI_DB, timeout=15)
    rows = conn.execute(
        f"SELECT up, down FROM client_traffics WHERE email IN ({placeholders})",
        emails,
    ).fetchall()
    conn.close()
    total_up   = sum(int(r[0] or 0) for r in rows)
    total_down = sum(int(r[1] or 0) for r in rows)
    return total_up, total_down


def remna_user_tid(username: str) -> int | None:
    raw = rq(f"SELECT t_id FROM users WHERE username={pg_q(username)} LIMIT 1;")
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def already_imported(bot_conn: sqlite3.Connection, username: str) -> bool:
    """проверяет, был ли трафик уже импортирован (флаг в bot.db)."""
    try:
        row = bot_conn.execute(
            "SELECT 1 FROM xui_traffic_imported WHERE username=?", (username,)
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False


def mark_imported(bot_conn: sqlite3.Connection, username: str):
    bot_conn.execute(
        "CREATE TABLE IF NOT EXISTS xui_traffic_imported "
        "(username TEXT PRIMARY KEY, imported_at TEXT)"
    )
    bot_conn.execute(
        "INSERT OR REPLACE INTO xui_traffic_imported (username, imported_at) VALUES (?, ?)",
        (username, datetime.now(timezone.utc).isoformat()),
    )
    bot_conn.commit()


def migrate_user(username: str, bot_conn: sqlite3.Connection, dry_run: bool = False):
    if already_imported(bot_conn, username):
        print(f"  {username}: уже импортирован, пропускаем")
        return

    up, down = xui_traffic(username)
    total = up + down
    if total == 0:
        print(f"  {username}: нет трафика в 3X-UI — пропускаем")
        return

    t_id = remna_user_tid(username)
    if t_id is None:
        print(f"  {username}: не найден в Remnawave — пропускаем")
        return

    up_gb   = up   / 1024**3
    down_gb = down / 1024**3
    total_gb = total / 1024**3
    print(f"  {username}: up={up_gb:.2f} GB  down={down_gb:.2f} GB  total={total_gb:.2f} GB  → t_id={t_id}")

    if dry_run:
        print(f"  [dry-run] не записываем")
        return

    # INSERT … ON CONFLICT — атомарно добавляем к существующему значению
    sql = (
        f"INSERT INTO user_traffic (t_id, used_traffic_bytes, lifetime_used_traffic_bytes) "
        f"VALUES ({t_id}, {total}, {total}) "
        f"ON CONFLICT (t_id) DO UPDATE SET "
        f"  used_traffic_bytes = user_traffic.used_traffic_bytes + {total}, "
        f"  lifetime_used_traffic_bytes = user_traffic.lifetime_used_traffic_bytes + {total};"
    )
    result = rq(sql)
    print(f"  → psql: {result or 'ok'}")
    mark_imported(bot_conn, username)


def main():
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    bot_conn = sqlite3.connect(BOT_DB, timeout=30)

    if "--all" in sys.argv:
        users = [row[0] for row in bot_conn.execute("SELECT name FROM users").fetchall()]
        print(f"Миграция трафика для {len(users)} юзеров{' [dry-run]' if dry_run else ''}:")
        for username in sorted(users):
            migrate_user(username, bot_conn, dry_run=dry_run)
    elif args:
        username = args[0]
        print(f"Миграция трафика для {username}{' [dry-run]' if dry_run else ''}:")
        migrate_user(username, bot_conn, dry_run=dry_run)
    else:
        print("Использование:")
        print("  python3 migrate_xui_traffic.py <username>")
        print("  python3 migrate_xui_traffic.py --all [--dry-run]")

    bot_conn.close()
    print("done")


if __name__ == "__main__":
    main()
