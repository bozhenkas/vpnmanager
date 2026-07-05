# Инцидент 2026-06-25 — FIN/FRA/SWE недоступны (сторона хостинг-провайдера)

> Tracked-доку без секретов. Точные IP/uuid/токены/конфиги — в gitignored
> `.claude/memory/secrets-and-logic.md` и `.claude/memory/facts.md`.
> Все бэкапы и команды отката приведены ниже.

## 1. Симптом и диагноз (verified read-only)
Зарубежные выходы **FIN (`77.110.108.57`), FRA (`95.163.152.210`), SWE (`89.22.230.5`)**
недоступны. По словам владельца — сбой на стороне хостинг-провайдера, не блокировки.

Замеры на момент инцидента:
- RU → FIN/FRA/SWE `:443` (TLS) = `000` (таймаут); RU → `:17904` (SSH) = connection timed out.
- С внешней (домашней) сети те же боксы отвечают на TCP SYN — то есть на сетевом
  уровне живы где-то, но из RU-ДЦ payload не проходит (та же сигнатура, что и при
  RU↔foreign freeze 2026-06-19). Для целей сервиса это значения не меняет: из кластера
  выходы мертвы, клиенты на `Оптимальный`/`Финляндия`/`Франция`/`Швеция` сломаны.
- RU `remnanode`: ~13 «Xray started» за час — Remnawave health-каскад рестартит весь
  RU-xray из-за отвалившихся foreign-нод (бьёт по всем RU-инбаундам).
- **Hydra жива и достижима из RU** (whitestore `200` за 0.5s; NL/POL/TUR healthy,
  DE интермиттентно). Hydra выходит RU→whitestore, НЕ RU→foreign — поэтому годится
  как замена.
- Резервный (`reserve.goida.fun` / `31.77.169.26`) жив, но его штатное плечо
  HY2-UDP→FIN/SWE тоже мертво (отсюда task 3 — переключение reserve на NL).

## 2. Что сделано (временно, до починки FIN/FRA/SWE)

Все изменения за флагами/константами для быстрого отката.

1. **Память** — зафиксирован инцидент в `.claude/memory/facts.md` и активная задача
   с откатом в `.claude/memory/tasks.md`.
2. **Заглушка вместо FIN/FRA/SWE** — в `bot/vpn-bot.py` `remnawave_subscription_links`
   при `FOREIGN_EXITS_DOWN=1` три строки `/fin /fra /swe` заменены одним плейсхолдером
   `🇷🇺⚠️🇸🇪🇫🇮🇫🇷 ВРЕМЕННО НЕДОСТУПНЫ ⚠️` (путь `/unavailable` — не WS-инбаунд, nginx 302,
   клиент видит n/a, что и сообщает недоступность).
3. **Резервный → Нидерланды (hydra)** — на reserve в `/etc/xray/reserve.json` добавлен
   outbound `proxy-nl` (xHTTP+Reality, реплика hydra `HYDRA_NL_3`, upstream
   `nld.north-1winter.cv:443`), балансер `FOREIGN` selector переключён на `[proxy-nl]`.
   Цепочка: `client → Резервный (gRPC Reality :443) → NL hydra`. `proxy-fin/proxy-swe`
   оставлены в конфиге для быстрого возврата. Verified: exit `37.49.225.121` (NL).
4. **`Оптимальный` балансит hydra** — в `subscription/engine.py` при `SMART_OVER_HYDRA=True`
   клиентский balancer строится не на `/fin /fra /swe`, а на `/hydra-nl /hydra-de
   /hydra-pol /hydra-tur` (тот же user-uuid, через наш же RU-вход), fallback —
   `Резервный` (который теперь сам идёт в NL). zapret/`/direct` и RU-direct split
   без изменений. Verified: exit `45.157.235.132` (живой foreign hydra-выход).
5. **`Оптимальный Лайт` убран** — при `FOREIGN_EXITS_DOWN=1` строка Лайт не добавляется
   (его foreign-плечо тоже шло через мёртвый `GOIDA_BALANCER_SMART`).
6. **Hydra универсальна** — все 29 юзеров добавлены во все 4 активных hydra-сквада
   (`HYDRA_{DE,NL,POL,TUR}_REMNA`, было по 5 → стало по 29), `include_hydra` форсится
   для всех. WL (whitelist) не трогали — гейтинг как был. Verified: ранее-не-hydra
   юзер `test-n` принят на `/hydra-nl`, exit `37.49.225.121`.
7. **Бэкапы + этот отчёт.**

## 3. Бэкапы
- RU код+БД: `/root/deploy-backups/20260624T235645Z-fin-fra-swe-outage/`
  (`vpn-bot.py.bak`, `engine.py.bak`, `__init__.py.bak`, `routing.json.bak`,
  `internal_squad_members.sql.gz`) + таблица
  `internal_squad_members_bak_20260624T235645Z_outage` (срез до backfill, 109 строк).
- Reserve: `/root/deploy-backups/20260624T235645Z-reserve-nl-egress/`
  (`etc-xray.bak/`, `etc-hysteria.bak/`, `xray-reserve.service.txt`) +
  `/etc/xray/reserve.json.bak.20260625-nl-egress`.
- Локально (worktree): git-diff `bot/vpn-bot.py`, `subscription/engine.py`,
  `tests/test_subscription_engine.py`.

## 4. Откат (после восстановления FIN/FRA/SWE)

Проверить сперва, что выходы живы из RU: `curl -sk --max-time 6 https://<ip>:443/`
(ожидать TLS-ответ, не `000`) для FIN/FRA/SWE.

**Быстрый откат (флагами, без редеплоя кода):**
1. RU: в окружении vpn-bot выставить `FOREIGN_EXITS_DOWN=0`
   (`/etc/systemd/system/vpn-bot.service` или `.env`), `systemctl restart vpn-bot`.
   → вернутся Финляндия/Франция/Швеция, Лайт; заглушка исчезнет; hydra снова по гейтингу
   (`user_hydra_enabled`).
2. RU: в `subscription/engine.py` `SMART_OVER_HYDRA=False`, redeploy
   `engine.py` + restart vpn-bot → `Оптимальный` снова на fin/fra/swe.
   (это не флаг окружения — нужна правка файла или восстановление из `engine.py.bak`.)

**Полный откат (восстановление файлов из бэкапа):**
```
# RU
cp /root/deploy-backups/20260624T235645Z-fin-fra-swe-outage/vpn-bot.py.bak /root/vpn-bot/vpn-bot.py
cp /root/deploy-backups/20260624T235645Z-fin-fra-swe-outage/engine.py.bak  /root/vpn-bot/subscription/engine.py
cd /root/vpn-bot && python3 -m py_compile vpn-bot.py subscription/engine.py && systemctl restart vpn-bot
# reserve
cp /etc/xray/reserve.json.bak.20260625-nl-egress /etc/xray/reserve.json
xray run -test -c /etc/xray/reserve.json && systemctl restart xray-reserve
```

**Откат универсальной hydra (task 6), если нужно вернуть гейтинг по squad-membership:**
```
# на RU, в контейнере remnawave-db: удалить добавленные строки, оставив исходный срез
docker exec remnawave-db psql -U postgres -d postgres -c \
 "delete from internal_squad_members m using internal_squads s
  where m.internal_squad_uuid=s.uuid and s.name like 'HYDRA\_%\_REMNA'
  and (m.internal_squad_uuid, m.user_id) not in
      (select internal_squad_uuid, user_id from internal_squad_members_bak_20260624T235645Z_outage);"
# затем restart-all нод (POST /api/nodes/actions/restart-all forceRestart:true)
```
Примечание: при общем откате задачи hydra обычно НЕ откатывают (она безвредна и
полезна), достаточно вернуть `FOREIGN_EXITS_DOWN=0`.

## 4a. Регрессия Резервного (поймана и исправлена)
При первом деплое `bot/vpn-bot.py` был **залит локальный worktree-файл, устаревший
относительно прода** (локально 5580 строк с дивергенцией, прод 5555 с фиксами
2026-06-24). Это затёрло корректную обработку Резервного: ссылка стала отдавать
per-user uuid + старые Reality-параметры ru-4 (`pbk UpJ1_…`, `sid 1df9284e…`,
`fp chrome`) вместо нового VPS (`shared uuid 2d08f735…`, `pbk Pm8yHbvRWJ…`,
`sid 7c6fc767…`, `fp firefox`). Симптом: Happ «Резервный» n/a, в xray
`REALITY: received real certificate`.
**Фикс:** взял прод-бэкап (`vpn-bot.py.bak`, корректная база 2026-06-24), наложил
ТОЛЬКО 3 инцидент-правки, передеплоил. Битый деплой сохранён как
`vpn-bot.py.STALE-BROKEN-DEPLOY` в backup-каталоге. Локальный worktree
`bot/vpn-bot.py` синхронизирован с корректной версией. `engine.py` дивергенции
НЕ имел (проверено diff'ом — только мои правки). Verified: Резервный handshake OK,
exit `37.49.225.121` (NL).
**Урок:** `bot/vpn-bot.py` в worktree может отставать от прода (мульти-агент). НЕ
заливать его целиком — деплоить от прод-бэкапа как базы, либо diff'ить против live
перед rsync.

## 5. Тесты
- `tests/test_subscription_engine.py` (21) — обновлены 2 теста smart-balancer под
  hydra-режим (с комментом про откат при `SMART_OVER_HYDRA=False`). Все зелёные.
- `tests/test_bot_devices.py` (16) — зелёные.
