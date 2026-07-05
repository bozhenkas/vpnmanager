# Кластерный аудит — 2026-07-01

Read-only аудит по плану `wild-stirring-fountain`: shadow-smoke всех профилей bozhenkas, инвентарь SSH/HY2/epilepsy-туннелей между RU/FIN/SWE/FRA/reserve, здоровье процессов на RU, DNS/failover, Hydra/WL/Lekanta. 6 параллельных read-only агентов, окно проверки ~16:00–16:25 UTC 2026-07-01. Провайдер (FIN/FRA/SWE) исключён как причина проблем — сеть у него в порядке (см. предыдущий диалог), аудит проверял нашу сторону.

## 🔴 Активный инцидент, найден во время аудита

**`vpn-bot.service` не может достучаться до Telegram API с 16:11 UTC (на момент аудита — непрерывно).**

Корень причины (подтверждено напрямую):
- `deploy/systemd/vpn-bot.service.d/telegram-proxy.conf` на RU содержит канареечную правку от **2026-06-28** — комментарий `CANARY 2026-06-28: egress via HY2 REALITY-over-UDP (hysteria-client-fin) instead of SSH tunnel`. `HTTPS_PROXY`/`HTTP_PROXY` для `vpn-bot.service` указывают на `127.0.0.1:18888`, и юнит теперь `Requires=hysteria-client-fin.service`.
- Порт `18888` слушает сам процесс `hysteria-client-fin` (pid 19140, HY2 QUIC-клиент RU→FIN).
- Этот процесс с ~16:10 UTC непрерывно пишет `WARN TCP forwarding error: connect error: timeout: no recent network activity` — QUIC-сессия к FIN зависла/умерла, каждый новый TCP-forward (= каждая попытка vpn-bot достучаться до Telegram) отваливается по таймауту → в логах vpn-bot это видно как `tg api error: Connection reset by peer`.
- **`goida-client-bot.service` не затронут** — у него отдельный drop-in (`telegram-proxy.conf`), всё ещё указывающий на старый стабильный SSH-туннель `127.0.0.1:8888` (tinyproxy на FIN через `telegram-proxy-tunnel.service`). Ручная проверка подтвердила: путь через `8888` работает (`curl -x http://127.0.0.1:8888 https://api.telegram.org` → `302` за 0.4с), путь через `18888` — нет.

**Эффект:** основной bot.py (юзер-фейсинг бот — подписки, `/devices`, весь админ-функционал) сейчас не получает апдейты от Telegram и не отвечает пользователям.

**Post-audit fix applied 2026-07-01 18:24 UTC:** по owner-направлению не трогать старый SSH-туннель, `vpn-bot` переведён на RU→FRA `epilepsy` L3-route. Backup: `/root/deploy-backups/20260701T1825Z-vpn-bot-telegram-epilepsy/`. Новый `goida-telegram-api-route.service` ставит `149.154.160.0/20 via 10.89.0.1 dev stun0`; `vpn-bot.service.d/telegram-proxy.conf` убрал proxy env на `18888` и требует `epilepsy-client.service` + route-unit. Verify: direct RU `curl -4 --noproxy '*' https://api.telegram.org` → `302` за ~0.4s, `vpn-bot active/running`, после рестарта новых `tg api error` нет.

Исторически на момент read-only аудита были два очевидных варианта фикса:
1. Откатить `vpn-bot.service.d/telegram-proxy.conf` обратно на порт `8888` (тот же путь, что у goida-client-bot) — быстро, но откатывает канареечный эксперимент.
2. Разобраться, почему у `hysteria-client-fin` зависла QUIC-сессия к FIN, и перезапустить/оживить именно её — сохраняет канарейку, но требует диагностики HY2-сессии.

---

## 1. Shadow-smoke — 16/16 профилей bozhenkas PASS

Полный прогон реального Xray-клиента по каждому профилю точной JSON-подписки (`Happ/4.10.2/ios/shadow-smoke` UA), проверка `generate_204` через реальный трафик, не просто TLS-handshake.

| Профиль | WS/handshake | generate_204 | Вердикт |
|---|---|---|---|
| Оптимальный 🇸🇨 | 101 (`/fin`,`/swe`,`/direct`) | 204 (через `proxy-swe`) | PASS |
| Резервный 🇰🇵 (мобильная) | gRPC/reality | 204 | PASS |
| Финляндия 🇫🇮 | 101 `/fin` | 204 | PASS* |
| Швеция 🇸🇪 | 101 `/swe` | 204 | PASS |
| Франция 🇫🇷 | 101 `/fra` | 204 | PASS |
| Русский (YT/Discord) 🇷🇺 | 101 `/direct` | 204 | PASS |
| Германия/Нидерланды/Польша/Турция (Hydra ×4) | 101 `/hydra-*` | 204 каждый | PASS |
| Whitelist 1–6 🇷🇺 | reality/tcp | 204 каждый | PASS |

Все 16 профилей, никаких `[Расходует трафик]`, порт `7443` нигде не торчит наружу, у всех есть `routing`.

**\*Аномалия во время теста:** Remnawave-нода `fin-goida` показала `is_connected=f` в БД в 16:21:10 UTC — прямо в момент, когда у неё уже минут 10 как разваливалась HY2-сессия (см. инцидент выше). Профиль «Финляндия» при этом всё равно отдал 204 (клиентский путь через `/fin` не совпадает с каналом HY2-канарейки vpn-bot), но совпадение по времени наводит на мысль о более широкой деградации RU↔FIN именно в этом окне — не расследовано глубже, вне read-only-скоупа.

---

## 2. SSH/HY2/epilepsy-туннели — инвентарь и живость

### RU → исходящие (подтверждено с обеих сторон, RU и принимающий сервер)

| Туннель | Куда | Локальный порт | Юнит | Статус | Примечание |
|---|---|---|---|---|---|
| infra-tunnel | FRA `:9082` | `127.0.0.1:9082` | `infra-tunnel.service` | ✅ up 2.6д, идентичен репо | — |
| telegram-proxy-tunnel | FIN `:8888` | `127.0.0.1:8888` | `telegram-proxy-tunnel.service` | ✅ up 1.8д | untracked drop-in с MemoryHigh/CPUQuota — дрейф от репо (см. ниже) |
| **remnawave-admin-tunnel** | **SWE `:31800`** | `127.0.0.1:31800` | `remnawave-admin-tunnel.service` | ✅ up с 27.06 | **Отсутствует в `deploy/systemd/` репо целиком** |
| hysteria-client-fin (primary) | FIN | `127.0.0.1:17450` | `hysteria-client-fin.service` | 🔴 **сейчас деградирует** | см. инцидент выше |
| hysteria-client-swe (primary) | SWE | `127.0.0.1:17451` | `hysteria-client-swe.service` | ✅ up 12д | — |
| hysteria salamander fallback ×3 | FIN/SWE/FRA | `17454-17456` | `hysteria-client-{fin,swe,fra}-salamander.service` | ✅ все живы | — |
| epilepsy transport | FRA `:5432` (PG-camouflage) | `127.0.0.1:17452` | `epilepsy-client.service` + `epilepsy-fra-forward.service` | ✅ up 8д, штатный реконнект-черн | — |

**Найденная историческая причина массового флаппинга:** 25.06 10:52–12:21 UTC все три основных туннеля (infra-tunnel, telegram-proxy-tunnel, remnawave-admin-tunnel) одновременно ушли в цикл переподключений (`Connection timed out` на все три цели разом) — похоже на общий сетевой сбой на стороне RU, а не проблему отдельных целей. Совпадает по времени с уже задокументированным инцидентом провайдера FIN/FRA/SWE 25.06. Все три восстановились и с тех пор стабильны (кроме текущего HY2-инцидента выше).

### Приём на стороне FIN/SWE/FRA/reserve

Все ожидаемые туннели подтверждены **с принимающей стороны** — соединение реально приходит с IP RU, локальные слушатели (tinyproxy на FIN `:8888`, infra-backend на FRA `:9082`, epilepsy-server на FRA `:5432`↔RU, HY2-серверы на FIN/SWE) активны. Postgres-камуфляжный epilepsy на FRA — реально установленное соединение подтверждено с обеих сторон.

**Левых/недокументированных туннелей не найдено** ни на одном из 4 серверов — единственное отклонение это уже известный `remnawave-admin-tunnel` (задокументирован в памяти проекта, но отсутствует в git-репо).

**Побочная находка:** исходящий IP RU для всех этих туннелей — `45.91.53.93`, а не `45.91.54.152` (документированный ingress IP). `45.91.53.93` — это уже известный "backup"-IP (Smart2 XHTTP relay) из `infrastructure-live-20260605.md`, так что это, вероятно, штатный сорс-роутинг, а не аномалия — но стоит явно задокументировать, что это ожидаемый egress-IP для межсерверных туннелей.

На **reserve** (31.77.169.26) обнаружен один нераспознанный пир `117.55.203.127:443` с большим объёмом xray-соединений — по паттерну похоже на легитимный апстрим Hydra (whitestore), не в списке известных IP кластера, но не похоже на вторжение. Рекомендация: занести в память как известный Hydra-апстрим после подтверждения.

---

## 3. RU: сертификаты, процессы, диск/память

- **TLS**: все 6 vhost-сертификатов (`ru`, `web`, `admin`, `anal`, `diploma.goida.fun`, `academcheck.ru`) валидны, ближайший истекает через 44 дня (web.goida.fun). `reserve.goida.fun` сертификат на этом хосте не найден (обслуживается отдельным сервером — ожидаемо).
- **`systemctl --failed`**: 0 юнитов. Чисто.
- **Docker**: `remnawave`/`remnawave-db`/`remnawave-redis` healthy, 0 рестартов. `remnanode` — аптайм 11ч, `StartedAt=2026-07-01T05:00:02Z`, RestartCount=0 — это и есть известный ежедневный cron-рестарт (уже задокументирован в этом диалоге), не креш-луп.
- **Ошибки ботов за 7 дней**: `goida-client-bot.service` — 3 изолированных инцидента (25.06, 29.06×2), все давно самовосстановились. `vpn-bot.service` — см. 🔴 активный инцидент выше.
- **Диск**: 77% (`/dev/vda2`, 4.4G свободно) — не критично, но стоит держать в поле зрения.
- **Память**: здорова, swap используется умеренно.

---

## 4. DNS / failover

Все 8 проверенных `*.goida.fun` доменов резолвятся ровно так, как задокументировано — расхождений нет. `ru.goida.fun` сейчас на **PRIMARY** IP (`45.91.54.152`), фейловера на backup нет. ip-watchdog фактически крутится на **home**-сервере (78.107.88.21), не на RU — это не проверялось в рамках этого прогона (не было в одобренном скоупе), но текущее DNS-состояние однозначно подтверждает primary без необходимости лезть на home.

---

## 5. Hydra/WL sync + Lekanta

- `sub-updater.timer` — идеально по расписанию: 144 запуска за 3 дня (ровно 72ч × 2/ч), последний успешный run 15:49:44 UTC, без единой ошибки в логах.
- Транзиентная деградация Hydra WL-эндпоинтов (`nl`/`tur`) с 22:49 30.06 по 03:19 01.07 (~4.5ч), сама восстановилась, сейчас чисто.
- `hydra_fail_counts.json` — файла физически нет на диске (`/opt/sub-updater/`) вопреки упоминанию в памяти проекта — либо скрипт больше его не пишет, либо путь/имя изменились. Стоит поправить память.
- **Lekanta (84.252.100.158) — недоступна по SSH** с этой машины: TCP до 22-го порта открыт, но SSH обрывает сразу после обмена версией (похоже на fail2ban/allowlist на их стороне) — это ограничение доступа, не обязательно проблема самого сервера.

---

## Phase 2 — Чистка устройств bozhenkas (выполнено)

Бэкап `bot.db` снят перед мутацией: `/root/deploy-backups/20260701162700-bozhenkas-device-wipe/bot.db.bak` (RU).

- `bot.db` (`user_devices`/`user_ips`, токен `ZBiA1p6M...`) — уже был пуст (0 строк) на момент аудита; локальный legacy device-трекинг для bozhenkas ничего не хранил.
- Реальный источник «кучи тестов» — Remnawave `hwid_user_devices` (uuid `e9e53187-ed16-4763-94d0-956fdc584f46`): **13 устройств**, из них 11 явно тестовые/deploy-артефакты (`shadow-smoke`, `shadow-analyzer-check`, `analyzer-deploy-smoke`, `pentest-lite-restore-smoke`, `lite-check`/`lite-check-2`/`lite-check-final`, `rollback-check`, `post-hy2`, `shadow-hy2`, старый `2605221402666` от Happ/1.0), 2 похожи на реальные активные устройства (macOS «Mac» Happ 4.8.3, iPhone 15 Pro Happ 4.13.0, оба обновлялись в течение последних 2 часов на момент чистки).
- Выполнен полный вайп (по решению владельца) — `DELETE FROM hwid_user_devices WHERE user_uuid=...` → 13 строк удалено, подтверждено `count=0`. `hwid_device_limit`/`device_limit` не тронуты. Реальные устройства перерегистрируются автоматически при следующем подключении.

## Точечные находки для памяти проекта

1. `remnawave-admin-tunnel.service` (RU→SWE, порт `31800`) существует в проде, отсутствует в `deploy/systemd/` репозитория — стоит добавить файл в репо или явно занести в память как «живёт только на сервере».
2. `telegram-proxy-tunnel.service` имеет untracked resource-limits drop-in на RU, отсутствующий в репо.
3. `vpn-bot.service` с 28.06 канареечно переключён на HY2-путь (`18888`, `hysteria-client-fin`) для Telegram, `goida-client-bot.service` остался на старом SSH-туннеле (`8888`) — сейчас это разошлось: HY2-путь деградировал, SSH-путь жив.
4. Egress-IP RU для межсерверных туннелей — `45.91.53.93`, не `45.91.54.152`.
5. `hydra_fail_counts.json` больше не существует на диске — упоминание в памяти устарело.
