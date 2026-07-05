#!/usr/bin/env bash
# goida_warn_collect.sh — единый сборщик WARNING/ERROR со всех ключевых источников RU.
# Только читает логи и аппендит в один файл; ничего не рестартит и не мутирует VPN-конфиг.
# Запускается systemd-таймером goida-warn-collect.timer (каждые 5 мин).
#
# Выход:  /var/log/goida/warn-error.log   — строки вида:
#   2026-06-14T11:20:01Z [remnanode-error] 2026/06/14 11:19:58 [Error] ...
# State:  /var/lib/goida-warn/            — офсеты файлов + epoch журналов (since-last-run).
# Первый запуск стартует с конца файловых логов (без исторического бэклога).
set -uo pipefail

OUT=/var/log/goida/warn-error.log
STATE=/var/lib/goida-warn
mkdir -p "$(dirname "$OUT")" "$STATE"
touch "$OUT"

now_iso() { date -u +%FT%TZ; }
emit() { # emit <source-tag>  (читает stdin построчно)
  local src line
  src="$1"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    printf '%s [%s] %s\n' "$(now_iso)" "$src" "$line" >> "$OUT"
  done
}

# --- 1. файловые логи по байт-офсету (формат-агностично, переживает ротацию) ---
collect_file() { # collect_file <src-tag> <path> <egrep-filter>
  local src f filt off size offf
  src="$1"; f="$2"; filt="$3"
  [ -f "$f" ] || return 0
  offf="$STATE/${src}.off"
  size=$(stat -c %s "$f" 2>/dev/null || echo 0)
  if [ ! -f "$offf" ]; then     # первый запуск — стартуем с конца, без бэклога
    echo "$size" > "$offf"; return 0
  fi
  off=$(cat "$offf" 2>/dev/null || echo 0)
  [ "$off" -gt "$size" ] && off=0          # файл усечён/ротирован
  if [ "$off" -lt "$size" ]; then
    tail -c +$((off + 1)) "$f" | grep -aE "$filt" | emit "$src"
  fi
  echo "$size" > "$offf"
}

# Xray на ноде: рестарты (Warning: "Xray ... started"), Error, отказы flow/uuid, observatory timeout
collect_file remnanode-error /var/log/remnanode/error.log '\[Warning\]|\[Error\]'
# nginx: warn/error/crit/alert/emerg
collect_file nginx-error    /var/log/nginx/error.log     '\[(warn|error|crit|alert|emerg)\]'

# --- 2. docker remnawave backend (since последний прогон) ---
collect_docker() { # collect_docker <src-tag> <container>
  local src cont sf since
  src="$1"; cont="$2"
  sf="$STATE/${src}.iso"
  docker inspect "$cont" >/dev/null 2>&1 || return 0
  since=$(cat "$sf" 2>/dev/null || echo "5m")   # RFC3339 с прошлого прогона либо "5m" на первом
  docker logs "$cont" --since "$since" 2>&1 \
    | sed -r 's/\x1b\[[0-9;]*m//g' \
    | grep -aE 'WARN|ERROR' \
    | emit "$src"
  now_iso > "$sf"
}
collect_docker remnawave-backend remnawave

# --- 3. systemd journald units (priority warning+) ---
collect_journal() { # collect_journal <src-tag> <unit>
  local src unit sf last now
  src="$1"; unit="$2"
  sf="$STATE/journal-${src}.epoch"
  systemctl list-unit-files "${unit}.service" >/dev/null 2>&1 || return 0
  now=$(date +%s)
  last=$(cat "$sf" 2>/dev/null || echo $((now - 300)))
  journalctl -u "$unit" --since "@$last" --until "@$now" -p warning -o short-iso --no-pager 2>/dev/null \
    | grep -avE '^-- ' | emit "$src"
  echo "$now" > "$sf"
}
collect_journal vpn-bot          vpn-bot
collect_journal sub-updater      sub-updater
collect_journal client-bot       goida-client-bot
collect_journal remnawave-svc    remnawave

exit 0
