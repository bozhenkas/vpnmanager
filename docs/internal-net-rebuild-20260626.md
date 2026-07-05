# Внутренняя сеть RU→FIN/SWE/FRA — пересборка на новый стек (2026-06-26)

> Живой ченжлог/runbook. Цель — чтобы при обрыве сессии другой ИИ-агент продолжил.
> Прод-мутации только с backup. Hydra НЕ ТРОГАЕМ ВООБЩЕ.

## Задача (владелец 2026-06-26)
1. Снести (с backup) старую сетевую логику remnanode RU→fin/swe (НЕ hydra). FRA изначально планировалась как фейк-инбаунд, но позже владелец уточнил: FRA чинить как `epilepsy` primary + HY2 fallback.
2. Новый стек на базе remnawave: **СТРОГО xHTTP REALITY, ВНЕШНЕ ТОЛЬКО :443** (ступень-1), fake-SNI = реалистичный домен страны/провайдера VPS.
3. Фолбэк: **hysteria2 + salamander, ВНЕШНЕ :8443/udp**. Если :8443 нельзя безопасно мигрировать из-за текущего reserve/legacy-пути, допускается отдельный внешний **:7443/udp** для нового fallback. Максимально remnawave-native; если HY2 не получается внутри xray/remnawave, fallback = отдельный hysteria2-туннель + локальный socks5 на RU.
4. Подписка: **Финляндия + Швеция** снова, на новых протоколах. **Франция** после уточнения владельца — рабочий профиль, но не участник `Оптимальный`.
5. После поднятия — реальные xray shadow-тесты до стабильного соединения.
6. **JSON-routing сохранить без регрессий:** русский трафик только direct с клиента; если RU-трафик дошёл до серверной стороны (reserve/exit), он должен блокироваться, чтобы не палить IP серверов русским сервисам. Telegram НЕ direct.

## Текущий фокус (уточнение владельца 2026-06-26)
- Сейчас отлаживаем **сам транспорт RU→FIN/SWE/FRA**, не полный анализатор.
- Ручка переключения нужна как контракт для будущего анализатора: POST должен уметь переключать link `fin|swe` между `xhttp-reality` и `hy2-fallback`, link `fra` между `epilepsy` и `hy2-fallback`, а при полном DOWN — отключать выход/держать его в repair-состоянии до восстановления.
- `Оптимальный` должен вернуться как Happ JSON-балансировщик между Финляндией/Швецией (и другими разрешёнными живыми выходами), но только после real forwarding-smoke.
- Транспортные smoke уже доказаны; следующий рискованный шаг — prod switch в backend `remnawave` (меняет target у `REMNA_FI/SWE/FRA` внутри `ru-ws-ingress`). Hydra не менять.

## Критерии готовности для пользователя (уточнение владельца)
В подписке после cutover должны вернуться:
- **`Оптимальный`** — локальный Happ JSON-балансировщик на базе нашего routing engine; YouTube/Discord идут через `zapret`, русский трафик остаётся direct с клиента, Telegram не direct.
- **`Финляндия`** — как раньше для клиента: WS/TLS через RU `/fin`; внутри RU может переключаться inter-node transport `xhttp-reality` ↔ `hy2-fallback`, но клиент этого не видит.
- **`Швеция`** — как раньше для клиента: WS/TLS через RU `/swe`; внутри RU может переключаться inter-node transport.
- **`Франция`** — рабочий профиль WS/TLS через RU `/fra`; внутри RU primary = `epilepsy` (PostgreSQL-camouflage ports), fallback = `hysteria2+salamander`. В `Оптимальный` не добавлять.
- **`Оптимальный Лайт`** — сейчас broke, убрать из подписки; отдельная задача на починку.

Обязательный инвариант: **сохранить текущий JSON-routing**. Русские сайты/сервисы не должны видеть IP RU/FIN/SWE/reserve/home: RU-домены/geoip идут напрямую с клиента; если русский трафик всё же дошёл до серверной стороны, серверная страховка должна блокировать его, а не выпускать через exit.

## Декомпозиция текущей работы
1. **Transport-smoke:** доказать L7 forwarding для RU→FIN/RU→SWE по `xhttp-reality :443` и по HY2 fallback (`8443/udp` или новый `7443/udp`); для RU→FRA — `epilepsy` primary и HY2 fallback. Артефакт = команды + результат `204/exit-IP`, без рестарта prod-сервисов.
2. **HY2 fallback build:** если текущий `8443/udp` нельзя менять из-за reserve/legacy, поднять отдельный salamander-инстанс на `7443/udp`; на RU — отдельный hysteria2-client + локальный socks5 или xray-compatible локальный outbound. Артефакт = backup path, unit/config paths, rollback.
3. **Switch-actuator:** POST-ручка для будущего анализатора: `fin|swe` → `xhttp-reality` / `hy2-fallback` / `repair-off`; `fra` → `epilepsy` / `hy2-fallback` / `repair-off`. Артефакт = локальный код + тесты; prod deploy только после smoke.
4. **Subscription restore:** вернуть `Оптимальный` JSON-balancer + `Финляндия`/`Швеция` WS/TLS через RU; `Франция` = рабочий standalone-профиль через RU `/fra`, не в `Оптимальный`; `Оптимальный Лайт` не выдавать, пока не починен. Если страна падает, repair-dummy remark format = `🇭🇰 ⚠️<flags> НА РЕМОНТЕ⚠️`. Артефакт = exact subscription body + Xray/Happ-equivalent smoke.
5. **No-leak verification:** русские домены/IP идут direct с клиента; серверная сторона RU→block не выпускает RU-трафик через exit/reserve. Артефакт = access-log/curl proof.
6. **Security gate:** перед промоутом закрыть/записать BLOCKER/WARN: public listeners, HY2 ACL, `:58443`, config perms, dummy inbound safety.

## Security gate перед промоутом switch/analyzer (subagent check 2026-06-26)
Prod switch/analyzer как новая механика сейчас **заблокирован hardening/cutover-gate**, shadow-отладка транспорта разрешена.
- BLOCKER/WARN: public listeners не соответствуют whitelist. RU публично видит `:22`, `:17904`, `:30080`, `:58443`, backup `:7443`, `:8443/udp`; FIN — много legacy/dev портов (`:27017`, `:5432/5433`, `:1337`, `:5173`, etc.); SWE — `:443`, `:8443/udp`, `:17904`, `:58443`.
- BLOCKER/WARN: HY2 UDP ACL/hardening не доведены до целевого состояния — новый FIN `7443/udp` имеет RU-only allow, но старые широкие UDP-правила остаются; SWE UFW inactive.
- RESOLVED for transport-smoke: HY2 fallback salamander поднят отдельными инстансами (`FIN/SWE :7443/udp`, `FRA :8443/udp`).
- BLOCKER: секретные конфиги world-readable (`644`) на FIN/SWE/reserve; перед промоутом привести к `600/640`.
- RESOLVED for transport-smoke: real forwarding proof есть для FIN/SWE/FRA primary и HY2 fallback (`204/exit-IP`), open port/active unit не считались достаточными.
- RESOLVED for subscription restore: `FOREIGN_EXITS_DOWN=0`, `SMART_OVER_HYDRA=0`, `FRA_EXIT_DOWN=0`; `Оптимальный Лайт` убран как broke.
- BLOCKER/WARN: no-leak нужно доказать live-конфигом Remnawave/server-side RU→BLOCK + exact JSON subscription, не только локальными docs.

## Архитектурное решение (Remnawave-centric)
- Remnawave остаётся control plane: пользователи, подписка, inbounds `GOIDA_FIN/SWE/FRA`, JSON-routing и outbound tags `REMNA_FI/SWE/FRA` живут в `ru-ws-ingress`.
- Switch-ручка в целевом backend `remnawave` меняет только target у соответствующего `REMNA_*` outbound в Remnawave profile: `address/port` primary ↔ fallback. Перед каждым apply создаётся `config_profiles_bak_*`, после apply — `docker restart remnanode`.
- **Цепочка FIN/SWE**: `client → nginx /fin|/swe → remnanode GOIDA_FIN/SWE → REMNA_FI/SWE → [primary: xhttp-reality → exit:443] | [fallback: xhttp-reality → 127.0.0.1:17455/17456 → hysteria-client → exit:7443/udp(salamander) → exit:443]`.
- **Цепочка FRA**: `client → nginx /fra → remnanode GOIDA_FRA → REMNA_FRA → [primary: epilepsy → 127.0.0.1:17452 → FRA:443] | [fallback: reality-over-HY2 → 127.0.0.1:17454 → FRA:8443/udp(salamander) → FRA:443]`.
- Experimental `xray-intermesh` localhost `18001/18002/18003` был собран и проверен как dev/rollback path, но **не выбран целевым** после напоминания владельца про Remnawave-centric.

## Порты / алиасы
| что | RU | FIN (77.110.108.57) | SWE (89.22.230.5) | FRA (95.163.152.210) |
|---|---|---|---|---|
| ступень-1 | Remnawave `REMNA_*` target → exit:443 or local primary | `xray-goida-reality` :443 | `xray-goida-reality` :443 | `epilepsy` via RU `127.0.0.1:17452` → FRA:443 |
| HY2 salamander | RU clients `17455`(fin)/`17456`(swe)/`17454`(fra) | `hysteria-fin-salamander` :7443/udp | `hysteria-swe-salamander` :7443/udp | `hysteria-fra-salamander` :8443/udp |
| switch | `goida-intermesh-switch` :9099 | — | — | — |

## Шаги (чекбоксы — обновлять по ходу)
- [x] S0 recon: reality-параметры FIN/SWE, hysteria-server config (salamander/auth/egress), RU hysteria-clients (не сломать reserve), remnanode ru-ws-ingress (outbounds/routing/inbounds/squads/balancer), listener/firewall hygiene. Проверить, что reality RU→FIN/SWE:443 РЕАЛЬНО форвардит (exit-IP).
- [x] S1 HY2 fallback: выбрать порт (`8443/udp` если можно мигрировать безопасно, иначе `7443/udp`), поднять/проверить salamander; если remnawave/xray не умеет нужный HY2-клиент — отдельный hysteria2-client на RU + локальный xray-compatible tcpForwarding; shadow xray-through-HY2 → exit FIN/SWE/FRA.
- [x] S2 switch.py локально адаптирован под Remnawave-centric backend: `/switch` меняет `REMNA_*` target в `ru-ws-ingress`, создаёт DB backup и рестартит `remnanode`; tests green.
- [x] S3 prod deploy switch backend `remnawave`; /switch flip FRA `epilepsy → hy2-fallback → epilepsy` с backup + live subscription shadow.
- [ ] S4 analyzer: RU + mac-home-exit probes, вызывает switch POST для `stage`/`repair`/`disabled`. Hydra не трогать.
- [x] S5 подписка: Финляндия/Швеция назад (revert incident-флагов ТОЛЬКО для fin/swe, hydra оставить); Франция восстановлена как рабочий профиль, но не включена в `Оптимальный`.
- [x] S6 shadow-тесты полной подписки до стабильного; correlated RU+exit логи; отдельно проверить acceptance: `Оптимальный` JSON-balancer содержит fin/swe и zapret-правила для YouTube/Discord; `Финляндия`/`Швеция`/`Франция` = WS/TLS через RU; RU-direct: русские домены идут direct с клиента, серверный RU→BLOCK не даёт палить exit/reserve IP.
- [ ] S7 changelog/memory финал.

## Состояние / backups (заполняется по ходу)
- Прод мутирован по явному запросу владельца, с backup перед каждым изменением.
- xHTTP Reality target fix:
  - SWE `/root/deploy-backups/20260626T013450Z-xhttp-reality-target-fix/goida-reality.json.bak`
  - FIN `/root/deploy-backups/20260626T013633Z-xhttp-reality-target-fix/goida-reality.json.bak`
  - RU Remnawave profile `/root/deploy-backups/20260626T014047Z-remna-xhttp-target-fix/`
- Subscription restore FIN/SWE + France dummy: `/root/deploy-backups/20260626T014619Z-subscription-restore-fin-swe/`
- Server-side RU→BLOCK safety: `/root/deploy-backups/20260626T015205Z-server-ru-block-safety/`
- Remove broken `Оптимальный Лайт` + repair remark format: `/root/deploy-backups/20260626T015705Z-subscription-lite-out-repair-remark/`
- Remove `Оптимальный Лайт` from user/admin notices: `/root/deploy-backups/20260626T020010Z-remove-lite-user-notices/`
- Restore Hydra issuance/permissions to pre-outage logic: `/root/deploy-backups/20260626T020650Z-hydra-squad-restore-preoutage/`
- FRA Reality target fix: `/root/deploy-backups/20260626T165200Z-fra-reality-target-fix/`
- Restore real `Франция 🇫🇷` in subscription: `/root/deploy-backups/20260626T165310Z-fra-subscription-restore/`
- FRA HY2+salamander fallback server: `/root/deploy-backups/20260626T170000Z-fra-hy2-salamander/`
- RU HY2 FRA client: `/root/deploy-backups/20260626T170000Z-ru-hy2-fra-client/`
- FIN HY2+salamander fallback server: `/root/deploy-backups/20260626T171200Z-fin-hy2-salamander/`
- SWE HY2+salamander fallback server: `/root/deploy-backups/20260626T171200Z-swe-hy2-salamander/`
- RU HY2 FIN/SWE clients: `/root/deploy-backups/20260626T171200Z-ru-hy2-fin-swe-clients/`
- Remnawave switch deploy/staging backup: `/root/deploy-backups/20260626T190322Z-intermesh-deploy/`
- FRA Remnawave switch-smoke backup file: `/root/deploy-backups/20260626T191343Z-remnawave-switch-fra-smoke/ru-ws-ingress.before.json`
- FRA switch backup tables: `config_profiles_bak_20260626_191500_intermesh_fra_active_hy2_fa`, `config_profiles_bak_20260626_191507_intermesh_fra_active_epilep`
- Native Smart Lite filter deploy backup: `/root/deploy-backups/20260626T191343Z-remnawave-switch-fra-smoke/vpn-bot.before-native-lite-filter.py`
- Pentest custom_sub pre-check backup: `/root/deploy-backups/20260626T191343Z-remnawave-switch-fra-smoke/pentest-custom-sub-before-lite-removal.txt`
- Чистка nomad/x-ui/epilepsy — ранее, см. facts.md 2026-06-26.

## S0 RECON — РЕЗУЛЬТАТЫ (2026-06-26)
### подтверждено
- **HY2-инфра для фолбэка УЖЕ ЕСТЬ:** RU `hysteria-client-fin`(`127.0.0.1:17450`→`FIN:443`) + `hysteria-client-swe`(`17451`→`SWE:443`), active, «REALITY over UDP» (tcpForwarding). Отдельные от reserve (у reserve свой бокс). socks5 НЕ нужен — фолбэк = reality-outbound дозванивается в локальный HY2-порт. Оба стейджа reality end-to-end.
- **FIN/SWE hysteria-server** `:8443`, TLS letsencrypt, auth password, `masquerade: type proxy url=https://web.max.ru`. **`obfs: salamander` ОТСУТСТВУЕТ** (только masquerade). Добавить salamander на общий `:8443` сломает reserve → решать отдельно (выделенный salamander-инстанс на новом UDP-порту ИЛИ координированная миграция reserve). Владелец требовал salamander СТРОГО — это открытый форк.
- **reality-параметры совпадают** RU REMNA_FI/SWE outbound ↔ FIN standalone inbound: net=xhttp mode auto path `/`, sni `www.microsoft.com`, shortId `d43bdc4fff21882f`, flow none, uuid tail `…d9912a`, fp chrome. То есть конфиги корректны.
- **Read-only listener/firewall check (2026-06-26):** RU: `hysteria-client-fin/swe` слушают `127.0.0.1:17450/17451`; `epilepsy-fra-forward` жив на `17452`; UFW inactive; публично видны `:22`, `:17904`, `:30080`, `:58443`, `:7443`, `:8443`. FIN: `xray-goida-reality :443`, `hysteria-server :8443/udp`, но также публичные legacy/dev порты и `goida-ticket-web :8443/tcp`; `/etc/xray/goida-reality.json` и `/etc/hysteria/config.yaml` mode `644`. SWE: `xray-goida-reality :443`, `hysteria-server :8443/udp`, `rw-core` тоже слушает `:443`, `:58443` public, UFW inactive, configs `644`. Это не блокирует shadow-отладку, но является security-gate перед промоутом.

### ⚠️ КРИТИЧЕСКАЯ НАХОДКА — стейдж-1 (прямой reality :443) НЕ ПРОХОДИТ
- Shadow-тест (temp xray 26.3.27, socks→reality-outbound) RU→FIN:443 **xhttp reality DIRECT**: TLS-коннект есть, но `transport/internet/splithttp: XHTTP is dialing to tcp:77.110.108.57:443, mode stream-one, HTTP2, host www.microsoft.com → failed to POST https://www.microsoft.com/ : EOF`. То есть **L7 payload по-прежнему режется ТСПУ** на прямом RU-ДЦ→FIN:443, несмотря на `400` reachability (400 = это masq/reject, не рабочий путь). Раньше REMNA_FI работал ТОЛЬКО через epilepsy-TUN, не напрямую — вот почему epilepsy и поднимали.
- Стейдж-2 (reality over HY2 `17450/17451`) в этом прогоне ТОЖЕ дал FAIL — но причина не дочинена (возможно: короткий sleep 2с до первого запроса по HY2, либо HY2-клиент сейчас не качает payload). **НАДО перепроверить HY2-путь отдельно, дольше, с debug.**
- Повтор 2026-06-26 01:29Z, temp xray inside `remnanode`, SOCKS inbound, live outbound params, без мутаций prod: `fin-direct 77.110.108.57:443`, `fin-hy2-existing 127.0.0.1:17450`, `swe-direct 89.22.230.5:443`, `swe-hy2-existing 127.0.0.1:17451` — все FAIL; лог каждого: `XHTTP is dialing ... host www.microsoft.com` → `failed to POST https://www.microsoft.com/ : EOF`. `api.ipify.org`/`cp.cloudflare.com/generate_204` не дали exit-IP/204. Журналы `xray-goida-reality`/`hysteria-server` на FIN/SWE за окно теста пустые. Вывод: текущая xHTTP Reality связка с fake-SNI `www.microsoft.com` не доказана ни direct, ни через существующий HY2 tcpForward.
- **Вывод:** премиса «xhttp reality direct :443» как стейдж-1 под вопросом — ТСПУ морозит прямой L7. Возможные ходы для след. сессии: (а) проверить, проходит ли стейдж-2 HY2 (если да — основным делать HY2, reality-direct как опт. стейдж-1 когда ТСПУ отпустит); (б) вернуть epilepsy как рабочий маскирующий транспорт для FIN (он реально нёс payload); (в) замерить xHTTP vs Vision/разные fake-SNI; (г) проверить, не мешает ли конкретно `www.microsoft.com` (reject EOF может быть от самого www.microsoft.com анти-бота, а не ТСПУ — проверить другой dest/SNI и сравнить с epilepsy-путём, который работал с теми же параметрами).

### FIX 2026-06-26 — root-cause `www.microsoft.com` как REALITY target
- Поиск по Xray upstream: `XTLS/Xray-core#6356` описывает аналогичный xHTTP/REALITY fail с `www.microsoft.com`: TLS Certificate record ~8247 bytes > 8192. У нас совпало 1:1: `failed to POST https://www.microsoft.com/ : EOF`. Замер `openssl s_client -msg`: `www.microsoft.com` Certificate length `0x2037`; `www.suomi.fi` `0x0ece`, `www.government.se` `0x1266`.
- Staged fix:
  - SWE backup `/root/deploy-backups/20260626T013450Z-xhttp-reality-target-fix/goida-reality.json.bak`; `/etc/xray/goida-reality.json` target/serverNames → `www.government.se`; `xray run -test` OK; `xray-goida-reality` restarted.
  - FIN backup `/root/deploy-backups/20260626T013633Z-xhttp-reality-target-fix/goida-reality.json.bak`; target/serverNames → `www.suomi.fi`; `xray run -test` OK; `xray-goida-reality` restarted.
  - RU Remnawave profile backup `/root/deploy-backups/20260626T014047Z-remna-xhttp-target-fix/` + DB table `config_profiles_bak_20260626T014047Z_xhttp_target_fix`; `REMNA_FI` → `77.110.108.57:443` + `www.suomi.fi`; `REMNA_SWE` → `89.22.230.5:443` + `www.government.se`; `docker restart remnanode`.
- Verified generated remnanode config: `REMNA_FI 77.110.108.57 443 www.suomi.fi xhttp`; `REMNA_SWE 89.22.230.5 443 www.government.se xhttp`.
- Shadow-smoke inside `remnanode`, temp xray SOCKS inbound, live-generated outbounds:
  - FIN: `ifconfig.me/ip=77.110.108.57`, `icanhazip.com=77.110.108.57`, `cp.cloudflare.com/generate_204=204`.
  - SWE: initial cf204 resets, then stable after retries: `ifconfig.me/ip=89.22.230.5`, `cp.cloudflare.com/generate_204=204`; xray-goida-reality logs accepted `REMNA_XHTTP_REALITY >> DIRECT`.
- Status: **stage1 xHTTP Reality direct :443 поднят для FIN/SWE**. HY2 fallback не закрыт: existing FIN HY2 tcpForward works with new SNI; existing SWE HY2 tcpForward still timeouts; salamander absent.

### СЛЕДУЮЩИЙ ШАГ (resume отсюда)
1. Деплоить `goida-intermesh-switch.service` в `SWITCH_BACKEND=remnawave`; проверить один flip FIN/SWE/FRA с backup table и live subscription smoke.
2. `Оптимальный Лайт` сейчас broke и убран из подписки — отдельная задача на починку/переосмысление.
3. После switch-deploy писать analyzer: RU + mac-home-exit как компенсирующий стек probes; POST в switch меняет stage или переводит link в `repair/off`.

### FRA FIX 2026-06-26 — primary epilepsy восстановлен
- Симптом: прямой shadow через `REMNA_FRA` и клиентский `/fra` давали `EOF`/`SSL_ERROR_SYSCALL`, хотя RU `epilepsy-client` и `epilepsy-fra-forward` были active.
- Диагноз: epilepsy не виноват; direct `95.163.152.210:443` с тем же outbound тоже падал. Live FRA rw-core config имел 30 клиентов, но Reality target был `www.microsoft.com`.
- Fix: `fra-reality-tcp-443` target/serverNames → `www.gouvernement.fr`; `ru-ws-ingress.REMNA_FRA.realitySettings.serverName` → `www.gouvernement.fr`; restart FRA/RU `remnanode`.
- Verified:
  - direct `REMNA_FRA` shadow: `cp.cloudflare.com/generate_204=204`, `api.ipify.org=95.163.152.210`.
  - live subscription now contains `Франция 🇫🇷`, no repair dummy; France profile smoke: `204`, exit `95.163.152.210`.
  - `Оптимальный` still selector `[proxy-fin, proxy-swe]` and does not include FRA.
- Switch contract updated locally: stage `epilepsy` added; `links.example.json` includes `fra` with `out-fra-epilepsy` and `out-fra-hy2-fallback`; tests green.

### FRA HY2 FALLBACK 2026-06-26 — salamander поднят
- FRA: installed/copied Hysteria2 v2.7.1; `hysteria-fra-salamander.service` listens `:8443/udp`.
- RU: `hysteria-client-fra.service` listens `127.0.0.1:17454`, `tcpForwarding` to FRA `127.0.0.1:443`.
- UFW FRA allows `8443/udp` only from RU `45.91.54.152` and RU alt `45.91.53.93`.
- Hysteria2 server syntax note: string `auth: <secret>` fails on v2.7.1 server; must be:
  `auth: {type: password, password: ...}`. Client string `auth: ...` works.
- Verified fallback smoke: temp Xray `REMNA_FRA` with address `127.0.0.1:17454` → `cp.cloudflare.com/generate_204=204`, `api.ipify.org=95.163.152.210`.

### FIN/SWE HY2 FALLBACK 2026-06-26 — salamander поднят
- Чтобы не ломать существующий non-salamander `:8443/udp`, новые fallback-инстансы подняты на внешнем `:7443/udp`.
- FIN: `hysteria-fin-salamander.service` listens `:7443/udp`; RU `hysteria-client-fin-salamander.service` listens `127.0.0.1:17455`, `tcpForwarding` to FIN `127.0.0.1:443`.
- SWE: `hysteria-swe-salamander.service` listens `:7443/udp`; RU `hysteria-client-swe-salamander.service` listens `127.0.0.1:17456`, `tcpForwarding` to SWE `127.0.0.1:443`.
- Verified fallback smoke:
  - temp Xray `REMNA_FI` with address `127.0.0.1:17455` → `cp.cloudflare.com/generate_204=204`, `api.ipify.org=77.110.108.57`.
  - temp Xray `REMNA_SWE` with address `127.0.0.1:17456` → `cp.cloudflare.com/generate_204=204`, `api.ipify.org=89.22.230.5`.
- Hardening remains: FIN has RU-only `7443/udp` allow but legacy broad UDP rules remain; SWE UFW inactive.

### SWITCH PIVOT 2026-06-26 — Remnawave-centric
- Владелец напомнил: всё должно быть Remnawave-centric. Поэтому экспериментальный `xray-intermesh` (`18001/18002/18003`) **не выбран как целевой cutover**.
- Что сделано на RU: staging `xray-intermesh` был поднят, smoke показал primary/fallback paths (SWE fallback retest 3/3 `204` + `89.22.230.5`), затем staging services выключены/disabled; live Remnawave profile НЕ переключался.
- Код `intermesh/switch.py` расширен backend `remnawave`: читает live `ru-ws-ingress`, меняет target у `REMNA_FI/SWE/FRA`, создаёт `config_profiles_bak_*`, рестартит `remnanode`, хранит state; tests green.
- `goida-intermesh-switch.service` задеплоен и active на RU, bind `127.0.0.1:9099`, backend `remnawave`.
- FRA flip-smoke: `/switch fra hy2-fallback` создал `config_profiles_bak_20260626_191500_intermesh_fra_active_hy2_fa`; `/switch fra epilepsy` создал `config_profiles_bak_20260626_191507_intermesh_fra_active_epilep`; live `/fra` после rollback primary: `cp.cloudflare.com/generate_204=204`, `api.ipify.org=95.163.152.210`.
- Live Remnawave current: `REMNA_FI=77.110.108.57:443 xhttp reality`, `REMNA_SWE=89.22.230.5:443 xhttp reality`, `REMNA_FRA=127.0.0.1:17452 tcp reality`.

### SMART LITE FILTER 2026-06-26
- `Оптимальный Лайт` дополнительно вырезан из Remnawave native subscription proxy response (`vpn-bot.py strip_broken_smart_lite_from_subscription`), потому что Remnawave native `/json` всё ещё отдавал старый `GOIDA_SMART_LITE` для `pentest`.
- Verified live: `pentest`, `test-n`, `bozhenkas` subscriptions have no `Оптимальный Лайт`; `Франция 🇫🇷` remains live and smokes `204` + `95.163.152.210`.

### SMART LITE PENTEST EXCEPTION 2026-06-27
- Owner requested `Оптимальный Лайт` back for diagnostic native user `pentest` only.
- Local code: `strip_broken_smart_lite_from_subscription(..., allow_smart_lite=True)` bypasses the native filter for Remnawave username `pentest`; all other users stay filtered until Lite is repaired.
- Deployed on RU with backup `/root/deploy-backups/20260627T002800Z-pentest-lite-restore/vpn-bot.before-pentest-lite-restore.py`; verified live Happ JSON: `pentest` has `Оптимальный Лайт`, `test-n` and `bozhenkas` do not.

### ANALYZER DRY-RUN 2026-06-27
- `intermesh/analyzer.py` added: stdlib decision maker for `fin/swe/fra` primary↔HY2 fallback; 3 confirm attempts with 25s delay + jitter; uses switch `/status` and `/switch`, and bot `/analyzer` for `foreign_exits_down`.
- `vpn-bot.py` now has localhost/server-only POST `/analyzer` with `NOTIFY_TOKEN`; it can set runtime `analyzer_foreign_exits_down` / `analyzer_fra_exit_down` in `bot_settings`, so analyzer can give hydra to everyone without restarting `vpn-bot`.
- Deployed on RU in dry-run only (`ANALYZER_APPLY=0`) with timer every 5 min. Backup `/root/deploy-backups/20260627T003300Z-intermesh-analyzer-dryrun/`; first systemd run success after adding `StateDirectory=goida-intermesh-analyzer`.
- Current dry-run probes are **weak TCP only** because `/etc/goida-intermesh/links.json` has no `smoke[stage].cmd` yet. Result: FIN/SWE/FRA primary+fallback TCP OK, action `noop`, dry-run bot action `foreign_exits_down=false`.
- Before `ANALYZER_APPLY=1`: add real Xray/Happ-equivalent smoke commands per stage (`cp.cloudflare.com/generate_204` + expected exit IP), then fake-block primary/fallback/all-foreign and verify switch + subscription behavior.
- Correction: earlier comparison mixed up local `LadonGo PortScan` with `belotserkovtsev/Ladon` (Reactive Anti-DPI Engine). The latter is a real DPI observer candidate: DNS→TCP→TLS→HTTP staged probes, typed failure codes (`tls13_block`, `http_cutoff`, etc.), temporal accumulation, optional exit-compare. Use it for DPI/path classification on HOME/RU; still require transport-specific Xray/Happ smoke before switch decisions because Ladon does not prove VLESS/xHTTP/HY2 application path by itself.

### LADON OBSERVER 2026-06-27
- Installed `belotserkovtsev/Ladon` v1.4.1 manually on RU and `mac-home-exit` as observer CLI only: `/opt/ladon/ladon`, config `/etc/ladon/config.yaml`, DB `/opt/ladon/state/engine.db`.
- Services are deliberately disabled/inactive on both hosts; no iptables/ip rules/routing changes, and no dnsmasq start/restart. This avoids opening/changing DNS before we wire the analyzer.
- Backups: RU `/root/deploy-backups/20260627T004000Z-ladon-observer/`; HOME `/root/deploy-backups/20260627T004000Z-ladon-observer/` (empty because no previous Ladon files existed).
- Manual probe smoke:
  - RU: `ru.goida.fun`, `youtube.com`, `discord.com`, `fin.goida.fun` OK; `api.telegram.org` → `tcp_timeout`.
  - HOME: `ru.goida.fun`, `fin.goida.fun` OK; `youtube.com`/`discord.com` → `tls_handshake_timeout`; `api.telegram.org` → `tcp_timeout`.
- Next: analyzer should call local RU Ladon + HOME Ladon over SSH/agent endpoint for DPI/path classification, then require transport-specific Xray smoke before switch.

### LADON SIGNATURE ANALYZER LOCAL 2026-06-27
- `intermesh/analyzer.py` rewritten locally: runs Ladon CLI matrix from RU and HOME (`ssh bozhenkas@78.107.88.21 -p 1722`), normalizes signature failure codes (`tcp_timeout`, `tls_handshake_timeout`, `tls_reset`, `tls_garbage`, `tls13_block`, `http_cutoff`, `http_451`, plus close aliases), and writes state with `vantage_results`, `transport_smokes`, and human `decision_reason`.
- Decision guard is now: primary smoke OK → keep/restore primary; primary Ladon signature bad + primary smoke fail + fallback smoke OK → switch fallback; primary+fallback smoke fail after confirmation → `disabled`/repair fake inbound path; all foreign smoke fail → `/analyzer foreign_exits_down=true` (dry-run unless APPLY=1).
- Added `intermesh/signature-targets.example.json` with RU primary, RU backup, reserve, FIN, SWE, FRA, HOME observer. RU backup and HOME observer are deliberately `ladon=false`: Ladon CLI v1 cannot probe raw IP with custom SNI, so backup candidate safety remains in watchdog direct SNI probe.
- `ip-watchdog/watchdog.py` rewritten toward Ladon: HOME checks current `ru.goida.fun` through Ladon before DNS failover; DNS switches only between managed primary/backup, keeps manual override guard, and keeps direct SNI probe for backup precheck / primary recovery while DNS points at backup. `DNS_APPLY=0` exists for dry-run.
- Local verification: `py_compile` OK; watchdog direct unittest file 13/13 OK; analyzer local runner 9/9 OK; `test_smart_lite.py` 7/7 OK; `test_hydra_gating.py` 6/6 OK; `test_intermesh_switch.py` OK; `test_subscription_engine.py` 22/22 OK with `PYTHONPATH`. `pytest` package is absent in this local environment, so pytest command itself was not used.
- Still **not cut over**: deploy dry-run with backup, then live Ladon matrix + subscription checks + real per-stage smokes (`204 + expected exit IP`) before `ANALYZER_APPLY=1`.

### LADON SIGNATURE ANALYZER DRY-RUN DEPLOY 2026-06-27
- RU deployed in dry-run only (`ANALYZER_APPLY=0`) with backup `/root/deploy-backups/20260627T043300Z-ladon-signature-analyzer-dryrun/`.
- Added dedicated RU→HOME analyzer SSH key `/etc/goida-intermesh/ssh/home-ladon`; public key added to HOME `authorized_keys` with backup `/home/bozhenkas/deploy-backups/20260627T043300Z-ladon-signature-analyzer-dryrun/authorized_keys.bak`.
- Unit hardening adjusted: `ProtectHome=read-only` for SSH key read, `ReadWritePaths=/opt/ladon/state` for Ladon SQLite DB under `ProtectSystem=strict`. Ladon command uses `-db /opt/ladon/state/engine.db -config /etc/ladon/config.yaml`; HOME command uses `sudo -n`.
- RU dry-run state verified:
  - `vantage_results`: RU/HOME OK for `ru-primary`, `reserve`, `fin`, `swe`; `fra` returns `tls_eof` from both vantages (Reality/protocol mismatch, not in blocking signature set); `ru-backup` and `home-observer` are `SKIPPED` by design.
  - `transport_smokes`: currently weak TCP only for all stages (`weak=true`), so decisions are forced `noop` with reason `primary probe ok but weak; no switch decision without transport smoke`.
  - `/analyzer foreign_exits_down=false` remains dry-run; timer active, next runs every 5 min.
- HOME `ip-watchdog/watchdog.py` deployed with backup `/home/bozhenkas/deploy-backups/20260627T044000Z-ladon-watchdog-dryrun/watchdog.py.bak`. Service run verified: Cloudflare A `ru.goida.fun → 45.91.54.152`, reserve TCP OK, backup HY2 UDP-send OK, Ladon `ru.goida.fun` OK, no DNS switch.
- Live subscription composition verified from RU localhost for `bozhenkas`, `test-n`, `pentest`: `Оптимальный`, FIN, SWE, FRA present; repair dummy absent; `Оптимальный Лайт` only present for `pentest`.
- Gate still closed for `ANALYZER_APPLY=1`: add real per-stage smoke commands to `/etc/goida-intermesh/links.json` (FIN/SWE primary xHTTP Reality `204+exit`, FIN/SWE HY2 fallback `204+exit`, FRA epilepsy `204+95.163.152.210`, FRA HY2 fallback `204+95.163.152.210`), then fake-block tests.

## Как продолжить при обрыве
- Прочитать этот файл + `facts.md` секцию «2026-06-26 пересборка». Найти первый невыполненный шаг.
- Switch-код: `intermesh/switch.py` (+ units `deploy/systemd/goida-intermesh-switch.service`, `goida-stage@.service`).
- Hydra трогать НЕЛЬЗЯ. Любой шаг — backup → мутация → shadow-проверка реальным пробросом (exit-IP), не «active».
