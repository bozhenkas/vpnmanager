#!/usr/bin/env bash
# ru-geo-update.sh — единый «обновлятор»: авто-промоут confirmed RU-ресурсов
# (2+ сигнала, найденных ru-geo-analyzer) в клиентский роутинг + отдаваемую ручку.
# Запускается ru-geo-updater.timer раз в день. Если новых нет — no-op (vpn-bot
# НЕ перезапускается).
set -euo pipefail

REPO="${RGA_REPO:-/root/vpn-bot}"
CANDIDATES="${RGA_CANDIDATES_PATH:-/var/lib/ru-geo-analyzer/candidates.json}"
SERVED="${RGA_SERVED_ROUTING:-/var/www/html/routing.json}"
DENY="${RGA_DENY_FILE:-/etc/ru-geo-analyzer/deny.txt}"
RELOAD="${RGA_RELOAD_CMD:-systemctl restart vpn-bot.service}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${RGA_BACKUP_DIR:-/root/deploy-backups/ru-geo-auto-$STAMP}"

deny_arg=()
[ -f "$DENY" ] && deny_arg=(--deny "$DENY")

exec /usr/bin/python3 "$REPO/scripts/promote_ru_candidates.py" \
  --candidates "$CANDIDATES" \
  --auto-confirmed --apply \
  --served "$SERVED" \
  --backup-dir "$BACKUP_DIR" \
  --sync-remnawave-xray-template \
  --reload-cmd "$RELOAD" \
  "${deny_arg[@]}"
