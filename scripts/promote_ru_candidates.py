#!/usr/bin/env python3
"""
promote_ru_candidates.py — подтверждённый шаг между Go-анализатором и роутингом.

Читает candidates.json (его пишет демон ru-geo-analyzer: RU-серверы по консенсусу
RDAP+ASN+geo, которые geoip:ru пропускает) и кладёт прошедшие фильтр в
subscription/ru_direct_auto.py. Затем регенерирует routing.json.

По умолчанию — DRY-RUN: только показывает, что бы добавилось. Реальная запись —
только с --apply. Это и есть «человек в цикле» для автодетекта.

Фильтры:
  • confidence >= --min-confidence и hits >= --min-hits (Go уже фильтрует, тут
    second gate);
  • домен НЕ добавляется, если уже покрыт курируемым suffix-правилом
    (domain:ru покрывает foo.ru — добавлять незачем). Остаются только
    иностранные TLD на RU-хостинге и сами IP — ровно промахи geoip;
  • денилист (--deny FILE, по строке на host) — никогда не добавлять.

Примеры:
  python3 scripts/promote_ru_candidates.py --candidates candidates.json
  python3 scripts/promote_ru_candidates.py --candidates candidates.json --apply
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTO_FILE = os.path.join(REPO, "subscription", "ru_direct_auto.py")
ROUTING_JSON = os.path.join(REPO, "routing.json")
REMNAWAVE_DB_CONTAINER = os.environ.get("REMNAWAVE_DB_CONTAINER", "remnawave-db")
REMNAWAVE_API_URL = os.environ.get("REMNAWAVE_API_URL", "https://127.0.0.1:30080")
REMNAWAVE_API_TOKEN_NAME = os.environ.get("REMNAWAVE_API_TOKEN_NAME", "codex-migration")


def curated_domain_suffixes() -> set[str]:
    """Домены X из всех 'domain:X' курируемого списка (для suffix-проверки)."""
    sys.path.insert(0, REPO)
    from subscription.ru_routing import RU_DIRECT_SITES  # noqa: E402

    out = set()
    for entry in RU_DIRECT_SITES:
        if entry.startswith("domain:"):
            out.add(entry[len("domain:"):].lower().strip("."))
    return out


def covered_by_suffix(host: str, suffixes: set[str]) -> bool:
    host = host.lower().strip(".")
    for suf in suffixes:
        if host == suf or host.endswith("." + suf):
            return True
    return False


def select_additions(candidates, suffixes, exist_domains, exist_ips, deny,
                     min_conf, min_hits):
    """Чистая логика отбора (тестируется отдельно от файлов/сети).

    Возвращает (add_domains, add_ips, skipped_covered).
    """
    add_domains: list[str] = []
    add_ips: list[str] = []
    skipped_covered = 0
    for c in candidates:
        verdict = c.get("verdict") or {}
        host = (c.get("host") or "").lower().strip(".")
        typ = c.get("type")
        if not host or host in deny:
            continue
        if verdict.get("confidence", 0.0) < min_conf or c.get("hits", 0) < min_hits:
            continue
        if not verdict.get("is_ru", False):
            continue
        if typ == "ip":
            entry = host if "/" in host else f"{host}/32"
            if entry not in exist_ips and entry not in add_ips:
                add_ips.append(entry)
        else:
            if covered_by_suffix(host, suffixes):
                skipped_covered += 1
                continue
            entry = f"domain:{host}"
            if entry not in exist_domains and entry not in add_domains:
                add_domains.append(entry)
    return add_domains, add_ips, skipped_covered


def load_existing_auto() -> tuple[list[str], list[str]]:
    ns: dict = {}
    try:
        with open(AUTO_FILE, encoding="utf-8") as f:
            exec(compile(f.read(), AUTO_FILE, "exec"), ns)  # noqa: S102 — свой файл
        return list(ns.get("RU_DIRECT_AUTO_DOMAINS", [])), list(ns.get("RU_DIRECT_AUTO_IPS", []))
    except FileNotFoundError:
        return [], []


def backup_file(path: str, backup_dir: str | None, label: str) -> None:
    if not backup_dir or not os.path.exists(path):
        return
    os.makedirs(backup_dir, exist_ok=True)
    shutil.copy2(path, os.path.join(backup_dir, f"{label}.bak"))


def pg_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def remnawave_query(sql: str) -> str:
    return subprocess.check_output(
        [
            "docker",
            "exec",
            REMNAWAVE_DB_CONTAINER,
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-At",
            "-c",
            sql,
        ],
        text=True,
    ).strip()


def remnawave_api_token() -> str:
    token = os.environ.get("REMNAWAVE_API_TOKEN", "").strip()
    if token:
        return token
    return remnawave_query(
        "select token from api_tokens "
        f"where token_name={pg_quote(REMNAWAVE_API_TOKEN_NAME)} "
        "order by created_at desc limit 1;"
    ).strip()


def remnawave_basic_auth() -> str:
    auth = os.environ.get("REMNAWAVE_PANEL_BASIC_AUTH", "").strip()
    if auth:
        return auth
    # Prod fallback: legacy vpn-bot still carries the panel basic auth default.
    # Do not print this value; it is only used to form the local API header.
    bot_path = os.path.join(REPO, "vpn-bot.py")
    try:
        text = open(bot_path, encoding="utf-8").read()
    except OSError:
        return ""
    match = re.search(
        r'REMNAWAVE_PANEL_BASIC_AUTH\s*=\s*os\.environ\.get\('
        r'"REMNAWAVE_PANEL_BASIC_AUTH",\s*"([^"]*)"\)',
        text,
    )
    return match.group(1) if match else ""


def sync_remnawave_xray_template(backup_dir: str | None = None) -> None:
    routing = json.load(open(ROUTING_JSON, encoding="utf-8"))["xray_routing"]
    row = remnawave_query(
        "select uuid || E'\\t' || template_json::text "
        "from subscription_templates where template_type = 'XRAY_JSON';"
    )
    uuid, raw_template = row.split("\t", 1)
    template = json.loads(raw_template)
    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        with open(os.path.join(backup_dir, "xray_json_template.before.json"), "w", encoding="utf-8") as f:
            json.dump(template, f, ensure_ascii=False, indent=2)
            f.write("\n")
    template["routing"] = routing

    headers = {
        "Authorization": f"Bearer {remnawave_api_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    basic = remnawave_basic_auth()
    if basic:
        headers["X-Goida-Basic-Auth"] = "Basic " + base64.b64encode(basic.encode()).decode()
    payload = {"uuid": uuid, "templateJson": template}
    request = urllib.request.Request(
        REMNAWAVE_API_URL.rstrip("/") + "/api/subscription-templates/",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers=headers,
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=20,
            context=ssl._create_unverified_context(),
        ) as response:
            response.read()
            print(f"remnawave XRAY_JSON template synced: HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"Remnawave template sync failed: HTTP {exc.code}: {body[:300]}") from exc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=os.environ.get(
        "RGA_CANDIDATES_PATH", "/var/lib/ru-geo-analyzer/candidates.json"))
    ap.add_argument("--min-confidence", type=float, default=0.80)
    ap.add_argument("--min-hits", type=int, default=5)
    ap.add_argument("--deny", default=None, help="файл с host-ами, которые никогда не добавлять")
    ap.add_argument("--apply", action="store_true", help="реально записать (иначе dry-run)")
    ap.add_argument("--auto-confirmed", action="store_true",
                    help="брать ТОЛЬКО candidates с confirmed=true (2+ сигнала); для авто-апдейтера")
    ap.add_argument("--served", default=None,
                    help="путь отдаваемого routing.json (напр. /var/www/html/routing.json) — обновить тоже")
    ap.add_argument("--reload-cmd", default=None,
                    help="shell-команда после реальных изменений (напр. 'systemctl restart vpn-bot.service')")
    ap.add_argument("--backup-dir", default=None,
                    help="папка для backup auto/routing/served/template перед записью")
    ap.add_argument("--sync-remnawave-xray-template", action="store_true",
                    help="после регенерации routing.json синхронизировать Remnawave XRAY_JSON template через API")
    args = ap.parse_args()

    try:
        with open(args.candidates, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"нет файла кандидатов: {args.candidates}", file=sys.stderr)
        return 1

    deny: set[str] = set()
    if args.deny and os.path.exists(args.deny):
        with open(args.deny, encoding="utf-8") as f:
            deny = {ln.strip().lower() for ln in f if ln.strip() and not ln.startswith("#")}

    candidates = data.get("candidates") or []
    if args.auto_confirmed:
        # авто-режим: только то, что демон сам подтвердил по 2+ сигналам.
        candidates = [c for c in candidates if c.get("confirmed")]
        print(f"авто-режим: confirmed-кандидатов {len(candidates)}")

    suffixes = curated_domain_suffixes()
    exist_domains, exist_ips = load_existing_auto()
    add_domains, add_ips, skipped_covered = select_additions(
        candidates, suffixes, exist_domains, exist_ips,
        deny, args.min_confidence, args.min_hits)

    print(f"кандидатов в файле: {data.get('count', len(data.get('candidates', [])))}")
    print(f"уже покрыто курируемым suffix-правилом (пропущено): {skipped_covered}")
    print(f"НОВЫХ доменов к добавлению: {len(add_domains)}")
    for d in add_domains[:50]:
        print(f"  + {d}")
    print(f"НОВЫХ IP к добавлению: {len(add_ips)}")
    for ip in add_ips[:50]:
        print(f"  + {ip}")

    if not args.apply:
        print("\n[dry-run] ничего не записано. Запусти с --apply, чтобы применить.")
        return 0

    if not add_domains and not add_ips:
        print("\nнечего добавлять — авто-файл не изменён.")
        return 0

    new_domains = sorted(set(exist_domains) | set(add_domains))
    new_ips = sorted(set(exist_ips) | set(add_ips))
    backup_file(AUTO_FILE, args.backup_dir, "ru_direct_auto.py")
    backup_file(ROUTING_JSON, args.backup_dir, "routing.json")
    write_auto_file(new_domains, new_ips)
    print(f"\nзаписан {AUTO_FILE}: {len(new_domains)} доменов, {len(new_ips)} IP")

    # регенерация routing.json (атомарно, с валидацией)
    tmp = ROUTING_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as out:
        subprocess.run([sys.executable, os.path.join("subscription", "ru_routing.py")],
                       cwd=REPO, stdout=out, check=True)
    with open(tmp, encoding="utf-8") as f:
        json.load(f)  # валидация
    os.replace(tmp, ROUTING_JSON)
    print(f"регенерирован {ROUTING_JSON}")

    # обновить отдаваемую ручку (Lekanta тянет её раз в час)
    if args.served:
        backup_file(args.served, args.backup_dir, "served-routing.json")
        if os.path.exists(args.served):
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            shutil.copy2(args.served, f"{args.served}.bak.{ts}")
        st = os.stat(args.served) if os.path.exists(args.served) else None
        stmp = args.served + ".tmp"
        shutil.copy2(ROUTING_JSON, stmp)
        if st is not None:
            os.chown(stmp, st.st_uid, st.st_gid)
        os.replace(stmp, args.served)
        print(f"обновлён отдаваемый {args.served}")

    if args.sync_remnawave_xray_template:
        sync_remnawave_xray_template(args.backup_dir)

    # перезапуск/перечитка конфигов — только когда реально что-то добавили
    if args.reload_cmd:
        print(f"reload: {args.reload_cmd}")
        subprocess.run(args.reload_cmd, shell=True, check=True)
    return 0


def write_auto_file(domains: list[str], ips: list[str]) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        '"""',
        "subscription/ru_direct_auto.py — МАШИННО-генерируемый список RU-серверов,",
        "которые `geoip:ru` пропускает. Заполняется scripts/promote_ru_candidates.py",
        "из candidates.json (Go-демон ru-geo-analyzer). НЕ редактировать руками.",
        '"""',
        "from __future__ import annotations",
        "",
        f'AUTO_VERSION = "{ts}"',
        "",
        "RU_DIRECT_AUTO_DOMAINS: list[str] = [",
        *[f'    "{d}",' for d in domains],
        "]",
        "",
        "RU_DIRECT_AUTO_IPS: list[str] = [",
        *[f'    "{ip}",' for ip in ips],
        "]",
        "",
    ]
    tmp = AUTO_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, AUTO_FILE)


if __name__ == "__main__":
    raise SystemExit(main())
