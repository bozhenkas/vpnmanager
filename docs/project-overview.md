# goida vpn — описание проекта

`goida vpn` — self-hosted VPN-сервис для небольшой family/community-аудитории. Проект объединяет VPN-инфраструктуру, Telegram management bot, клиентский Telegram Mini App, подписочный сервер и набор фоновых диагностик для устойчивой работы в российских сетях.

Главная идея: пользователь получает простую подписку и управляет доступом через Telegram, а сложная логика маршрутизации, устройств, серверов, оплат и обхода блокировок остаётся на стороне сервиса.

## Что входит в проект

### VPN-инфраструктура

Основной входной сервер находится в RU-сегменте и принимает клиентские подключения через Xray/3X-UI. Дальше трафик маршрутизируется по разным выходам:

- `smart` — основной “умный” профиль, сейчас ориентирован на самый быстрый доступ через Finland с Sweden как fallback.
- `fin` — выход через Финляндию.
- `swe` — выход через Швецию.
- `ru-zapret` / `direct` — российский прямой профиль с zapret-логикой, удобен для ТВ и российских сервисов.
- Hydra countries — дополнительные внешние страны из внешней подписки.
- `home` — домашний российский IP для отдельных сайтов, которым нужен именно домашний/российский маршрут.

Источник истины для Xray-конфига — `xrayTemplateConfig` в SQLite 3X-UI. Файл `config.json` считается generated output, его нельзя редактировать как главный конфиг.

### Telegram management bot

Основной bot — single-file Python без внешних pip-зависимостей. Он управляет пользователями, токенами, подписками, серверными предпочтениями, ручной маршрутизацией сайтов и админскими операциями.

Возможности:

- создание и удаление VPN-пользователей;
- выдача и регенерация subscription URL;
- управление лимитом устройств;
- paid-until, free access, продления;
- inline user card для админских операций;
- ручная маршрутизация сайтов в `direct`, `home`, `foreign`;
- включение legacy V2Ray-роута только по явному флагу;
- сбор телеметрии устройств и запросов подписки.

Live-путь на сервере: `/root/vpn-bot/vpn-bot.py`.

### Subscription engine

Подписочный сервер обслуживает `/subscribe/<token>` и отдаёт разные ответы по типу клиента.

Текущая модель:

- Happ или клиент со стабильным HWID получает реальные ноды.
- Browser-like запрос получает branded HTML-заглушку.
- Клиент без стабильного HWID получает fake unsupported-ноды.
- Clash получает unsupported fallback YAML.
- Legacy plain base64 включается только через явный per-user флаг.

Это защищает подписку от бесконтрольного копирования и позволяет вести учёт реальных устройств.

### Client Mini App

Mini App — клиентский интерфейс внутри Telegram. Он обслуживается отдельным `goida-client-bot` и nginx на `web.goida.fun`.

Основные экраны:

- `мой ключ` — статус подписки, ссылка, подключение через Happ, трафик и список устройств;
- `оплата` — paid-until, напоминание об оплате, лимит устройств, заявка на смену тарифа;
- `сервера` — включение/выключение доступных серверов.

Доступ в Mini App закрыт:

- API принимает только валидный Telegram WebApp `initData`;
- подпись HMAC обязательна;
- `auth_date` ограничен TTL;
- Telegram user id должен быть связан с VPN username в `client_tg_links`.

### sub-updater

`sub-updater` синхронизирует внешние подписки и Hydra-страны с Xray template.

Задачи:

- получать source subscription;
- обновлять outbounds/routing в `xrayTemplateConfig`;
- поддерживать whitelist;
- чистить исчезнувшие Hydra-страны из routing/outbounds и пользовательских prefs;
- не трогать manual bot routing rules.

Live-путь: `/opt/sub-updater/updater.py`.

### RKN / TSPU / watchdog diagnostics

Проект содержит диагностики, которые проверяют не только “пингуется ли”, а реальный сетевой путь:

- TCP connect;
- TLS handshake;
- HTTP request;
- YouTube media probe;
- primary/backup IP checks;
- отдельные статусы domain / primary IP / backup IP / media quality.

Цель — меньше ложных алертов и быстрее понимать, где именно деградировал путь.

### zapret

На RU-сервере используется zapret2/nfqws2 для обхода DPI. Для YouTube применяется отдельная логика: UDP/443 для YouTube блокируется на уровне Xray для нужных inbounds, чтобы клиент уходил в TCP/TLS, где zapret работает предсказуемее.

## Пользовательский сценарий

1. Пользователь получает доступ через Telegram.
2. Telegram ID связывается с VPN username.
3. В Mini App пользователь видит подписку, устройства и серверы.
4. Кнопка `подключить` ведёт через HTTPS bridge `/happ/<token>`, который открывает Happ или показывает ручную инструкцию.
5. Happ запрашивает `/subscribe/<token>` с HWID.
6. Subscription engine проверяет тип клиента, HWID и лимиты.
7. Если всё ок — отдаёт реальные профили и routing.
8. Если лимит устройств заполнен — отдаётся заглушка с предложением докупить устройства в боте.

## Админский сценарий

Админ работает в Telegram-боте, без ручных SQL-правок для обычных операций:

- открыть user card через `/user <username>` или список пользователей;
- продлить подписку;
- изменить лимит устройств;
- включить/выключить free access;
- посмотреть устройства;
- удалить дубликаты/старые устройства;
- управлять ручной маршрутизацией сайтов;
- смотреть статистику и логи.

Все чувствительные операции должны логироваться в `admin_log` или аналогичную таблицу.

## Основные данные и таблицы

В bot DB используются:

- `users` — VPN users, токены, базовые флаги;
- `client_tg_links` — связь Telegram user id с VPN username;
- `client_profiles` — paid-until, device limit, free access, reminder toggle;
- `user_devices` — реальные устройства по HWID/User-Agent;
- `client_server_prefs` — включённые/выключенные серверы;
- `client_plan_requests` — заявки на изменение тарифа;
- `client_reminders` — дедупликация напоминаний;
- `sub_requests_log` — запросы подписки;
- `admin_log` — действия администратора.

В 3X-UI SQLite важны:

- `settings.xrayTemplateConfig` — главный Xray template;
- `inbounds.stream_settings` — WS stream settings, включая `heartbeatPeriod: 30`;
- `inbounds.sniffing` — отдельная колонка, не часть stream settings.

## Безопасность

Ключевые правила проекта:

- не выдавать реальные ноды браузерам и клиентам без стабильного HWID;
- Mini App API доступен только через валидный Telegram WebApp initData;
- Telegram user id должен быть явно связан с VPN username;
- legacy V2Ray plain base64 не должен быть default-путём;
- токены подписки не логируются целиком, только hash;
- IP-адреса в логах хешируются;
- перед мутацией Xray template нужен backup;
- manual routing rules пользователя нельзя удалять при авто-cleanup Hydra.

## Наблюдаемость

Проект постепенно движется к модели, где каждое важное событие видно:

- запросы подписки;
- тип клиента и наличие HWID;
- response type: real/fake/html/legacy;
- watchdog verdicts;
- TSPU статусы;
- admin actions;
- device activity;
- plan/reminder события.

Цель — быстро отвечать на вопросы вроде “почему у пользователя пусто в Happ”, “почему скорость упала”, “какой клиент забрал подписку” и “кто поменял маршрут сайта”.

## Live-компоненты

Основной RU-сервер:

- domain: `ru.goida.fun`;
- Mini App/API: `web.goida.fun`;
- subscription endpoint: `https://ru.goida.fun/subscribe/<token>`;
- bot: `/root/vpn-bot/vpn-bot.py`;
- bot DB: `/root/vpn-bot/bot.db`;
- client bot: `/opt/goida-client/client-bot/client-bot.py`;
- client web: `/opt/goida-client/client-web/index.html`;
- sub-updater: `/opt/sub-updater/updater.py`;
- X-UI DB: `/etc/x-ui/x-ui.db`.

## Текущие технические принципы

- Python 3.12 и stdlib-first.
- Минимум внешних зависимостей в live bot.
- Ручные изменения live-конфигов делать через backup.
- При изменении Xray routing сначала проверять `xrayTemplateConfig`.
- Не трогать пользовательские manual routing rules авто-скриптами.
- После значимых изменений обновлять `.Codex/wings/*/changelog.md`.
- Если одна и та же ошибка повторилась дважды, сначала изучить 3-5 способов исправления, затем выбрать лучший и внедрить.

## Открытые долги

- Довести `heartbeatPeriod: 30` на всех live WS inbounds до автоматического/idempotent состояния.
- Сделать единый `events_log` для подписок, устройств, оплаты, админских действий и watchdog.
- Добавить preflight validator для Xray template перед записью в 3X-UI.
- Улучшить dashboard по нодам, трафику, ошибкам подписок и HWID.
- Расширить отчёты по устройствам и причинам отказа в подписке.
- Формализовать rescue CLI для восстановления template/бота без ручного ковыряния.

