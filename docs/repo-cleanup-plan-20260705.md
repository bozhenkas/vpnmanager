# План полной чистки и структуризации репо — 2026-07-05

> Основан на 4 параллельных аудитах (структура+git, дублирование, deploy, тесты+мёртвый код).
> Цель владельца: чистый репозиторий, лёгкая раскатка правок на прод, всё чётко по директориям.
> Прод НЕ трогаем нигде, кроме явно помеченных шагов (Phase 3 деплой консолидированного кода — только по отдельному ОК).

## Диагноз (сжатая картина)

1. **~95 файлов не закоммичено** — фактически весь текущий прод-стек (client-bot/, client-web/, infra-backend/, infra-web/, intermesh/, remnawave_client/, scripts/ целиком, docs/, ~25 systemd-юнитов) живёт вне git. Репо не является source of truth, хотя должен быть зеркалом прода.
2. **~500 строк дублей**: 3 независимые копии docker-exec-psql транспорта (client-bot, switch.py, manual_routes) + 16 локальных psql()-хелперов в scripts/ + дубли dotenv-лоадера, Telegram-обёртки, Happ-профиля, routing-констант. Плюс **гонка записи config_profiles** (switch.py vs manual_routes.py) и **футган**: `HAPP_ROUTING_LINE` в vpn-bot.py присваивается дважды (:290 и :1648), вторая — hardcoded base64, тихо затеняет первую.
3. **Хрупкий импорт-механизм**: vpn-bot.py находит `subscription`/`remnawave_client` только потому, что на проде они rsync-нуты сиблингами в /root/vpn-bot/. Из репо `python bot/vpn-bot.py` не работает; тесты обходят через spec_from_file_location.
4. **Deploy-дрейф**: `deploy/systemd/sub-updater.service` — устаревший while-true (прод давно на oneshot+timer из proposed/); deploy/nomad/ целиком на мёртвых IP; 2 proposed drop-in'а на списанный ru-4; у epilepsy и sub-updater/updater.py (FIN) нет юнитов в репо; деплой-тулинга нет вообще (ручной rsync по памяти).
5. **Мусор**: tmp/ (~30 разовых скриптов + дампы юзер-данных subs_raw*.json), tmp_actor_cheatsheets/ (постороннее), .cursor/ и tmp*/ не в .gitignore, stray routing.json и tmp_prefer_fin_balancer.py в корне.
6. **Тесты**: 217 passed / 3 failed (дрейф поведения: happ-профили в test_bot_devices, hysteria2→vless в test_smart2). remnawave_client/, infra-backend/, rkn-checker.py — без тестов. Зависимости не декларированы нигде (один requirements.txt на весь репо — в deployer-bot/).
7. **Мёртвый код**: в vpn-bot.py (6065 строк) ~7 функций ни разу не вызываются (xui_add_client, hysteria_get_status/set, native_sub_set, adblock_dns_set, get_subscription_content, check_ip_limit, wl_registry_add); 14+ разовых миграционных скриптов в scripts/ (p14/p15/p16/smart2_*/fix_*/apply_*/emergency_ru4*).

---

## Phase 0 — .gitignore + карантин мусора (без изменения кода, 1 сессия)

Порядок важен: сначала ignore, потом коммиты — чтобы мусор не уехал в историю.

- [ ] .gitignore += `tmp/`, `tmp_*/`, `.cursor/`, `.pytest_cache/`, `/routing.json`
- [ ] Проверить `.cursor/mcp.json` на креды перед решением ignore-vs-commit (по умолчанию ignore)
- [ ] `tmp/` — НЕ коммитить никогда (дампы юзер-данных subs_raw*.json). Локально можно оставить, git его не видит
- [ ] `tmp_actor_cheatsheets/` — удалить (постороннее; **спросить владельца** перед rm)
- [ ] `tmp_prefer_fin_balancer.py`, stray `routing.json` в корне — переместить в tmp/ или удалить

## Phase 1 — закоммитить реальность (~10 логичных коммитов, 1 сессия)

Принцип: один компонент = один коммит, conventional commits как в истории (feat/fix/chore/docs, русские summary).

Перед коммитом modified-файлов — быстрый sanity: они синкались с прода 2026-07-05, считаем их актуальными (последняя сессия синкала nginx-конф явно).

1. `chore: .gitignore + удаление stray-файлов` (Phase 0)
2. `feat: remnawave_client/ — единая точка доступа к Remnawave` (+ упоминание move-only из vpn-bot)
3. `feat: client-bot/ + client-web/ — клиентский бот и Mini App`
4. `feat: infra-backend/ + infra-web/ — админ-дашборд`
5. `feat: intermesh/ — analyzer + switch межсерверной сети`
6. `feat: ru-geo-analyzer/ — Go-демон детекта RU-промахов`
7. `feat: subscription/ — cf_api, cluster_config, ru_direct_auto, ru_routing, xhttp + engine sync`
8. `feat: scripts/ — рабочие утилиты + разовые миграции` (чистка разовых — в Phase 2, сначала зафиксировать как есть)
9. `feat: deploy/ — systemd/nginx/logrotate/netdata синк с прода`
10. `test: 21 новый тест-файл + fixtures`
11. `docs: specs, incident reports, research`
12. `fix: синк живых правок bot/, sub-updater/, ip-watchdog/, AGENTS.md`

После Phase 1 репо снова = зеркало прода, и любые дальнейшие правки видны диффом.

## Phase 2 — чистка мёртвого (1 сессия)

- [ ] `scripts/archive/` (или `legacy/scripts/`): переместить 14+ отработавших миграций — p14_*, p15_*, p15b_*, p16_*, smart2_* (4 шт), apply_routing_fix.py, fix_remna_ru_safety_routes.py, fix_ws_heartbeat.py, migrate_xui_traffic.py, remna_clean_rename.py, remna_host_cleanup.py, remna_hydra_dedup.py, remna_tag_migration.py, emergency_ru4_* (2 шт). README со ссылкой на даты применения
- [ ] `deploy/nomad/` — удалить целиком (оба IP мёртвые: 83.147.255.98, 89.22.230.5-monitoring; Nomad-миграция закрыта 2026-05-15). **Подтвердить у владельца**
- [ ] `deploy/systemd/proposed/`: удалить 2 dead-host drop-in'а (xray-reserve-fin-limits, xray-ru4-egress-limits); `sub-updater.service/.timer` из proposed/ → в основной deploy/systemd/ (замена stale while-true версии — это то, что реально на проде); остальные *-limits.conf оставить в proposed/ с README
- [ ] `web/` — сверить с /var/www/html на RU (read-only), если superseded → legacy/
- [ ] Мёртвые функции vpn-bot.py (7 шт: native_sub_set :1333, adblock_dns_set :1341, xui_add_client :1406, hysteria_get_status :1838, hysteria_set :1848, get_subscription_content :2093, check_ip_limit :2248, wl_registry_add :4820) — удалить после grep-подтверждения; legacy-sub path НЕ трогать (ещё используется)
- [ ] Проверить legacy-path ссылку в subscription/cluster_config.py перед любыми правками legacy/
- [ ] Разобраться с тестовым дрейфом: 3 failing теста (test_bot_devices x2, test_smart2 emergency hysteria2→vless) — обновить ожидания под текущее поведение engine

## Phase 3 — дедупликация (по рискам, 2-3 сессии; деплой на прод — отдельный ОК на каждый шаг)

Приоритеты из аудита:

1. **config_profiles guarded writer** (закрывает гонку — бэклог-пункт «шаг 2»):
   - в remnawave_client/transport.py: `update_config_profile(name, mutator_fn)` — SELECT...FOR UPDATE + UPDATE в одной транзакции (docker exec psql с `BEGIN; ... COMMIT;` одним вызовом) + автоматический backup-table как сейчас
   - переключить intermesh/switch.py (_psql/_sql_str/_sql_json → транспорт) и bot/manual_routes.py (_run/_load_remna_profile/sync_remnawave)
   - тесты на writer (у remnawave_client их сейчас ноль)
   - **деплой на RU**: sibling-схема уже возит remnawave_client/, switch.py живёт в /opt/goida-intermesh — проверить, как он импортирует (может понадобиться копия пакета или sys.path)
2. **HAPP_ROUTING_LINE футган** — vpn-bot.py:1648 переприсваивает :290 hardcoded base64. Разобраться, какая версия реально нужна проду, оставить одну, источник — subscription/ru_routing.py
3. **client-bot → remnawave_client**: сначала перенести вверх remnawave_delete_device и lateral-join вариант devices, потом удалить ~135 строк локальных копий (pg_quote :669, remnawave_query :673, remnawave_user :690, remnawave_devices :711, remnawave_delete_device :738, inline psql в server_catalog :817)
4. **Живые scripts/** на общий транспорт: sync_remna_hydra.py, promote_ru_candidates.py, hwid_inspector.py, cleanup_user_devices.py, migrate_ingress_ip.py, remna_routing_spec.py + apply_remna_routing_spec.py (архивные не трогаем — они уже в archive/)
5. **Routing-константы**: Telegram CIDR / discord-voice / geosite-списки, продублированные в manual_routes.py:201 и updater.py:683 → общий модуль (subscription/ или новый common/)
6. Косметика (опционально, в конце): dotenv-лоадер x2, Telegram-обёртка x2, sub-URL builder

Единый принцип: бизнес-логика (что писать) остаётся на месте, выносится только транспорт (как писать).

## Phase 4 — deploy-гигиена + единый деплой-инструмент (1-2 сессии)

Закрывает бэклог-пункт №6 из топ-10 («единый деплой-инструмент вместо ручного rsync/scp»).

- [ ] **Deploy-манифест** `deploy/manifest.json`: компонент → {host, src в репо, dst на сервере, unit(s), post-deploy check}. Из аудита уже есть полная таблица unit→ExecStart→repo-dir
- [ ] **scripts/deploy.sh** (или deploy/deploy.py): `deploy.sh <component> [--host ru] [--dry-run]` — rsync с --dry-run по умолчанию, backup на сервере по runbook-схеме (/root/deploy-backups/...), py_compile перед заливкой, systemctl restart + is-active после, отчёт. Реализует «Deploy immediately»-правило из памяти безопасным способом
- [ ] Добавить недостающие юниты в репо: sub-updater/updater.py (FIN-юнит), указатель на epilepsy (код gitignored, но юнит-файл можно трекать)
- [ ] infra-backend/: README.md + env.example (сейчас голый .py)
- [ ] deployer-bot/systemd/ — решить: влить в deploy/systemd/ или оставить (deployer-bot переезжает на FIN по Phase B миграции)
- [ ] Зависимости: root `pyproject.toml` (или requirements.txt на компонент) — зафиксировать реальные версии с прод-хостов (read-only pip freeze)
- [ ] Задокументировать в README: карта директорий, sibling-схема импортов на проде, naming-конвенции юнитов (goida-* vs без префикса — НЕ переименовывать на проде, только зафиксировать)

## Phase 5 — тесты и защита от регрессий (фоново, вместе с Phase 3-4)

- [ ] Тесты remnawave_client/ (транспорт мокается, guarded writer — обязательно)
- [ ] Тест rkn-checker.py (сейчас ноль)
- [ ] infra-backend/ — хотя бы smoke на auth/роуты
- [ ] pre-deploy проверка в deploy.sh: pytest затронутого компонента + py_compile

---

## Решения, нужные от владельца

1. `tmp_actor_cheatsheets/` — удалить? (выглядит посторонним для проекта)
2. `deploy/nomad/` — удалить целиком? (мёртвые IP, миграция закрыта)
3. `web/` — считать superseded client-web'ом → в legacy/?
4. `.cursor/` — в ignore или коммитить без mcp.json?
5. Phase 3.1 (guarded writer) деплоить на прод сразу после локальной готовности или копить батч?

## Порядок и оценка

| Phase | Что | Прод-риск | Объём |
|---|---|---|---|
| 0 | ignore+карантин | нет | 30 мин |
| 1 | закоммитить всё | нет | 1 сессия |
| 2 | чистка мёртвого | нет (read-only сверка web/) | 1 сессия |
| 3 | дедуп | деплой по явному ОК | 2-3 сессии |
| 4 | deploy-тулинг | деплой-скрипт сам по себе безопасен (dry-run default) | 1-2 сессии |
| 5 | тесты | нет | фоново |

Phases 0-2 полностью локальные и безопасные — их можно пройти подряд за один заход.
