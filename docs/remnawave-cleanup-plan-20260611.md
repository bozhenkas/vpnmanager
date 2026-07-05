# Remnawave cleanup plan — 2026-06-11

> READ-ONLY аудит живого RU `45.91.54.152` выполнен 2026-06-11 (этот запуск НИЧЕГО
> не мутировал). Цель документа — привести Remnawave к канонической логике
> `docs/routing-logic.md`, удалить мусор, не повторив инцидент 2026-06-05
> (прямой SQL-ренейм тегов → очистка node mappings).
>
> Каждая мутация — отдельной фазой с бэкап-командой ПЕРЕД ней. В этом запуске
> фазы НЕ выполнялись. Перед исполнением: один прод-актор на фазу + shadow-test
> из `docs/incident-20260605-...md` после каждого изменения роутинга/подписки.

---

## 0. Метод аудита

- SSH: `ssh -p 17904 -i ~/.ssh/id_rsa root@45.91.54.152` (доступен).
- DB: `docker exec -i remnawave-db psql -U postgres -d postgres -P pager=off` (heredoc).
- Все запросы read-only (SELECT / information_schema / pg_tables). Секреты не читались.

---

## 1. Текущее состояние

### 1.1 Аптаймы и здоровье (snapshot 2026-06-11 ~17:58 UTC)

| контейнер | started | health |
|---|---|---|
| `remnawave` | 2026-06-07 18:37:03Z (3 дня) | **healthy** |
| `remnawave-db` | 2026-06-07 18:37:03Z | **healthy** |
| `remnawave-redis` | 2026-06-07 18:37:03Z | **healthy** |
| `remnanode` | 2026-06-07 18:37:03Z | none (нет healthcheck), Xray 26.3.27 запущен, XTLS up |

Хост `ru.goida.fun`: uptime 3 дня 23ч, load 1.4. Все 4 контейнера подняты
одновременно (перезагрузка/ребут 2026-06-07). `sub-updater.service` — **active**
(running 3 дня, PID 1973).

**Backend-логи (`remnawave --tail`)** — здоров, но есть штатный шум:
- `StartNodeProcessor` периодически `EHOSTUNREACH 78.107.88.21:58443` (home-node
  mgmt-порт). На момент аудита прямой TCP-чек `78.107.88.21:58443` с RU прошёл
  (порт OPEN) → транзиентно/NAT-keepalive; нода в БД `is_connected=t`.
- Эпизодические `health check ... timeout of 15000ms` на `89.22.230.5` (swe) и
  `getUsersStats timeout` на `95.163.152.210` (fra) — совпадает с известной грабли
  «SWE/FI intermittent» (known-bugs §8). Не блокер, но риск для balancer.

### 1.2 Ноды (`nodes`) — НЕ пустая (важно: прошлый инвентарь ошибался)

| node | address | is_connected | is_disabled | profile |
|---|---|---|---|---|
| ru-smart-goida | 45.91.54.152 | t | f | да |
| fin-goida | 77.110.108.57 | t | f | да |
| fra-goida | 95.163.152.210 | t | f | да |
| swe-goida | 89.22.230.5 | t | f | да |
| home-goida | 78.107.88.21 | t | f | да |

Все 5 connected. **Это противоречит ожиданию «nodes пустая» из задания** — таблица
заполнена и является живым источником node→inbound маппинга.

### 1.3 Config profiles (4 живых) и их inbounds

| profile | inbound tag | port | net |
|---|---|---|---|
| `ru-ws-ingress` (`11111111-…-001`) | GOIDA_SMART | 17443 | ws |
| | GOIDA_RU | 17444 | ws |
| | GOIDA_FIN | 17445 | ws |
| | GOIDA_FRA | 17446 | ws |
| | GOIDA_SWE | 17447 | ws |
| | RU_WS_HOME | 17448 | ws |
| | GOIDA_RESERVE | 2053 | grpc |
| | GOIDA_SMART2 | 7443 | xhttp |
| | GOIDA_HYDRA_{NL,DE,POL,TUR} | 17460-64 | ws |
| `foreign-reality-tcp-443` (`98bc…`) | REMNA_VLESS_TCP_REALITY_7443 | 443 | raw |
| `fra-reality-tcp-443` (`c53e…`) | FRA_VLESS_TCP_REALITY_443 | 443 | raw |
| `home-exit-reality` (`2222…-001`) | HOME_VLESS_TCP_REALITY_7443 | 7443 | raw |

> NB: тег `REMNA_VLESS_TCP_REALITY_7443` фактически слушает на **443** (имя
> историческое, порт 443). Имя «7443» — наследие, переименовывать осторожно (§3).

### 1.4 Node → inbound маппинг (`config_profile_inbounds_to_nodes`)

| node | inbound |
|---|---|
| ru-smart-goida | все GOIDA_* + RU_WS_HOME (14 ws/grpc/xhttp инбаундов) |
| fin-goida | REMNA_VLESS_TCP_REALITY_7443 |
| swe-goida | REMNA_VLESS_TCP_REALITY_7443 |
| fra-goida | FRA_VLESS_TCP_REALITY_443 |
| home-goida | HOME_VLESS_TCP_REALITY_7443 |

**Ключевой факт:** fin и swe делят один foreign-reality инбаунд; **fra стоит на
отдельном** `FRA_VLESS_TCP_REALITY_443` (отдельный профиль). Это критично для
target-балансира `[fin, fra]` — fra уже полноценный выход (не только swe).

### 1.5 Internal squads (8, все с членами — НЕ пустые/Default)

| squad | members | inbounds |
|---|---|---|
| SMART_RU_REMNA | 27 | GOIDA_SMART, GOIDA_RU, GOIDA_RESERVE, GOIDA_SMART2 |
| SMART_REMNA | 27 | GOIDA_FIN, GOIDA_SWE, REMNA_VLESS_TCP_REALITY_7443 |
| FRA | 27 | GOIDA_FRA, FRA_VLESS_TCP_REALITY_443 |
| HOME_REMNA | 2 | RU_WS_HOME |
| HYDRA_{DE,NL,POL,TUR}_REMNA | 5 каждый | GOIDA_HYDRA_{…} |

`external_squads`: 0.

### 1.6 Users / HWID / hosts

- `users`: **27** (все ACTIVE). `user_traffic`: 24 строки (3 юзера без traffic-row
  — потенциально известная грабля #9, проверить отдельно при чистке юзеров).
- `hwid_user_devices`: **137** устройств у 26 юзеров; из них **16** синтетических
  (regex `CHECK|TEST|shadow|smoke|VK-ROUTE|BOOSTY`).
- `hosts` (live): **26 строк**, но это **6 логических хостов с дублями ×4**
  (см. §2.3) + FRA Reality 443 + Home Reality 7443 (is_hidden).

### 1.7 Routing внутри `ru-ws-ingress` (живой)

**Outbounds:** BLOCK, DIRECT, HYDRA_DE, HYDRA_NL, HYDRA_NL_2, HYDRA_NL_3,
HYDRA_POL, HYDRA_TUR, REMNA_FI, REMNA_FRA, REMNA_HOME, REMNA_SWE.

**Balancers:**
- `GOIDA_BALANCER_SMART` = `random[REMNA_FRA, REMNA_SWE, REMNA_FI]`, fallback `REMNA_FI`.
- `BALANCER_HYDRA_NL` = `roundRobin[HYDRA_NL, HYDRA_NL_2, HYDRA_NL_3]`, fallback HYDRA_NL.

**SMART rule chain (живой порядок):** supercell→DIRECT; telegram(dom+91.108.4.0/22)
→BALANCER_SMART; gov-ru + 188.68.217.194→REMNA_HOME; youtube→BLOCK затем DIRECT;
udp→DIRECT; happ.su→BALANCER_SMART; ru.goida.fun→DIRECT; 83.147.255.0/24→DIRECT;
geoip:ru→DIRECT; category-ru→DIRECT; catch-all tcp,udp→BALANCER_SMART.

### 1.8 Принцип архитектуры — ПОДТВЕРЖДЁН фактами

- Подписка собирается **subscription engine** (`subscription/engine.py`,
  vpn-bot `:9090`): `HAPP_ROUTING_LINE`/`happ_routing_line()` строят Happ JSON;
  `remnawave_subscription_links()` (vpn-bot.py:884) собирает vless-ссылки из
  server-key карты (`/smart`,`/fin`,…). Remnawave — **backend** (user store +
  node xray config).
- Native-подписка Remnawave (таблицы `subscription_settings`,
  `subscription_page_config`, `subscription_templates`) — **пустые** (0 строк) →
  не живой путь. `hosts` нужны Remnawave только для собственной подписки, которую
  мы не используем → их дубли безопасны для чистки.

**Вывод:** чистить `hosts`, bak-таблицы, лишние inbounds/outbounds можно, не ломая
подписку — при условии сохранения node→inbound FK и tag-UUID (см. §3 правила).

---

## 2. Кандидаты на удаление

### 2.1 Bak-таблицы (139 шт.) — РИСК НИЗКИЙ

Все `*_bak_*` — снимки из прошлых правок, не используются рантаймом Remnawave.
Разбивка: `config_profiles_bak_*` ×91 (большинство — `*_hydra_remna` поминутные
снимки sub-updater 2026-06-04/05), `config_profile_inbounds_bak_*` ×14,
`internal_squad_inbounds_bak_*` ×9, `config_profile_inbounds_to_nodes_bak_*` ×7,
`internal_squad_members_bak_*` ×4, `internal_squads_bak_*` ×4, `nodes_bak_*` ×4,
`hosts_bak_*` ×2, плюс `cpitn_bak_*`, `isi_bak_*`, `users_bak_*`,
`subscription_settings_bak_*`.

Полный список (для DROP-скрипта):

```
config_profile_inbounds_bak_20260525_remna_ws_ingress
config_profile_inbounds_bak_202605310359_cleanup
config_profile_inbounds_bak_20260604_113039_clean_names
config_profile_inbounds_bak_20260605_060614_clean_ru_ingress
config_profile_inbounds_bak_20260605_111351_happ_fin_swe
config_profile_inbounds_bak_20260605_114430_telegram_foreign
config_profile_inbounds_bak_20260605_tag_clean_20260605t061238z
config_profile_inbounds_bak_20260606_151626_tag_migration
config_profile_inbounds_bak_20260606_153514_hydra_dedup
config_profile_inbounds_bak_20260606_181808_goida_smart2
config_profile_inbounds_bak_20260606_182230_goida_smart2
config_profile_inbounds_bak_20260606t151609z_pre_cleanup
config_profile_inbounds_bak_grpc_202606010238
config_profile_inbounds_bak_reality_2053
config_profile_inbounds_to_nodes_bak_20260604_113039_clean_name
config_profile_inbounds_to_nodes_bak_20260605_111351_happ_fin_s
config_profile_inbounds_to_nodes_bak_20260605_114430_telegram_f
config_profile_inbounds_to_nodes_bak_20260606_151626_tag_migrat
config_profile_inbounds_to_nodes_bak_20260606_181808_goida_smar
config_profile_inbounds_to_nodes_bak_20260606_182230_goida_smar
config_profile_inbounds_to_nodes_bak_20260606t151609z_pre_clean
config_profiles_bak_20260525_215912_ru_routing_rules
config_profiles_bak_20260525_220643_bot_manual_routes
config_profiles_bak_20260525_221013_bot_manual_routes
config_profiles_bak_20260525_234542_hydra_remna
config_profiles_bak_20260525_234708_hydra_remna
config_profiles_bak_20260525_235026_hydra_remna
config_profiles_bak_20260525_block_udp443
config_profiles_bak_20260525_exit_useipv4
config_profiles_bak_20260525_home_outbound
config_profiles_bak_20260525_remna_ws_ingress
config_profiles_bak_20260525_restore_smart_udp
config_profiles_bak_20260525_smart_fra_only
config_profiles_bak_20260526_020032_hydra_remna
config_profiles_bak_202605310359_cleanup
config_profiles_bak_202605310407_homerename
config_profiles_bak_202605310420_f443
config_profiles_bak_20260603_040915_reserve_safety
config_profiles_bak_20260603_041434_reserve_safety
config_profiles_bak_20260603_115025_remna_server_routing_202606
config_profiles_bak_20260604_113039_clean_names
config_profiles_bak_20260604_113450_youtube_before_foreign
config_profiles_bak_20260604_120851_hydra_remna  ... (все *_hydra_remna 2026-06-04/05/06/07)
config_profiles_bak_20260604_160551_emergency_reserve_fin_20260
config_profiles_bak_20260604_160740_emergency_reserve_fin_zapre
config_profiles_bak_20260604_164433_restore_normal_reserve_2026
config_profiles_bak_20260604_164935_dedupe_reserve_tags_2026060
config_profiles_bak_20260604_165033_clean_hydra_preserve_202606
config_profiles_bak_20260604_171711_prefer_fin_smart
config_profiles_bak_20260604_220520_fix_foreign_balancer_fin_fr
config_profiles_bak_20260604_newru_remove_empty_profile
config_profiles_bak_20260605_060614_clean_ru_ingress
config_profiles_bak_20260605_111351_happ_fin_swe
config_profiles_bak_20260605_114430_telegram_foreign
config_profiles_bak_20260606_151626_tag_migration
config_profiles_bak_20260606_153514_hydra_dedup
config_profiles_bak_20260606_175734_reserve_fin_only
config_profiles_bak_20260606_181808_goida_smart2
config_profiles_bak_20260606_182230_goida_smart2
config_profiles_bak_20260606_182437_smart2_fix
config_profiles_bak_20260606_183432_smart2_443
config_profiles_bak_20260606t151609z_pre_cleanup
config_profiles_bak_grpc_202606010238
config_profiles_bak_hydra_202606010208
config_profiles_bak_reality_2053
config_profiles_bak_routing_rufix
cpitn_bak_20260605_tag_clean_20260605t061238z
hosts_bak_20260606_151618_hosts_cleanup
hosts_bak_20260606t151609z_pre_cleanup
internal_squad_inbounds_bak_*  (×9, см. полный дамп)
internal_squad_members_bak_*   (×4)
internal_squads_bak_*          (×4)
isi_bak_20260605_tag_clean_20260605t061238z
nodes_bak_*                    (×4)
subscription_settings_bak_20260527_hwid_native
users_bak_20260605_111351_happ_fin_swe
remna_xhttp_backup_20260522    (отдельный backup-table, не *_bak_ паттерн)
```

> Полный машинный список: `pg_tables WHERE tablename LIKE '%\_bak\_%'` (139 шт.) +
> `remna_xhttp_backup_20260522`. **Риск:** низкий — это снимки. **Оговорка:** перед
> массовым DROP сделать один pg_dump (фаза P1 бэкап ниже) — это и есть «бэкап
> бэкапов». Удалять только таблицы, которые матчат паттерн, НЕ live-таблицы.

### 2.2 Лишние/устаревшие outbounds в `ru-ws-ingress` — РИСК СРЕДНИЙ

- `HYDRA_NL_2`, `HYDRA_NL_3`, `BALANCER_HYDRA_NL` — **НЕ трогать руками**: их ставит
  sub-updater по живой hydra-подписке (§5.4 routing-logic). Если NL-бэкендов сейчас
  3 — это корректно. Чистка профиля не должна их сносить.
- Нет следов `smart-pro-out`, `socks-proxy-*`, `xhttp-test` в живых outbounds — уже
  чисто (в отличие от 3x-ui). **Кандидатов «мёртвых» outbound в live-профиле нет.**

### 2.3 Дубли в `hosts` — РИСК НИЗКИЙ (но косметика, не live-путь)

6 логических ru-хостов задублированы ×4 каждый (итого 24 строки):
`/smart ×4`, `/fin ×4`, `/fra ×4`, `/swe ×4`, `/direct ×4`, `/home ×4`. Плюс
`FRA Reality 443` (fra.goida.fun) и `Home Reality 7443` (is_hidden). Эти host-строки
нужны только native-подписке Remnawave, которую мы НЕ используем (подписку строит
engine). **Кандидат:** схлопнуть до 1 строки на путь. **Риск:** низкий, но это
правка через панель/host-API (НЕ direct-SQL DELETE по FK без проверки). Можно даже
отложить — на живой путь не влияет.

> Инбаунды без host-строки (норма, т.к. это reality-выходы и hydra): GOIDA_HYDRA_*,
> GOIDA_RESERVE, GOIDA_SMART2, HOME_VLESS_TCP_REALITY_7443,
> REMNA_VLESS_TCP_REALITY_7443. Не считать «мусором».

### 2.4 Синтетические HWID-устройства — РИСК НИЗКИЙ

16 строк в `hwid_user_devices` матчат `CHECK|TEST|shadow|smoke|VK-ROUTE|BOOSTY`.
Цель чистки — `scripts/cleanup_user_devices.py` (расширить `SYNTHETIC_RE` на
`shadow|smoke`). Юзеры-цели: `bozhenkas`, `remnatest`, `test-sub`. **Риск:** низкий,
dry-run обязателен.

---

## 3. Изменения к целевой логике (`docs/routing-logic.md`)

> **Главное правило (из инцидента 2026-06-05 / known-bugs §2):** НЕ делать
> direct-SQL `UPDATE … SET tag=…` по тегам, на которые ссылаются
> `config_profile_inbounds_to_nodes`, `internal_squad_inbounds`, `hosts`. Партиал-
> ренейм очищает node mappings. Tag меняется ТОЛЬКО через панель/контролируемый
> сейв всего профиля (Remnawave при сейве пере-синкает маппинги по UUID inbound'а),
> либо через atomic-скрипт, который вместе с тегом переписывает FK-ссылки в одной
> транзакции и затем `restart-all nodes`. UUID inbound'а должен оставаться
> стабильным.

Каждая фаза: **бэкап → изменение → shadow-test (smart/fin/fra/swe/direct из
реальной подписки + логи нод) → только потом следующая**.

### Фаза P1 — снять полный бэкап БД (ПЕРЕД любой мутацией)

```bash
# backup ПЕРЕД фазой
ssh -p 17904 root@45.91.54.152 \
 'docker exec remnawave-db pg_dump -U postgres -d postgres -Fc \
  -f /var/lib/postgresql/data/backup_20260611_precleanup.dump && \
  docker cp remnawave-db:/var/lib/postgresql/data/backup_20260611_precleanup.dump \
  /root/remna-backups/'
```
Изменение: нет (только бэкап). Риск: нет.

### Фаза P2 — DROP bak-таблиц (низкий риск)

Бэкап: P1 dump уже содержит их. Доп-страховка — `pg_dump` только bak-таблиц.
Изменение: `DROP TABLE` по списку §2.1 (паттерн `%\_bak\_%` + `remna_xhttp_backup_20260522`),
**исключив** live-таблицы. Генерировать DROP программно из `pg_tables`, не руками.
Risk: низкий. После — `\dt` проверка, что 4 live config-таблицы + nodes/hosts/squads целы.

### Фаза P3 — балансир `GOIDA_BALANCER_SMART` → target

Текущий: `random[REMNA_FRA, REMNA_SWE, REMNA_FI]` fb FI.
Target (`routing-logic §6`): `leastLoad[REMNA_FI, REMNA_FRA]` (= fin+fra),
**fallbackTag `REMNA_SWE`** (swe только при падении обоих).
Бэкап: `CREATE TABLE config_profiles_bak_20260611_balancer AS SELECT * FROM config_profiles;`
Изменение: правка `config->routing->balancers[GOIDA_BALANCER_SMART]` (selector+strategy+fallback)
+ убедиться, что observatory настроен для leastLoad. Делать через профиль-сейв/atomic-скрипт.
Risk: **средний** — затрагивает живой smart-путь. Учесть known-bugs §8 (swe/fra
intermittent — сейчас в логах есть timeouts; leastLoad сам отсеет лежачий, fallback swe
страхует). Обязателен shadow-test smart + логи fin/fra/swe.

### Фаза P4 — упразднить `direct-zapret`, слить в `direct` (Q2)

Проверка V1 (routing-logic §10): подтвердить, что zapret2 на новом RU матчит egress
по dst-порту 80/443 (nft/nfqueue), а не по SO_MARK — тогда отдельный direct-zapret
не нужен. **В этом аудите V1 НЕ проверялся (вне DB-скоупа).** Сделать перед P4:
`nft list ruleset | grep -i nfqueue` + проверить, есть ли отдельный direct-zapret
outbound (в live-профиле его НЕТ — есть только `DIRECT`). Похоже, уже слито в один
`DIRECT`. Изменение: вероятно no-op в outbounds; убедиться, что youtube/discord/«Русский»
→ `DIRECT` (уже так). Risk: низкий, но V1 обязателен до объявления «готово».

### Фаза P5 — переименование тегов GOIDA_* (косметика подписи) — ОТЛОЖИТЬ/осторожно

Теги уже `GOIDA_*` (миграция 2026-06-06 прошла). Опасные технические теги
`REMNA_VLESS_TCP_REALITY_7443` / `HOME_VLESS_TCP_REALITY_7443` /
`FRA_VLESS_TCP_REALITY_443` — **НЕ ренеймить direct-SQL**. Если хочется «по фен-шую»
(`GOIDA_FOREIGN_REALITY`), делать atomic-recreate через панель + node push (как в
inventory). **Риск:** высокий. Рекомендация: НЕ трогать в эту волну — на подписку и
маршрутизацию имя не влияет (engine не использует эти теги в выдаче).

### Фаза P6 — схлопнуть дубли hosts (косметика) — опционально

Бэкап: `CREATE TABLE hosts_bak_20260611 AS SELECT * FROM hosts;`
Изменение: оставить по 1 строке на путь. Через host-API/панель. Risk: низкий
(native-подписка не используется). Можно пропустить.

### Фаза P7 — HWID/устройства

`scripts/cleanup_user_devices.py` dry-run → apply. Расширить `SYNTHETIC_RE` на
`shadow|smoke`. Risk: низкий.

### Фаза P8 — sub-updater hydra (НЕ мутировать вручную)

Hydra inbounds/outbounds (`GOIDA_HYDRA_*`, `HYDRA_*`, `HYDRA_NL_{2,3}`,
`BALANCER_HYDRA_NL`) — под управлением sub-updater. Чистка профиля их НЕ касается.
Если нужно изменить hydra-логику — править `updater.py`, не БД руками.

---

## 4. Открытые вопросы / риски

- **R1 (V1 не проверен).** zapret2 dst-port vs SO_MARK на новом RU — нужно
  проверить nft до P4. От этого зависит, достаточно ли одного `DIRECT`.
- **R2 (swe/fra intermittent).** В backend-логах 2026-06-10/11 есть health/stats
  timeouts на swe (89.22.230.5) и fra (95.163.152.210). При переводе балансира на
  `leastLoad[fin,fra]` fallback swe — убедиться, что observatory корректно метит
  лежачий бэкенд. Иначе риск посадить smart на нестабильный fra. Shadow-test
  обязателен (known-bugs §8 — не верить WS 101).
- **R3 (home mgmt EHOSTUNREACH).** Периодический `EHOSTUNREACH 78.107.88.21:58443`
  в логах при `is_connected=t`. Резидентный home за NAT — нужен keepalive/туннель,
  иначе ru-via-home (банки) может моргать. Не блокер чистки, но операционный риск.
- **R4 (3 юзера без user_traffic).** `users`=27, `user_traffic`=24. Возможна грабля
  known-bugs §9 (API user lookup падает без user_traffic-row). Проверить перед
  любой работой с юзерами; не чистить юзеров, пока не понятно.
- **R5 (flow-патч эфемерный).** known-bugs §1: VLESS flow-патч живёт ВНУТРИ
  контейнера `remnawave`. Любой `docker compose pull/up` его снесёт → foreign Reality
  начнёт отбивать клиентов. НЕ обновлять панель в рамках чистки.
- **R6 (Telegram).** Сейчас telegram в smart → `GOIDA_BALANCER_SMART` (соответствует
  target). Бэклог routing-logic §10-Q3 (telegram на RU с обфускацией) — НЕ в этой волне.
- **R7 (native subscription).** Подтверждено: native Remnawave-подписка пустая,
  engine строит выдачу. Но если кто-то в будущем включит native-подписку, дубли
  hosts всплывут — поэтому P6 (схлопывание) полезно как гигиена, хоть и не срочно.
- **R8 (один актор / shadow-test).** Инцидент 2026-06-05: запрет на параллельные
  мутации из нескольких агентов и на «fixed» без client-equivalent теста. Каждая
  фаза P3-P7 — один прод-актор + полный shadow-checklist.

---

## 5. Сводка diff к target (routing-logic)

| что | live сейчас | target | действие |
|---|---|---|---|
| smart balancer | random[fra,swe,fi] fb fi | leastLoad[fin,fra] fb swe | **P3** |
| fra выход | отдельный FRA_VLESS_TCP_REALITY_443 (готов) | полноценный выход в пуле | уже есть, включить в селектор |
| direct-zapret | отдельного нет, только DIRECT | один DIRECT | **P4** (после V1) |
| smart-pro/socks/xhttp-test | отсутствуют в live | удалить | уже чисто |
| теги | GOIDA_* (мигрированы) | GOIDA_* | ок, опасные REMNA_*/HOME_* не трогать |
| hydra | sub-updater (NL×3 балансир) | динамический | не трогать |
| bak-таблицы | 139 + 1 backup | 0 | **P2** |
| hosts дубли | ×4 на путь | ×1 | **P6** (опц.) |
| synthetic HWID | 16 | 0 | **P7** |
