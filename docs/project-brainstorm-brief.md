# goida vpn — безопасный бриф для брейншторма

> Документ намеренно не содержит токенов, ключей, IP-адресов, приватных URL панелей, паролей, UUID клиентов, subscription tokens, SSH-параметров и иных секретов.  
> Цель: дать другой модели полную картину логики проекта для развития продукта и архитектуры без доступа к чувствительным данным.

## 1. Коротко о проекте

`goida vpn` — self-hosted VPN-сервис для небольшой закрытой аудитории.

Проект состоит из нескольких связанных частей:

- основной Telegram-бот для управления VPN-пользователями и подписками;
- отдельный клиентский Telegram-бот и Telegram Mini App;
- Xray/3X-UI слой с VLESS/WS/TLS входами;
- nginx как публичный HTTPS/WebSocket edge;
- subscription engine для выдачи подписок Happ/V2Ray-compatible клиентам;
- `sub-updater`, который подтягивает внешние серверы/whitelist/Hydra и пересобирает Xray routing;
- zapret/TSPU обход для RU direct/smart сценариев;
- домашний RKN/IP watchdog для проверки блокировок и DNS failover;
- задел под deployer-bot и кластерное управление.

Проект ориентирован не на массовый SaaS, а на приватный family/community VPN: закрытый доступ, ручное администрирование, высокий контроль над серверами, подписками и устройствами.

## 2. Основные принципы

- Доступ к клиентскому Mini App только внутри Telegram.
- Доступ только для своих: Telegram user id должен быть связан с VPN username.
- Основной источник правды для Xray — `xrayTemplateConfig` в 3X-UI sqlite.
- Для Xray routing нельзя полагаться на сгенерированный `config.json` как source of truth.
- Клиентская подписка должна быть Happ-first.
- Старые/unsupported клиенты не должны получать реальные ноды без стабильного HWID.
- Пользовательские server toggles не должны требовать рестарта Xray.
- Ручные правила администратора имеют приоритет над автоматикой.
- Любая автоматика вокруг ТСПУ должна отличать реальную блокировку от случайного таймаута.

## 3. Runtime-состав

### Основной серверный слой

На основном VPN-сервере сейчас активны:

- Xray через 3X-UI;
- nginx edge;
- основной Telegram management bot;
- `sub-updater`;
- отдельный клиентский бот/Mini App API;
- zapret service;
- smart-pro service;
- Nomad agent.

Состояние при последней проверке: ключевые сервисы активны, диск в норме.

### Домашний мониторинг

Отдельный домашний сервер используется как “глаз клиента из российского провайдера”.

На нем работают:

- `rkn-checker.timer` — доменная RKN/TSPU проверка;
- `ip-watchdog.timer` — primary-first IP failover checker.

Этот узел важен, потому что проверка с датацентра может не видеть ТСПУ-поведение residential/mobile провайдеров.

## 4. Основной Telegram management bot

Основной бот — single-file Python без внешних pip-зависимостей в runtime. Он:

- управляет пользователями;
- хранит пользователей и токены в sqlite;
- обслуживает HTTP subscription server на localhost;
- пишет device telemetry;
- отдает подписки через nginx;
- принимает RKN status/notify webhook от домашнего мониторинга;
- содержит админские команды и inline UI для user cards.

### Модель пользователя

В основной базе есть:

- `users`: VPN username, subscription token, даты, флаги, Telegram id, parsing-флаг;
- `user_ips`: legacy учет IP;
- `user_devices`: новая телеметрия устройств;
- `deleted_subs`: fallback/история удаленных подписок;
- `bot_settings`: feature flags и служебные настройки;
- client-specific таблицы для Mini App.

### Device telemetry

При запросах подписки бот извлекает из Happ/User-Agent/headers:

- device id;
- приложение и версию;
- платформу;
- версию платформы;
- device name;
- first seen;
- last seen;
- client ip;
- source.

Устройства используются в Mini App для отображения и отвязки. В 3X-UI hard limit по IP отключен; контроль устройств живет на уровне бота и UX.

## 5. Subscription engine

Основной публичный маршрут подписок сохранил старые ссылки, но внутри переведен на новый локальный `subscription/` engine.

### Поведение `/subscribe/<token>`

Логика зависит от типа клиента:

- Happ или запрос со стабильным HWID получает реальные ноды.
- Browser-like запрос получает брендированную HTML-заглушку, а не raw base64.
- Клиент без стабильного HWID получает fake unsupported ноды с текстом “клиент не поддерживается / скачайте Happ”.
- Clash route возвращает unsupported fallback без реальных нод.
- JSON route существует, но основная продуктовая линия — Happ.

### Legacy route

Есть legacy V2Ray/plain-base64 route:

- включается только по per-user флагу;
- отдает plain base64 VLESS;
- не имеет JSON варианта;
- нужен для совместимости, но не должен быть default.

### Happ routing

Happ получает routing metadata:

- через HTTP header;
- через строку в body;
- с корректным ключом `DirectIp`;
- с RU/private direct rules;
- с блокировкой YouTube QUIC на уровне Xray, чтобы клиент уходил в TCP/TLS.

## 6. Xray / 3X-UI слой

Есть базовые VLESS WS/TLS входы:

- FI profile;
- SE profile;
- smart profile;
- RU direct/zapret profile;
- personal smart-pro profile;
- Hydra country profiles;
- отдельный test inbound для xhttp.

Важно: у live WS inbounds все еще отсутствует `heartbeatPeriod: 30` в sqlite `stream_settings`. Это остается maintenance-задачей.

### Outbounds

Есть:

- direct;
- direct-zapret;
- home exit;
- blocked;
- FI/SE proxy;
- DNS outbound;
- smart-pro socks outbound;
- Hydra country outbounds;
- дополнительный outbound для второго DE;
- balancer для DE.

### Balancers

Есть два ключевых balancer-паттерна:

- smart balancer для FI/SE;
- country balancer для Hydra DE, когда источник дает несколько серверов одной страны.

## 7. Routing

Основные правила:

- bittorrent уходит в blocked;
- private IP ranges уходят direct;
- YouTube UDP/443 для smart/direct блокируется, чтобы форсировать TCP/TLS fallback;
- direct/zapret inbound уходит в direct-zapret;
- smart и smart-pro имеют выборочные RU/home/manual правила;
- RU/IP leak domains уходят direct;
- country inbounds уходят в соответствующие country outbounds;
- smart catch-all уходит в smart balancer;
- socks-proxy country inbounds уходят в соответствующую страну;
- manual rules сохраняются поверх автоматических.

Критично: не добавлять port 53 rules в общий routing array, кроме smart-pro/DNS-specific сценария.

## 8. sub-updater

`sub-updater` отвечает за внешний источник серверов и whitelist.

Он:

- периодически получает внешний subscription/registry;
- извлекает WL configs;
- deep-checks WL через временный Xray/SOCKS путь;
- требует несколько успешных HTTPS probes;
- исключает плохие WL;
- собирает live whitelist file;
- определяет активные Hydra страны;
- группирует несколько серверов одной страны;
- создает stable outbound tags;
- создает country balancers при дублях;
- сохраняет manual bot routing rules;
- сохраняет generated YouTube QUIC block rule;
- обновляет Xray template только если есть реальные изменения;
- чистит stale client server prefs, если Hydra country исчезла из актуальной подписки.

### Hydra logic

Hydra countries сейчас появляются в web app только если:

- соответствующий inbound существует и активен;
- у конкретного пользователя в x-ui есть enabled client для этого country inbound;
- страна есть в актуальной source subscription;
- per-user preference в Mini App не отключает сервер.

Если страна исчезает из source subscription, она должна пропасть:

- из Xray routing/outbounds;
- из user-facing Mini App;
- из `client_server_prefs`.

## 9. Zapret / TSPU логика

Zapret используется для RU direct/smart сценариев.

Текущий подход:

- узкий YouTube-specific desync;
- отдельные правила для QUIC UDP/443 и TLS TCP/443;
- Google/YouTube hostlist очищен от доменов, которые не должны попадать в YouTube list;
- Xray дополнительно блокирует YouTube UDP/443 для нужных inbounds, чтобы Happ/клиент перешел на TCP/TLS;
- диагностика YouTube должна строиться на реальных media/player/yt-dlp проверках, а не на ping/generate_204.

Важно: короткий HTTP probe может проходить, а медиа потом деградировать. Для YouTube это уже наблюдалось.

## 10. RKN checker и IP failover

Есть две разные задачи:

### rkn-checker

Доменный мониторинг доступности публичных endpoints.

Он:

- использует `rkn-block-checker`-style CLI;
- проверяет домены как обычный пользователь;
- пишет статус в основной бот;
- отправляет alert только при изменении статуса;
- теперь подтверждает bad status несколькими попытками, чтобы один случайный timeout не создавал ложный “заблокирован”.

### ip-watchdog

DNS failover для основного домена.

Ключевая логика:

- всегда primary-first;
- каждый запуск сначала проверяет primary IP;
- если primary живой, DNS возвращается/удерживается на primary даже если сейчас выбран backup;
- backup проверяется только если primary не прошел retry threshold;
- DNS переключается на backup только если backup сам проходит проверку;
- если оба не проходят, DNS не меняется и отправляется аварийный alert.

Проверка не ping-based:

- TCP connect к конкретному IP;
- TLS handshake с SNI публичного домена;
- HTTP request с Host публичного домена;
- проверка HTTP 451;
- проверка ISP/block stub markers;
- verdict categories mirror `rkn-block-checker`: `OK`, `TIMEOUT`, `TCP_RESET`, `TLS_BLOCK`, `HTTP_STUB`, `DOWN`.

Такой подход нужен, потому что:

- `rkn-check --url domain` проверяет текущую DNS-запись, а не обязательно primary;
- `rkn-check --url ip` теряет SNI/Host, а ТСПУ обычно режет доменное TLS/HTTP поведение.

## 11. Клиентский Telegram bot + Mini App

Отдельный клиентский бот не является основным management bot.

Он включает:

- Telegram polling;
- static Mini App;
- localhost API;
- admin inline panel;
- payment reminders.

Внешний доступ к Web App идет через отдельный HTTPS домен и nginx proxy на локальный API.

### Mini App access

Mini App доступен только:

- внутри Telegram;
- с валидным `initData`;
- если Telegram id связан с VPN username.

Если пользователь не привязан, он видит закрытую ошибку.

### Mini App sections

Есть три раздела:

1. `мой ключ`
2. `оплата`
3. `сервера`

#### мой ключ

Показывает:

- статус подписки;
- короткий key id формата `da-...`;
- protocol chip `ws + TLS`;
- subscription URL;
- copy icon;
- connect button;
- download block for Happ;
- device list;
- unbind device action with confirmation.

Download block:

- определяет платформу по user-agent;
- дает tabs: iOS, Android, Windows, macOS, Apple TV, Android TV;
- отдельно показывает download action/buttons.

#### оплата

Показывает:

- paid-until date;
- payment day;
- current plan;
- requested plan;
- plan slider from 2 to 7 devices;
- base plan and extra-device price;
- request plan change button;
- confirmation sheet before request.

Для free-access users показывается сообщение, что VPN предоставляется бесплатно, без обычного tariff flow.

#### сервера

Показывает server toggles:

- smart;
- FI;
- SE;
- RU-zapret;
- enabled Hydra countries for that user.

Текст объясняет, что лишние сервера можно отключить для удобства в Happ.

### Client API

Endpoints:

- `GET /api/me`
- `POST /api/devices/unbind`
- `POST /api/plan/request`
- `POST /api/servers/toggle`

Все API-запросы требуют Telegram WebApp initData.

### Client DB tables

- `client_profiles`;
- `client_tg_links`;
- `client_invite_tokens`;
- `client_plan_requests`;
- `client_server_prefs`;
- `client_reminders`.

### Admin features

Через slash commands и inline admin panel:

- рассылка;
- привязка Telegram пользователя;
- отвязка пользователя;
- установка paid-until даты;
- добавление оплаченных месяцев;
- изменение device limit;
- free access on/off;
- просмотр user card;
- отправка payment reminders;
- approve/reject plan change requests.

### Payment model

Текущая логика:

- minimum: 2 devices;
- maximum: 7 devices;
- base price for 2 devices;
- each extra device adds fixed amount;
- user requests tariff change;
- admin approves/rejects;
- approved request updates `client_profiles.device_limit`.

Reminder:

- payment day is fixed;
- reminders de-duplicated by username + date;
- admin can force reminders manually.

## 12. Web style / UX

Mini App style follows the light goida subscription stub:

- light blue background;
- large goida logo;
- Inter regular/medium;
- negative letter spacing;
- restrained cards and rounded controls;
- no heavy bold everywhere;
- tabs with swipe transitions;
- bottom sheet confirmations;
- icon copy button;
- server toggles;
- platform download tabs.

Known UX direction:

- keep all key client actions on one screen with sections;
- avoid copying Hydra’s four-screen complexity;
- make family/community users able to self-serve devices, payment status, and server selection.

## 13. Deployer / cluster roadmap

There is a separate deployer-bot nested repo.

Known state:

- async Telegram bot stack;
- Redis/Postgres/Docker-oriented deployment architecture;
- direct scenario exists;
- cascade scenario skeleton exists;
- unit tests exist;
- CI workflow exists.

Open direction:

- simplified management bot for cascade nodes;
- cluster expansion/add nodes;
- documentation site;
- package current proven RU logic into repeatable deployments.

## 14. Current repo organization

Active code areas:

- `bot/` — main management bot working copy;
- `subscription/` — current subscription engine;
- `sub-updater/` — current updater logic;
- `client-bot/` — client Telegram bot/API;
- `client-web/` — Telegram Mini App;
- `ip-watchdog/` — home-server RKN/IP monitoring;
- `deploy/` — nginx/systemd/nomad/zapret assets;
- `web/` — older web/static assets;
- `research/` — research notes/vendor investigations;
- `legacy/` — archived old code.

Memory:

- `.Codex/` is current operational memory.
- `.claude/` is archive-only and may contain stale data and secrets.
- Do not copy raw `.claude` content into public docs or model handoffs.

## 15. Known risks and open tasks

### High priority

- Keep Mini App access strict: Telegram-only and allowlisted user IDs.
- Keep main subscription protected from unsupported clients without HWID.
- Monitor RKN/IP failover for false positives after retry/primary-first changes.
- Add WS `heartbeatPeriod: 30` to live WS inbounds during planned maintenance.
- Continue monitoring sub-updater Hydra country cleanup and whitelist deep-check behavior.

### Product/UX

- Make admin payment/date/device operations pleasant enough to replace manual DB edits.
- Improve user-facing wording around paid date, free access, device limit, and server toggles.
- Consider hiding advanced server labels unless user needs them.
- Add clearer “what changed” state after plan request approval.

### Infra

- Formalize backups before any Xray template mutation.
- Add dry-run/report mode for `sub-updater` changes.
- Build better observability for subscription requests, device growth, and disabled servers.
- Ensure Nomad/cluster work does not conflict with current systemd-critical services.

### TSPU diagnostics

- Maintain separate notions of:
  - domain status;
  - concrete primary IP status;
  - concrete backup IP status;
  - client media-path quality.
- Do not treat one timeout as proof of block.
- For YouTube/media, use real media/player tests, not only handshake/generate_204.

## 16. Brainstorming prompts for another model

Useful prompts to explore:

1. How should a family VPN Mini App balance simplicity and power-user server toggles?
2. What admin workflows should move from slash commands to inline UI first?
3. How to design a clean domain model around users, devices, paid periods, subscriptions, and server preferences?
4. How to split the monolithic management bot into modules without breaking no-external-pip constraints?
5. How to make `sub-updater` safer: dry-run, diff preview, rollback, alerting?
6. How to represent TSPU status as product state: normal, degraded, primary blocked, backup active, both unhealthy?
7. How to evolve payment reminders without turning the bot into a billing system?
8. How to package this as a repeatable deployment for future nodes/community servers?
9. How to expose enough observability to the owner without leaking sensitive infra details to users?
10. How to keep Happ-first while preserving limited escape hatches for legacy clients?

## 17. Non-goals / constraints

- Do not expose secrets in UI, docs, logs, or handoff prompts.
- Do not make the Mini App public outside Telegram.
- Do not make unsupported clients first-class unless deliberately approved.
- Do not restart Xray for simple user server toggles.
- Do not let automatic rules overwrite manual admin routing rules.
- Do not trust `.claude` as current source of truth without checking `.Codex` and live state.

