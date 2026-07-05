# goida-vpn — техническое описание архитектуры

> Документ для перепроектирования. Секреты (токены, пароли, ключи) опущены.
> Актуальность: 2026-06-14.

---

## 1. Кластер серверов

| alias | IP | домен | роль | SSH |
|---|---|---|---|---|
| **ru** | `45.91.54.152` | `ru.goida.fun` | основной RU-вход, Remnawave панель+нода, бот, подписки | port 17904 |
| **ru-backup-ip** | `45.91.53.93` | (тот же `ru.goida.fun` при failover) | вторичный IP того же хоста, xHTTP-вход `GOIDA_SMART2` | — |
| **reserve** | `194.117.80.94` | `reserve.goida.fun` | резервный вход (независим от RU), свой xray→FIN | port 22 |
| **fin** | `77.110.108.57` | `fin.goida.fun` | выход Финляндия, remnanode | port 17904 |
| **fra** | `95.163.152.210` | — | выход Франция, remnanode | port 17904 |
| **swe** | `89.22.230.5` | `swe.goida.fun` | выход Швеция (fallback), remnanode, Nomad | port 17904 |
| **home** | `78.107.88.21` | — | жилой RU-выход (банки/госуслуги), remnanode | port 17904 |
| **hydra-nl** | `nl.north-1winter.cv` | — | сторонний NL, провайдер whitestore | — |
| **hydra-de** | `de.smotri-shop.top` | — | сторонний DE, провайдер whitestore | — |
| **hydra-pol** | `188.255.163.44` | — | сторонний PL, провайдер whitestore | — |
| **hydra-tur** | `try.north-1winter.cv` | — | сторонний TR, провайдер whitestore | — |

DNS: Cloudflare, TTL=60. A-запись `ru.goida.fun` переключается между primary/backup через ip-watchdog при блокировке.

---

## 2. Резервный сервер — reserve (`194.117.80.94`)

> Полностью независим от основного RU `45.91.54.152`. Отдельный VPS, свой xray, собственный egress в FIN через SSH-туннель.

### Цепочка трафика
```
Клиент (Happ, «Резервный 🇰🇵 (мобильная связь)»)
  │  VLESS Reality gRPC, SNI=web.max.ru, reserve.goida.fun:443
  ▼
[reserve 194.117.80.94]  xray (/etc/xray/reserve-fin.json)
  │  inbound GOIDA_RESERVE (:443, gRPC, Reality)
  │  routing: GOIDA_RESERVE → outbound REMNA_FI
  │  REMNA_FI = VLESS → 127.0.0.1:17905 (security none)
  ▼
[SSH-туннель ru4-fin-tunnel]  127.0.0.1:17905 → 77.110.108.57:17905
  ▼
[FIN 77.110.108.57]  xray (/etc/xray/ru4-egress.json)
  │  inbound RU4_PRIVATE (:17905, vless, security none)
  │  routing: RU4_PRIVATE → outbound DIRECT (freedom)
  ▼
Интернет с IP Финляндии (77.110.108.57)
```

### Параметры клиентской подписки (reserve)
| параметр | значение |
|---|---|
| host | `reserve.goida.fun` → `194.117.80.94` |
| port | `443` |
| transport | gRPC, serviceName=`grpc`, multiMode=true |
| security | REALITY |
| SNI | `web.max.ru` |
| fingerprint | `chrome` |

### Сервисы на reserve
| сервис | статус | описание |
|---|---|---|
| `xray-reserve-fin.service` | active (с 2026-06-07) | основной xray, `/etc/xray/reserve-fin.json` |
| `ru4-fin-tunnel.service` | active, Restart=always | SSH-туннель reserve→FIN:17905 |
| `x-ui` (legacy) | active/disabled | :2096/:25565, legacy панель, не используется |

### Зависимость от основного RU
**Нет.** Если `45.91.54.152` упадёт — reserve продолжит работать. Единственная зависимость — FIN (`77.110.108.57`, SSH-туннель).

### Открытые задачи по reserve
- Завязка на SSH-туннель — потенциальная точка отказа (alt: прямой VLESS-Reality reserve→FIN напрямую)
- FIN `RU4_PRIVATE` слушает `0.0.0.0:17905 security none` — желательно перевязать на `127.0.0.1` или закрыть файрволом
- Legacy x-ui на reserve: второй xray + панель не используются, нужна отдельная фаза отключения

---

## 3. Технологический стек

### VPN-ядро
- **Xray-core** (v26.3.27) — туннелирование, роутинг, управление
- **Remnawave** — панель управления нодами (PostgreSQL 17.6 backend, Valkey/Redis кэш)
- **remnanode** — агент на каждой ноде (docker контейнер, supervisord внутри)
- Старый слой: **3X-UI** (x-ui, SQLite `xrayTemplateConfig`) — частично остался для legacy Hydra

### Транспорты и протоколы

| профиль / инбаунд | протокол | транспорт | порт/IP | защита |
|---|---|---|---|---|
| SMART, RU, FIN, FRA, SWE | VLESS | gRPC | `45.91.54.152:443` | REALITY |
| RESERVE | VLESS | gRPC | `reserve.goida.fun:443` (194.117.80.94) | REALITY, SNI=web.max.ru |
| SMART2 («Оптимальный 2») | VLESS | xHTTP | `45.91.53.93:7443` | REALITY, SNI=ok.ru |
| управление нодой | mgmt API | TCP | `45.91.54.152:58443` | — |
| Hydra (whitestore) | VLESS | — | 443 / 8443 (внешний провайдер) | TLS |
| вход client→RU (старый 3X-UI слой) | VLESS | WebSocket | `443` (nginx proxy) | TLS (Let's Encrypt) |

**Примечание по SMART2:** xHTTP+REALITY на `45.91.53.93:7443` — iOS Happ не работает (не инициирует xHTTP-коннект). Сервер исправен, проблема на стороне клиента. Нужна замена транспорта на gRPC.

### nginx на RU (443, `ru.goida.fun`)
- TLS termination (Let's Encrypt)
- gRPC-pass на Remnawave inbounds (127.0.0.1:17443-17448)
- proxy_pass на subscription engine (127.0.0.1:9090)
- legacy WebSocket proxy на 3X-UI hydra inbounds (10012-10016, дают 502 пока x-ui не поднят)
- `/notify` — allow только с домашнего ISP (78.107.88.21), relay алертов
- `/rknstatus` — приём JSON-отчётов от rkn-checker

### nginx на RU (443, `web.goida.fun`)
- Reverse proxy на client-bot Mini App API (127.0.0.1:9081)
- X-Robots-Tag: noindex

### Обфускация DPI — zapret2 / nfqws2
- Systemd `zapret2.service` / `nfqws2.service` на хосте RU
- nftables матчит egress по **dst-порту 80/443** (не по SO_MARK)
- YouTube-стратегия:
  - UDP/443 QUIC: `fake quic_google`, repeats=8, по `list-google.txt`
  - TCP/443 TLS: `multisplit pos=1 seqovl=681 seqovl_pattern=tls_google ip_id=zero`
- YouTube QUIC дополнительно блокируется на уровне Xray (inbound SMART/RU) → клиент форсированно уходит в TCP/TLS
- `list-google.txt` **не содержит** googleapis/gstatic/googleusercontent

---

## 4. Программные компоненты

### 4.1 vpn-bot (управляющий бот)
**Роль:** управление пользователями, выдача подписок, администрирование, алерты.

- Python 3.12, single-file `/root/vpn-bot/vpn-bot.py`, **stdlib-only** (без внешних pip)
- Sync Telegram polling
- HTTP subscription server в отдельном треде на `127.0.0.1:9090`
- SQLite `bot.db`
- systemd: `vpn-bot.service`, WorkingDirectory `/root/vpn-bot`, Restart=always

**Ключевые возможности:**
- `/user <name>` — inline user card (продление, лимит устройств, free access, устройства, ручной роутинг)
- `/sub` — управление hydra-подпиской: просмотр SUB_URL/UA, пагинированный список серверов (green=enabled), toggle → пишет в `config.env`, рестарт sub-updater
- `/dnsip` — текущая CF A-запись, inline кнопки переключения primary↔backup через CF API
- `/subdesc` — заголовок подписки в Happ (`subscribe_next_description`)
- `/devices [name]` — телеметрия устройств по HWID/UA
- `/notify` — relay алертов от ip-watchdog (домашний сервер → TG)
- Ручная маршрутизация сайтов: domain → `direct` / `home` / `foreign` (таблица `manual_domain_rules`)

**Env** (`/root/vpn-bot/.env`): BOT_TOKEN, OWNER_ID, CF_TOKEN, CF_ZONE_ID, NOTIFY_TOKEN, NOTIFY_ALLOWED_IP, SMART2_REALITY_SNI, RESERVE_REALITY_SNI/PBK/SID и др.

### 4.2 subscription engine (пакет)
**Роль:** генерация VPN-профилей для Happ.

- Пакет `/root/vpn-bot/subscription_engine/` (local: `subscription/engine.py`)
- Генерирует: Happ routing JSON (L1 клиентский роутинг) + vless:// ссылки на все ноды

**Схема работы:**
```
GET /subscribe/<token>
    │
    ├── UA == Happ + HWID валиден?
    │       ├── ДА → реальные ноды + routing JSON (happ://routing/onadd/...)
    │       └── НЕТ → fake VLESS «скачайте Happ» / HTML-заглушка / YAML-fallback
    │
    └── Ответ = base64(plain-text подписка)
```

**Структура Happ-подписки:**
1. `happ://routing/onadd/<base64>` — профиль L1 роутинга
2. `#profile-title: goida :)`
3. Основные ноды: SMART, RU (zapret), FIN, FRA, SWE, RESERVE, (SMART2)
4. Hydra ноды (если включены для юзера)
5. Whitelist ноды
6. `custom_sub` пользователя

**Маршруты подписки:**
| маршрут | описание |
|---|---|
| `/subscribe/<token>` | основной, subscription engine + HWID gate |
| `/subscribe/<token>/json` | V2Ray JSON (без роутинга) |
| `/subscribe/<token>/clash` | YAML заглушка |
| `/subscribe-next/<token>` | с routing в JSON (`domainStrategy=IPIfNonMatch`) |
| `/subscribe-old/<token>` | legacy plain base64, по флагу `legacy_sub` |
| `/happ/<token>` | bridge → Happ deep link |

**HWID gate:**
- Happ UA формат: `Happ/.../<hwid>`
- Также: `?hwid=` или `?device-id=`
- Без HWID → fake ноды; браузер Accept:text/html → branded HTML без единого `vless://`

### 4.3 goida-client-bot + Mini App
**Роль:** клиентский интерфейс — пользователь видит подписку, устройства, оплату, серверы.

- Python 3.12, single-file `/opt/goida-client/client-bot/client-bot.py`, **stdlib-only**
- Одновременно: Telegram polling + ThreadingHTTPServer на `127.0.0.1:9081`
- HTTP-сервер отдаёт Static SPA (`/opt/goida-client/client-web/`) + API-маршруты
- systemd: `goida-client-bot.service`, EnvironmentFile `/etc/goida-client/client-bot.env`, Restart=always
- Деплой: rsync с мака после правок (не спрашивать подтверждения)

**Экраны Mini App:**
- **Мой ключ** — статус подписки, ссылка, кнопка «подключить» (Happ deep link), трафик, список устройств
- **Оплата** — paid-until, дата следующего платежа, лимит устройств, заявка на смену тарифа
- **Серверы** — включение/выключение доступных серверов (`client_server_prefs`)

**Каталог серверов (REMNAWAVE_SERVER_CATALOG в коде):**
`smart`, `smart2`, `reserve`, `fi`, `fra`, `se`, `zapret`, `hydra:usa/pol/tur/nl/de`

**Безопасность API:**
- Только валидный Telegram WebApp `initData` + HMAC подпись
- `auth_date` TTL = 1800 сек
- Telegram user_id должен быть в `client_tg_links` (явная связка с VPN username)

**Тарифы (константы в коде):**
- Базовая цена: **170 ₽/мес**, DEFAULT_DEVICES=2
- Доп. устройство: **+30 ₽**, MAX_DEVICES=7
- День оплаты: 14-е число

**Web:** `web.goida.fun` (nginx → 127.0.0.1:9081), X-Robots-Tag: noindex, TLS Let's Encrypt

**Таблицы БД (bot.db, shared с vpn-bot):**
- `client_tg_links` — tg_id → username
- `client_profiles` — paid_until, device_limit, free_access, payment_reminders_enabled
- `client_server_prefs` — включённые серверы по юзеру
- `client_plan_requests` — заявки на смену тарифа
- `client_reminders` — дедупликация напоминаний об оплате
- `client_invite_tokens` — токены приглашений

### 4.4 sub-updater
**Роль:** синхронизация Hydra-серверов из внешней подписки whitestore в 3X-UI + Remnawave.

- Python 3.12, `/opt/sub-updater/updater.py` (на RU) + `sync_hydra_remna.py`
- systemd: цикл `while true; do python3 sync_remna_hydra.py; sleep 600; done`, Restart=always
- Config override: `/opt/sub-updater/config.env` (SUB_URL, SUB_UA — пишет бот через `/sub`)

**Алгоритм:**
1. GET `https://sub.whitestore.club/<token>` с `User-Agent: v2box_short` + `X-HWID: <hwid>`
2. Парсит base64, извлекает VLESS-ноды, маппит по названию (Польша→pol, Netherlands→nl…)
3. Пингует каждый сервер (несколько проб) — только живые идут дальше
4. Обновляет outbound в **3X-UI SQLite** (`xrayTemplateConfig`)
5. `sync_hydra_remna.py` копирует обновлённые outbound в **Remnawave** `config_profiles` (мост при ротации UUID провайдером)
6. Рестарт remnanode только при реальном изменении

**Whitelist:**
- `whitelist_links.txt` — авто-whitelist из подписки (ключевые слова: "Whitelist", "РЕЗЕРВ")
- `whitelist_manual.txt` — ручной whitelist
- Whitelist registry: `/opt/wl-registry/wl-list.txt` (отдаётся nginx `/wl/list`)
- Перед включением — probe `https://www.gstatic.com/generate_204` (≥2 из 3 попыток)

### 4.5 deployer-bot (отдельный подпроект)
**Роль:** управление деплоем community VPN-нод (отдельный бот, не основной).

- Репо: `/opt/vpndeployer` на **FIN** (77.110.108.57), docker compose
- Python, aiogram v3, asyncssh, asyncpg/PostgreSQL, RedisStorage
- CI/CD: `.github/workflows/deploy.yml` (GitHub Actions, секреты SSH_HOST/PORT нужно обновить)
- 106 тестов

**Реализовано:** direct scenario, cascade scenario (skeleton), CI/CD pipeline
**Открыто:** simplified management vpn-bot.py для cascade RU-ноды, расширение кластера

### 4.6 HWID Inspector
**Роль:** read-only инспекция HWID и User-Agent из Remnawave (аудит устройств).

- Python, `/opt/hwid-inspector/hwid_inspector.py`
- systemd oneshot + timer: `hwid-inspector.service` / `hwid-inspector.timer`
- Работает после docker.service, читает данные ноды

---

## 5. Логика роутинга (два уровня)

### L1 — клиентский (Happ routing JSON в подписке)
Главная задача: **geo-RU трафик уходит напрямую с устройства**, не доходя до сервера — RU-сайты не видят IP VPN.

```json
{
  "DomainStrategy": "IPIfNonMatch",
  "RemoteDNS": "DoH cloudflare-dns.com (1.1.1.1)",
  "DomesticDNS": "DoH dns.yandex.ru (77.88.8.8)",
  "DirectSites": [
    "geosite:category-ru", "regexp:.*\\.ru$", "regexp:.*\\.su$",
    "domain:vk.com", "domain:ok.ru", "domain:ozon.ru", "domain:wildberries.ru",
    "domain:sberbank.ru", "domain:tbank.ru", "domain:gosuslugi.ru", "..."
  ],
  "DirectIp": ["geoip:ru", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
}
```

Доставляется как `happ://routing/onadd/<base64>` в начале подписки и в HTTP заголовке `routing`.

### L2 — серверный (Remnawave config_profile на RU-ноде)
Страховка по RU + выбор зарубежного выхода.

#### Сводная матрица «инбаунд × категория → выход»

| категория | SMART | RU | FIN | FRA | SWE | HYDRA-x | RESERVE |
|---|---|---|---|---|---|---|---|
| private | direct | direct | direct | direct | direct | direct | direct |
| bittorrent | blocked | blocked | blocked | blocked | blocked | blocked | blocked |
| youtube-quic UDP/443 | blocked | blocked | — | — | — | — | — |
| youtube / discord TCP | **direct** (zapret) | direct | свой сервер | свой сервер | свой сервер | своя страна | fin |
| ru-via-home (банки/маркетплейсы/госуслуги) | **home-exit** | direct | fallback | fallback | fallback | fallback | fin |
| ru-vpn-checker-ip (Яндекс/VK AS) | **home-exit** | direct | fallback | fallback | fallback | fallback | fin |
| telegram | **balancer-foreign** | direct | свой сервер | свой сервер | свой сервер | своя страна | fin |
| ipleak (ipinfo/whoer/…) | direct | direct | свой сервер | свой сервер | свой сервер | своя страна | fin |
| ru-geo | fallback→direct | direct | fallback | fallback | fallback | fallback | direct |
| **foreign catch-all** | **leastLoad[fin,fra]→swe** | direct | fin | fra | swe | своя страна | **fin** |

#### Зарубежный балансировщик (SMART catch-all + telegram)
```
balancer-foreign:
  selector:    [fin, fra]   — leastLoad (burstObservatory пингует gen_204 каждые 30 мин)
  fallbackTag: swe          — только когда fin и fra оба недоступны
```

#### Аутбаунды RU-ноды
| outbound | тип | назначение |
|---|---|---|
| `direct` | freedom | прямой выход + zapret2 на хосте (один direct, без маркировки) |
| `home-exit` | VLESS→REALITY | `78.107.88.21:4443` жилой RU |
| `fin` | VLESS→REALITY | `77.110.108.57:443` |
| `fra` | VLESS→REALITY | `95.163.152.210:443` |
| `swe` | VLESS→REALITY | `89.22.230.5:443` |
| `balancer-foreign` | balancer | leastLoad[fin,fra], fallback swe |
| `hydra-<cc>` | VLESS | управляется sub-updater динамически |
| `blocked` | blackhole | bittorrent, youtube-QUIC UDP/443 |
| `dns-out` | dns | только для port-53 (единственное правило) |

---

## 6. Инбаунды на RU-ноде

| тег Remnawave | путь nginx | upstream | назначение |
|---|---|---|---|
| `GOIDA_SMART` | `/smart`, `/smart-pro` | `127.0.0.1:17443` | Оптимальный (balancer fin/fra/swe) |
| `GOIDA_RU` / direct | `/direct` | `127.0.0.1:17444` | Русский (всё direct+zapret) |
| `GOIDA_FIN` | `/fin`, `/fi` | `127.0.0.1:17445` | Финляндия |
| `GOIDA_FRA` | `/fra` | `127.0.0.1:17446` | Франция |
| `GOIDA_SWE` | `/swe`, `/se` | `127.0.0.1:17447` | Швеция |
| `HOME_*` | `/home` | `127.0.0.1:17448` | Жилой RU (home-exit) |
| `GOIDA_RESERVE` | (на RU — спящая страховка) | `:2053` (gRPC Reality) | catch-all → fin; реально трафик идёт через reserve VPS |
| `GOIDA_SMART2` | — | `45.91.53.93:7443` | xHTTP+REALITY «Нео» (⚠️ iOS не работает) |
| `REMNA_HYDRA_*` | `/pol-out`, `/tur-out`, `/nl-out`, `/de-out` | 10012-10015 | Hydra (legacy 3X-UI WS) |

Все основные инбаунды: gRPC через nginx→Remnawave на 443, `heartbeatPeriod:30`.

---

## 7. Hydra — сторонние серверы

### Архитектура
```
Провайдер whitestore (sub.whitestore.club)
  ↓  ротация UUID периодически
sub-updater (каждые 10 мин)
  ├── парсит подписку (X-HWID required)
  ├── пингует каждый сервер
  ├── обновляет 3X-UI SQLite (xrayTemplateConfig, outbounds)
  └── sync_hydra_remna.py → Remnawave config_profiles (мост)

Клиент → ru.goida.fun:443/nl-out → nginx → Remnawave HYDRA_NL → nl.north-1winter.cv:443 → интернет NL
```

### Страны и порты
| страна | путь | порт (legacy) | outbound |
|---|---|---|---|
| USA 🇺🇸 | `/usa-out` | 10011 | `hydra-proxy-usa` |
| Poland 🇵🇱 | `/pol-out` | 10012 | `hydra-proxy-pol` |
| Turkey 🇹🇷 | `/tur-out` | 10013 | `hydra-proxy-tur` |
| Netherlands 🇳🇱 | `/nl-out` | 10014 | `hydra-proxy-nl` |
| Germany 🇩🇪 | `/de-out` | 10015 | `hydra-proxy-de` + `hydra-proxy-de-2` |

---

## 8. DNS Failover

### ip-watchdog (домашний сервер, российский ISP)
```
[Домашний сервер 78.107.88.21]
    ├── TCP+TLS probe → 45.91.54.152:443, каждые 5 мин
    │     fail × 3 → Cloudflare API PATCH (TTL=60) → A ru.goida.fun = 45.91.53.93
    └── POST https://ru.goida.fun/notify → relay → Telegram алерт
```

- State: `/var/lib/ip-watchdog/state` — текущий активный IP
- Manual override: `/var/lib/ip-watchdog/state.manual` — если задан, watchdog DNS не меняет (⚠️ сейчас активен)
- AUTO_RECOVERY: возвращает на primary когда восстановился

---

## 9. RKN-мониторинг (rkn-checker)

- Домашний сервер (российский ISP), systemd timer каждые 10 мин
- Утилита `rkn-check` (pip `rkn-block-checker`): TCP+TLS probe с российского ISP
- Эндпоинты: `ru.goida.fun` (domain), `45.91.54.152` (primary), `45.91.53.93` (backup), fin, swe + youtube_media + telegram
- POST результатов → `https://ru.goida.fun/rknstatus` → vpn-bot сохраняет в `bot_settings`
- Алерт через `/notify` только при смене статуса
- Env: `PRIMARY_IP=45.91.54.152`, `BACKUP_IP=45.91.53.93` (исправлено 2026-06-14, было стale)

### Вердикты
| verdict | значение |
|---|---|
| `OK` | доступен |
| `TLS_BLOCK` + tcp_ok=true | TCP бьётся, TLS заблокирован (camouflage сертификат — сервер доступен) |
| `TLS_BLOCK` + tcp_ok=false | реальный блок |
| `TIMEOUT` | недоступен (блок или хост выключен) |
| `TCP_RESET` | DPI сбрасывает соединение |

---

## 10. Угроза: ТСПУ «сибирская» эвристика (июнь 2026)

REALITY **не взломан**. ТСПУ перешёл на поведенческий анализ входного хопа (соединение клиент→сервер).

Срабатывает при **всех трёх** одновременно:
1. **Подсеть первого хопа** в «подозрительных» (Selectel, Яндекс.Облако, Hetzner, DO, OVH…)
2. **TLS-fingerprint**: Chrome, Safari, iOS — подозрительны. Проходят: Firefox, Android OkHttp, Edge
3. **Поведение**: >3 параллельных TLS-коннектов к одному SNI с интервалом <~100мс за 60 сек

При совпадении: заморозка TLS **120 сек**. Смена fingerprint во время заморозки → бан **600 сек** на весь TLS к узлу.

**Для нашего стека:** защищает REALITY выход RU→foreign, но DPI смотрит на вход client→RU (TLS+gRPC). Главная переменная — **флагнутость AS** хостера входного хопа.

---

## 11. Инфраструктура мониторинга (бэклог)

| компонент | статус | где |
|---|---|---|
| Grafana | данные скопированы (`/opt/nomad/volumes/grafana`, 53MB), не запущена | SWE |
| Loki | данные скопированы (`/opt/nomad/volumes/loki`, 75MB), не запущена | SWE |
| Promtail | **active**, шлёт xray-access.log на SWE:3100 | RU |
| Nomad cluster | 3-node Raft, datacenter `goida` (ru/fin/swe), 1.7.7 | все ноды |

Grafana/Loki запуск отложен в бэклог. Цель: история посещённых сайтов по юзерам (родительский контроль).

Telegram proxy tunnel: `telegram-proxy-tunnel.service` — SSH -L `127.0.0.1:8888:127.0.0.1:8888` → FIN:17904. Нужен потому что Telegram API заблокирован у российского ISP; vpn-bot ходит через этот туннель.

---

## 12. Кастомные решения

| решение | зачем |
|---|---|
| HWID gate в подписке | защита от бесконтрольного шаринга ссылок |
| Browser HTML stub | не раскрывать `vless://` в браузере |
| Happ routing JSON в подписке | L1 клиентский роутинг — RU-трафик direct с устройства |
| home-exit (жилой IP) | банки/госуслуги/VPN-детекторы видят residential IP |
| /notify relay через RU | Telegram заблокирован у домашнего ISP |
| sync_hydra_remna.py | два источника истины (3X-UI SQLite и Remnawave Postgres) → мост при ротации UUID |
| itdog_geosite.dat | расширенный RU geo-список (cron обновляет ежедневно в 05:00, `docker restart remnanode`) |
| zapret2 YouTube QUIC block | форсируем TCP/TLS на YouTube чтобы zapret desync работал предсказуемо |
| ipleak → direct | IP-чекеры видят нужный IP |
| ru-via-home (65 доменов) | банки/маркетплейсы/госуслуги через жилой RU-IP |
| reserve VPS (194.117.80.94) | полностью независимый резервный вход, не зависит от RU при блокировке |
| Telegram proxy tunnel RU→FIN | vpn-bot ходит в Telegram через SSH-туннель на FIN (обход блока TG) |

---

## 13. Открытые технические долги

| проблема | приоритет |
|---|---|
| **GOIDA_SMART2 xHTTP iOS** — iOS Happ не инициирует xHTTP-коннект. Нужно gRPC+REALITY | высокий |
| **ip-watchdog manual override** (`state.manual` активен) — автофейловер заморожен | средний |
| **backup_ip 45.91.53.93 TIMEOUT** с домашнего ISP — маршрутная проблема рос. сетей к этому IP | средний |
| **Два источника истины** (3X-UI SQLite + Remnawave Postgres) — sync_hydra_remna.py лечит симптом | средний |
| **VLESS flow-патч** внутри контейнера remnawave — при `compose pull` снесётся | средний |
| **RU4_PRIVATE на FIN слушает 0.0.0.0:17905** без шифрования — нужен `127.0.0.1` или firewall | низкий |
| **Legacy x-ui на reserve** — второй xray + панель :2096/:25565 не используются | низкий |
| **heartbeatPeriod:30** — не проставлен на всех live WS inbounds | низкий |
| **Grafana/Loki не запущены** на SWE (данные есть, Nomad job написан) | бэклог |
| Нет единого `events_log` для подписок, устройств, оплат, watchdog | бэклог |

---

## 14. Файловая карта (live серверы)

### RU `45.91.54.152`
```
/root/vpn-bot/
  vpn-bot.py              — управляющий бот (stdlib-only)
  bot.db                  — SQLite (users, devices, settings, client_*)
  subscription_engine/    — пакет генерации подписок

/opt/goida-client/
  client-bot/client-bot.py  — клиентский бот + Mini App API (stdlib-only)
  client-web/index.html     — SPA клиентского приложения

/opt/sub-updater/
  updater.py              — синхронизация hydra (legacy 3X-UI)
  sync_hydra_remna.py     — мост 3X-UI → Remnawave
  whitelist_links.txt     — авто-whitelist
  whitelist_manual.txt    — ручной whitelist
  config.env              — оверрайд SUB_URL/SUB_UA

/opt/wl-registry/wl-list.txt   — whitelist registry (отдаёт nginx /wl/list)

/opt/zapret2/             — обфускатор DPI
  config                  — конфиг nfqws2
  ipset/list-google.txt   — YouTube IP-список

/etc/x-ui/x-ui.db        — legacy 3X-UI SQLite (hydra, xrayTemplateConfig)
/root/xray-config-from-old-ru.json  — эталон списков доменов/IP (53KB, старый RU)

Remnawave (docker):
  remnawave      — backend панели
  remnawave-db   — PostgreSQL 17.6
  remnawave-redis — Valkey 9
  remnanode      — Xray агент (управляет нодами fin/fra/swe/home/reserve)
```

### Reserve `194.117.80.94`
```
/etc/xray/reserve-fin.json     — конфиг основного xray (GOIDA_RESERVE → FIN через туннель)
/root/.ssh/ru4_fin_tunnel      — ключ SSH-туннеля reserve→FIN
xray-reserve-fin.service       — основной xray, active
ru4-fin-tunnel.service         — SSH-туннель :17905 → FIN:17905, Restart=always
x-ui (legacy, disabled)        — :2096/:25565, не используется
```

### FIN `77.110.108.57`
```
/etc/xray/ru4-egress.json      — egress для reserve (RU4_PRIVATE :17905 → DIRECT)
/opt/vpndeployer/              — deployer-bot (docker compose)
```

### Домашний сервер `192.168.31.176` (RU ISP)
```
/opt/ip-watchdog/watchdog.py       — DNS failover
/etc/ip-watchdog/watchdog.env      — CF_TOKEN, PRIMARY_IP, BACKUP_IP
/var/lib/ip-watchdog/state         — текущий активный IP
/var/lib/ip-watchdog/state.manual  — ручной override (сейчас активен)

/opt/rkn-checker/rkn-checker.py    — мониторинг доступности с рос. ISP
/etc/rkn-checker/rkn-checker.env   — PRIMARY_IP=45.91.54.152, BACKUP_IP=45.91.53.93
/var/lib/rkn-checker/state.json    — последние вердикты по эндпоинтам
```

### SWE `89.22.230.5`
```
/opt/nomad/volumes/grafana/   — данные Grafana (53MB, не запущена)
/opt/nomad/volumes/loki/      — данные Loki (75MB, не запущена)
```
