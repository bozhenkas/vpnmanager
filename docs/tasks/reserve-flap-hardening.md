# Задача: убрать флап Резервного (ru-4) в Happ — безопасный вариант

> Автор постановки: диагностика инцидента 2026-06-24. Исполнитель — другой ИИ-кодер.
> Делать **только безопасные правки A и B (fail2ban)**. Жёсткий allowlist-фаервол на SSH — НЕ делать (риск локаута), он вынесен в отдельную задачу.
> Статус 2026-06-24 03:48 MSK: **A+B применены на проде**. Backup на обеих машинах: `/root/deploy-backups/20260624T004900Z-reserve-flap-hardening/`.

## Контекст (что происходит)

Нода «Резервный» периодически уходит в `n/a` в клиенте Happ, потом сама оживает.

- **Сам box ru-4 здоров** (`load 0`, RAM/диск ок после чистки 2026-06-24). Дело НЕ в ресурсах ноды.
- Egress Резервного устроен так: клиент → `reserve:443` (xray REALITY gRPC, `xray-reserve-fin.service`) → **единственный SSH-туннель** `ru4-fin-tunnel.service` (`ssh -L 127.0.0.1:17905:127.0.0.1:17905` в **публичный sshd FIN на :17904**) → `xray-ru4-egress.service` на FIN → DIRECT (выход с IP FIN).
- Туннель рвётся при **45с тишины** от FIN: опции `ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes`. После обрыва systemd поднимает его за `RestartSec=3`, но **окно без egress → Happ метит ноду n/a**.
- Триггеры тишины: (1) **sshd FIN :17904 жёстко брутфорсят** ботнетом (`130.12.181.x`, `77.83.39.x`, поток `[preauth]`) → спайки `load` до 4–5 на FIN от плодящихся процессов sshd; (2) кратковременные блэкауты на длинном пути reserve↔FIN (RTT ~79ms).
- Подтверждение в журнале reserve: `03:26:33 ssh[833]: Timeout, server 77.110.108.57 not responding → ru4-fin-tunnel.service restart`.

Цель: уменьшить частоту обрывов туннеля (→ меньше n/a), не меняя саму egress-схему.

## Серверы / доступ

| host | IP | SSH | роль в задаче |
|---|---|---|---|
| ru-4 / reserve | `194.117.80.94` | `root` ключ `~/.ssh/id_rsa`, **порт 22** | правка A (юнит туннеля) |
| FIN | `77.110.108.57` | `root` **порт 17904** | правка B (fail2ban) |

SSH к ru-4 может **флапать** (banner timeout под блипом) — ретраить коннект 3–5 раз.
Источник истины по деплою reserve-стека: `scripts/p15b_ru4_reserve_fin_deploy.sh`. Drop-in лимиты: `deploy/systemd/proposed/ru4-fin-tunnel-limits.conf`.

## Hard rules

- Прод-мутация с backup и по runbook. Перед правкой снять `cp -a` затрагиваемых файлов в `/root/deploy-backups/$(date -u +%Y%m%dT%H%M%SZ)-<tag>/`.
- **FIN не ломать**: на нём крутятся сторонние сервисы (docker: dropp-server, daywith, belekker, vpndeployer и т.д.). fail2ban НЕ должен блокировать IP reserve `194.117.80.94`, RU `45.91.54.152`, SWE `89.22.230.5`, FRA `95.163.152.210`, HOME `78.107.88.21` и owner IP `176.15.167.130`/`45.157.235.132` — занести их в `ignoreip`.
- НЕ трогать xray-инбаунды/REALITY-ключи, НЕ менять `-L`-форвард, НЕ менять egress-цепочку.
- НЕ ставить allowlist-фаервол на :17904 (отдельная задача).
- Минимальный diff. Не откатывать чужие изменения.

---

## Правка A — устойчивый keepalive туннеля (на ru-4, низкий риск)

Файл: `/etc/systemd/system/ru4-fin-tunnel.service` на `194.117.80.94`.

Текущий `ExecStart`:
```
/usr/bin/ssh -NT -i /root/.ssh/ru4_fin_tunnel -p 17904 -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=no -L 127.0.0.1:17905:127.0.0.1:17905 root@77.110.108.57
```

Изменить keepalive так, чтобы кратковременный (до ~2 мин) спайк/блип не убивал туннель, и добавить TCP keepalive:
- `ServerAliveInterval=15` → оставить `15`
- `ServerAliveCountMax=3` → **`8`** (терпит ~120с тишины вместо 45с)
- добавить `-o TCPKeepAlive=yes`
- оставить `ExitOnForwardFailure=yes`, `Restart=always`, `RestartSec=3`, drop-in лимиты не трогать.

Применение:
```bash
systemctl daemon-reload
systemctl restart ru4-fin-tunnel.service
```
Минус (приемлемый): реальную смерть туннеля systemd заметит на ~75с позже — но рестарт всё равно авто.

### Проверка A
- На ru-4: `systemctl show ru4-fin-tunnel.service -p ExecStart` содержит `ServerAliveCountMax=8` и `TCPKeepAlive=yes`.
- На ru-4: `ss -tlnp | grep 17905` — слушает (форвард поднят).
- На FIN `77.110.108.57`: `ss -tn | grep 194.117.80.94` — ESTAB, `Send-Q ≈ 0`.
- На FIN: `ss -tn | grep :17905 | awk '{print $1}' | sort | uniq -c` — есть `ESTAB` (живой egress-трафик), нет роста `CLOSE-WAIT`.
- В журнале reserve после рестарта нет нового `Timeout, server ... not responding` в течение наблюдения.

---

## Правка B — fail2ban на sshd FIN (на FIN, без риска локаута)

Цель: снять брутфорс-флуд с :17904 → убрать спайки load → меньше триггеров обрыва. Это не блокирует легитимные коннекты (в отличие от allowlist-фаервола).

На `77.110.108.57`:
1. Установить `fail2ban` (apt/dnf по факту дистрибутива; проверить package manager).
2. `/etc/fail2ban/jail.d/sshd.local`:
   ```ini
   [sshd]
   enabled  = true
   port     = 17904
   backend  = systemd
   maxretry = 4
   findtime = 10m
   bantime  = 1h
   ignoreip = 127.0.0.1/8 ::1 194.117.80.94 45.91.54.152 89.22.230.5 95.163.152.210 78.107.88.21 176.15.167.130 45.157.235.132
   ```
   `port=17904` обязателен (sshd слушает не на 22). `backend=systemd` т.к. логи в journald. Перед применением проверить текущий клиентский IP активной SSH-сессии (`echo "$SSH_CLIENT"`) и добавить его в `ignoreip`, если он отличается от owner IP выше.
3. `systemctl enable --now fail2ban`.

### Проверка B
- `fail2ban-client status sshd` — jail активен, порт 17904, со временем растёт `Banned`.
- `iptables -S | grep f2b` (или `nft list ruleset | grep f2b`) — появилась цепочка fail2ban.
- **Самопроверка от локаута**: убедиться, что свой IP и IP reserve/RU/SWE/FRA/HOME в `ignoreip`; проверить, что текущая SSH-сессия и туннель reserve→FIN живы (`ss -tn | grep 194.117.80.94` ESTAB).
- Через ~10–15 мин: `load` на FIN ниже, в журнале меньше `[preauth]`-спама.

---

## Rollback

- A: вернуть `ServerAliveCountMax=3`, убрать `TCPKeepAlive`, `daemon-reload`, `restart ru4-fin-tunnel.service` (или восстановить юнит из backup).
- B: `systemctl disable --now fail2ban` (цепочки f2b снимутся), при необходимости удалить пакет.

## Definition of done

- Туннель reserve→FIN переживает кратковременные блипы без рестарта (проверки A зелёные).
- fail2ban банит брутфорс на :17904, `load` FIN снизился, легитимные IP в `ignoreip` (проверки B зелёные).
- egress Резервного идёт (`:17905` ESTAB на FIN), нода не флапает в Happ под наблюдением.
- Backup'ы сняты, изменения минимальны, FIN-сервисы не задеты.
- Обновить память проекта (`.claude/memory/secrets-and-logic.md`, `tasks.md`): что применено A/B и фактические значения.
