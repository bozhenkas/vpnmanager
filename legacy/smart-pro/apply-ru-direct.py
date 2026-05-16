#!/usr/bin/env python3
"""
apply-ru-direct.py — применяет кастомный RU-direct список к xray routing.

читает /etc/smart-pro/ru-direct-custom.txt (по одному домену на строку),
находит или создаёт глобальное правило в xrayTemplateConfig (ruleTag="custom-ru-direct")
с outboundTag=direct и обновляет список доменов.

правило вставляется ПОСЛЕ общих правил (api/ads/bittorrent/private)
и ПЕРЕД inbound-specific end-user правилами (10001-10005).

ИСПОЛЬЗОВАНИЕ:
  python3 apply-ru-direct.py              — применить (создать бэкап БД)
  python3 apply-ru-direct.py --dry-run    — показать что изменится

после применения вызывающая сторона (бот) должна сделать `systemctl restart x-ui`.
"""

import sqlite3
import json
import sys
import shutil
from datetime import datetime

DB_PATH = "/etc/x-ui/x-ui.db"
CUSTOM_LIST_PATH = "/etc/smart-pro/ru-direct-custom.txt"
RULE_TAG = "custom-ru-direct"

DRY_RUN = "--dry-run" in sys.argv


def read_custom_list(path):
    """читает список доменов, игнорируя пустые строки и комментарии"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []

    domains = []
    seen = set()
    for line in lines:
        d = line.strip()
        if not d or d.startswith("#"):
            continue
        d = d.lower()
        if "." not in d:
            print(f"WARN: пропускаю невалидный домен: {d}", file=sys.stderr)
            continue
        if d in seen:
            continue
        seen.add(d)
        domains.append(d)
    return domains


def find_rule_index(rules, rule_tag):
    """ищет правило по ruleTag, возвращает индекс или -1"""
    for i, r in enumerate(rules):
        if r.get("ruleTag") == rule_tag:
            return i
    return -1


def find_insert_position(rules):
    """
    ищет правильную позицию для нового custom-ru-direct правила:
    после последнего general-правила (no inboundTag, кроме api/ads/bittorrent/private)
    и перед первым end-user inbound-specific правилом (10001-10005).

    стратегия: ищем правило с ip=["geoip:private"] и outboundTag=direct — это
    канонически последнее общее правило. вставляем сразу после него.
    """
    for i, r in enumerate(rules):
        ips = r.get("ip") or []
        if "geoip:private" in ips and r.get("outboundTag") == "direct":
            return i + 1

    # фолбэк: первое правило с inbound-10001..10005
    end_user_tags = {f"inbound-1000{i}" for i in range(1, 6)}
    for i, r in enumerate(rules):
        inb = r.get("inboundTag") or []
        if any(t in end_user_tags for t in inb):
            return i

    return None  # не нашли — требует ручной проверки


def make_rule(domains, rule_tag):
    """формирует правило xray"""
    return {
        "type": "field",
        "ruleTag": rule_tag,
        "domain": [f"domain:{d}" for d in domains],
        "outboundTag": "direct",
    }


def main():
    domains = read_custom_list(CUSTOM_LIST_PATH)

    if DRY_RUN:
        print("=" * 60)
        print("DRY RUN — изменения НЕ применяются")
        print("=" * 60)

    print(f"кастомный список: {CUSTOM_LIST_PATH}")
    print(f"доменов: {len(domains)}")
    if domains:
        for d in domains:
            print(f"  - {d}")
    else:
        print("  (пусто)")
    print()

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT value FROM settings WHERE key='xrayTemplateConfig'"
    ).fetchone()
    if not row:
        print("ОШИБКА: xrayTemplateConfig не найден", file=sys.stderr)
        sys.exit(1)

    cfg = json.loads(row[0])
    rules = cfg.setdefault("routing", {}).setdefault("rules", [])

    idx = find_rule_index(rules, RULE_TAG)

    action = None
    if not domains:
        if idx >= 0:
            action = "delete"
            if not DRY_RUN:
                del rules[idx]
            print(f"список пуст → удаляю правило {RULE_TAG} (позиция {idx})")
        else:
            action = "noop"
            print(f"список пуст и правила нет → ничего не делаем")
    else:
        new_rule = make_rule(domains, RULE_TAG)
        if idx >= 0:
            action = "update"
            old_domains = rules[idx].get("domain", [])
            if not DRY_RUN:
                rules[idx] = new_rule
            print(f"обновляю правило {RULE_TAG} (позиция {idx})")
            print(f"  было: {len(old_domains)} доменов")
            print(f"  стало: {len(domains)} доменов")
        else:
            pos = find_insert_position(rules)
            if pos is None:
                print("ОШИБКА: не смог найти место для вставки правила", file=sys.stderr)
                print("нет правила с geoip:private и нет end-user inbound правил", file=sys.stderr)
                sys.exit(1)
            action = "insert"
            if not DRY_RUN:
                rules.insert(pos, new_rule)
            print(f"вставляю правило {RULE_TAG} в позицию {pos}")

            # покажем контекст
            ctx_start = max(0, pos - 2)
            ctx_end = min(len(rules), pos + 3)
            print("  контекст:")
            # учитываем что в DRY_RUN правило не вставлено
            display_rules = list(rules)
            if DRY_RUN:
                display_rules.insert(pos, new_rule)
                ctx_end = min(len(display_rules), pos + 3)
            for i in range(ctx_start, ctx_end):
                r = display_rules[i]
                inb = r.get("inboundTag", ["*"])
                out = r.get("outboundTag", r.get("balancerTag", "?"))
                tag = r.get("ruleTag", "")
                marker = " ← НОВОЕ" if i == pos and action == "insert" else ""
                print(f"    {i:2d}. [{inb[0]:22s}] → {out}{' (' + tag + ')' if tag else ''}{marker}")

    if DRY_RUN:
        print()
        print("DRY RUN — ничего не записано в БД")
        conn.close()
        return

    if action == "noop":
        conn.close()
        return

    # бэкап перед записью
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{DB_PATH}.bak.{ts}"
    shutil.copy2(DB_PATH, backup)
    print(f"бэкап: {backup}")

    new_cfg_json = json.dumps(cfg, ensure_ascii=False, indent=2)
    conn.execute(
        "UPDATE settings SET value=? WHERE key='xrayTemplateConfig'",
        (new_cfg_json,),
    )
    conn.commit()
    conn.close()

    print()
    print(f"ГОТОВО ({action}). вызывающая сторона должна выполнить:")
    print("  systemctl restart x-ui")


if __name__ == "__main__":
    main()
