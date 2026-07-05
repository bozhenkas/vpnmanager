# remnawave rewrite spec + text catalog

Цель: переехать с прямой работы с 3X-UI SQLite на Remnawave, но сохранить goida-логику подписок, Mini App, оплат, устройств, fake-профилей и branded browser page.

Документ специально написан как ТЗ для переписывания под себя. Remnawave здесь используется как backend/source of truth для пользователей, нод, squads, traffic и config profiles. Всё, что Remnawave не умеет в твоей модели, остаётся в собственном слое.

## 1. Что забираем из Remnawave

### Native Remnawave

- users: uuid, username, short uuid, subscription uuid/url, status, expireAt, traffic limits.
- nodes: online/offline, адрес, порт node API, метрики, ошибки push.
- internal squads: доступ пользователя к наборам inbounds/серверов.
- config profiles: полный Xray template, валидация, push на ноды.
- hosts/inbounds: то, что раньше жило в `inbounds` 3X-UI.
- traffic stats: usage по пользователю/нодам.
- API tokens: токен для твоего bot/subscription/Mini App backend.
- node keygen: корректный `SECRET_KEY` payload для remnawave-node.

### Что не отдаём Remnawave

- `/subscribe/<token>` как публичный endpoint.
- fake-ноды для браузера/неподдерживаемых клиентов/удалённых/expired/overlimit.
- branded HTML page с твоим сайтом.
- tombstone token для удалённых пользователей.
- device telemetry в твоём формате.
- per-user manual routing.
- `custom_sub` append-only links.
- Mini App и тарифная бизнес-логика.
- payment reminders.
- legacy V2Ray switch.
- watcher/TSPU/zapret diagnostics.

## 2. Главная архитектура

```text
Telegram bot / Mini App / Subscription Server
        |
        | local business db: bot.db
        | users, tokens, payments, devices, prefs, overrides
        v
RemnawaveAdapter
        |
        | HTTP API / SDK
        v
Remnawave Panel + Postgres
        |
        | pushes xray config
        v
remnawave-node(s)
```

Важно: пользователь снаружи продолжает видеть твой URL:

```text
https://ru.goida.fun/subscribe/<token>
https://ru.goida.fun/happ/<token>
```

Remnawave subscription URL не отдаётся пользователю напрямую. Его можно использовать только как upstream source внутри твоего subscription server, если это удобно.

## 3. Backend abstraction

В коде должен появиться слой, чтобы legacy и remna жили параллельно.

```python
class VpnBackend:
    name: str

    def get_user(self, username: str) -> BackendUser | None: ...
    def get_user_by_token(self, token: str) -> BackendUser | None: ...
    def create_user(self, username: str, *, expire_at: str | None, traffic_limit: int | None) -> BackendUser: ...
    def delete_user(self, username: str) -> None: ...
    def disable_user(self, username: str) -> None: ...
    def set_expire_at(self, username: str, expire_at: str | None) -> None: ...
    def set_traffic_limit(self, username: str, bytes_total: int | None) -> None: ...
    def get_traffic(self, username: str) -> BackendTraffic: ...
    def get_nodes_for_user(self, username: str) -> list[BackendNodeLink]: ...
    def set_server_enabled(self, username: str, server_key: str, enabled: bool) -> None: ...
    def get_available_servers(self, username: str) -> list[ServerItem]: ...
```

### BackendUser

```python
@dataclass
class BackendUser:
    username: str
    backend: str  # legacy | remnawave
    remna_uuid: str = ""
    subscription_uuid: str = ""
    subscription_url: str = ""
    status: str = "active"  # active | disabled | expired | deleted
    expire_at: str = ""
    traffic_limit: int = 0
```

### BackendNodeLink

```python
@dataclass
class BackendNodeLink:
    server_key: str       # smart, fi, se, zapret, hydra:de
    display_name: str
    link: str             # vless://...
    outbound_tag: str = ""
    country: str = ""
```

## 4. Локальная база

Remnawave не заменяет `bot.db` полностью. Она заменяет только x-ui часть.

### users

Добавить:

```sql
ALTER TABLE users ADD COLUMN backend TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE users ADD COLUMN remna_user_uuid TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN remna_subscription_uuid TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE users ADD COLUMN deleted_at TEXT NOT NULL DEFAULT '';
```

`users.token` остаётся публичным стабильным subscription token. Не путать с Remnawave subscription uuid.

### client_profiles

Остаётся:

```sql
username TEXT PRIMARY KEY
device_limit INTEGER NOT NULL DEFAULT 2
paid_until TEXT NOT NULL DEFAULT ''
free_access INTEGER NOT NULL DEFAULT 0
payment_reminders_enabled INTEGER NOT NULL DEFAULT 1
updated_at TEXT NOT NULL
```

Правило: `client_profiles.paid_until` = бизнес-истина для твоего UX, Remnawave `expireAt` = техническая копия, которую синхронизирует adapter.

Если хочешь наоборот, надо явно написать:

```text
paid_until = mirror(remnawave.expireAt)
```

Но смешивать нельзя.

### user_devices

Остаётся локальным источником для красивого UX и fake-ответов:

```sql
token TEXT NOT NULL
device_id TEXT NOT NULL
first_seen TEXT NOT NULL
last_seen TEXT NOT NULL
client_ip TEXT NOT NULL DEFAULT ''
user_agent TEXT NOT NULL DEFAULT ''
app_name TEXT NOT NULL DEFAULT ''
app_version TEXT NOT NULL DEFAULT ''
platform TEXT NOT NULL DEFAULT ''
platform_version TEXT NOT NULL DEFAULT ''
device_name TEXT NOT NULL DEFAULT ''
source TEXT NOT NULL DEFAULT 'subscription'
PRIMARY KEY (token, device_id)
```

Не включать strict HWID в Remnawave на первом этапе. Иначе Remnawave будет отдавать 404 раньше твоего branded layer.

### client_server_prefs

Остаётся UX-истиной:

```sql
username TEXT NOT NULL
server_key TEXT NOT NULL
enabled INTEGER NOT NULL DEFAULT 1
updated_at TEXT NOT NULL
PRIMARY KEY (username, server_key)
```

Adapter может зеркалить это в Internal Squads:

```text
server_key smart     -> squad goida-smart
server_key fi        -> squad goida-fi
server_key se        -> squad goida-se
server_key zapret    -> squad goida-zapret
server_key hydra:de  -> squad goida-hydra-de
```

### deleted_subs

Обязательная tombstone-таблица:

```sql
CREATE TABLE IF NOT EXISTS deleted_subs (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT 'deleted'
);
```

Удалённый пользователь не должен получать `404`, если токен когда-то был валиден. Он должен получать fake profile с понятным текстом.

### user_routing_overrides

Если нужен настоящий per-user routing:

```sql
CREATE TABLE IF NOT EXISTS user_routing_overrides (
    username TEXT NOT NULL,
    domain TEXT NOT NULL,
    target TEXT NOT NULL, -- direct | home | foreign | auto
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (username, domain)
);
```

Пока можно оставить global manual routing из Config Profile, но это не per-user.

## 5. Subscription state machine

Вход:

```text
GET /subscribe/<token>
GET /subscribe/<token>/json
GET /subscribe/<token>/clash
GET /subscribe-old/<token>
GET /happ/<token>
```

Контекст:

```python
ctx = {
    "token": token,
    "user": local_user_or_none,
    "deleted": deleted_sub_or_none,
    "client_type": browser | happ | legacy | clash | unknown,
    "hwid": hwid_or_empty,
    "device_count": N,
    "device_limit": M,
    "paid": bool,
    "backend_status": active | disabled | expired | deleted,
}
```

### Decision table

| Условие | Ответ |
|---|---|
| token unknown, never existed | HTTP 404 plain `404` |
| token in deleted_subs | fake subscription `deleted` |
| browser-like request | branded HTML page, не реальные links |
| `/happ/<token>` | HTML bridge, пытается открыть Happ |
| active user, paid/free, Happ/HWID ok, devices ok | real subscription |
| active user, paid/free, no HWID | fake subscription `unsupported_client` |
| active user, paid/free, device limit exceeded | fake subscription `device_limit` |
| active user, unpaid/expired | fake subscription `expired` |
| active user disabled by admin | fake subscription `disabled` |
| clash branch | fake/unsupported YAML unless explicitly supported |
| legacy branch disabled | fake unsupported or 404 old-json |
| legacy branch enabled | plain base64 VLESS without Happ metadata |

### Priority order

1. Unknown/tombstone token.
2. Browser/HTML detection.
3. User status/deleted/disabled.
4. Payment/free access.
5. Client support and HWID.
6. Device limit.
7. Server prefs and routing injection.
8. Generate real profile.

Нельзя проверять device limit раньше deleted/expired, иначе удалённый пользователь может увидеть не тот текст.

## 6. Real subscription generation

Алгоритм:

```python
def render_real_subscription(user, request):
    links = remnawave_backend.get_nodes_for_user(user.name)
    links = filter_by_client_server_prefs(user.name, links)
    links = append_custom_sub(user.custom_sub, links)
    routing_line = build_happ_routing(user.name)
    plain = [
        routing_line,
        "#profile-title: goida :)",
        "#profile-web-page-url: https://t.me/vpngoidabot",
        *description_lines,
        *links,
    ]
    headers = build_subscription_headers(user, plain)
    return base64_if_needed(plain), headers
```

### Headers

Сохраняем:

```text
Content-Type: text/plain; charset=utf-8
Content-Disposition: inline
Profile-Update-Interval: 2
Profile-Title: base64:<goida :)>
Subscription-Userinfo: upload=<up>; download=<down>; total=<total>; expire=<unix_ts>
Support-Url: https://t.me/vpngoidabot
Profile-Web-Page-Url: https://t.me/vpngoidabot
Announce: base64:<description>
Routing-Enable: true
Routing: happ://routing/onadd/<base64-json>
routing: happ://routing/onadd/<base64-json>
```

## 7. Fake subscriptions

Fake subscriptions должны выглядеть как валидный subscription body, чтобы Happ/V2Ray показал пользователю текст в списке серверов.

### Base fake VLESS

```text
vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443/?type=tcp&security=none#<urlencoded-message>
```

Можно отдавать 2 строки:

```text
vless://000...@127.0.0.1:443/?type=tcp&security=none#<main>
vless://000...@127.0.0.1:443/?type=tcp&security=none#<action>
```

### Fake: deleted user

Profile title:

```text
#profile-title: пользователь удален
```

Fake nodes:

```text
пользователь удален
обратитесь в @vpngoidabot
```

### Fake: expired/unpaid

Profile title:

```text
#profile-title: подписка закончилась
```

Fake nodes:

```text
подписка закончилась
продлите доступ в @vpngoidabot
```

### Fake: disabled

Profile title:

```text
#profile-title: доступ отключен
```

Fake nodes:

```text
доступ отключен
напишите в @vpngoidabot
```

### Fake: unsupported client

Profile title:

```text
#profile-title: клиент не поддерживается
```

Fake nodes:

```text
клиент не поддерживается
скачайте Happ
```

### Fake: device limit

Profile title:

```text
#profile-title: goida :) - лимит превышен
```

Fake nodes:

```text
лимит: {limit} устройства
купите больше в @vpngoidabot
```

### Fake: browser

Browser не должен получать raw subscription вообще. Он получает HTML.

Browser detection:

```text
Accept: text/html
Mozilla/Safari/Chrome browser UA
?html=1
view=html
no Happ UA and no known subscription client UA
```

Исключения:

```text
Happ/*
v2ray*
sing-box*
hiddify*
nekoray*
streisand*
clash*
```

## 8. Browser HTML page

Назначение: твой сайт вместо утечки subscription body.

Обязательные элементы:

- logo/brand.
- CTA: скачать Happ.
- platform tabs: iOS, Android, Windows, macOS, Apple TV, Android TV.
- app links.
- subscription URL readonly input.
- copy button.
- open in Happ button: `happ://add/<encoded-url>`.
- support link.

Текущие тексты:

```text
goida subscription
скачивай happ и подключайся!
iOS
iPhone и iPad
Android
телефоны и планшеты
Windows
Windows 10/11
macOS
Mac Intel и Apple Silicon
Apple TV
tvOS
Android TV
TV и приставки
скопировать
скопировано ✓
```

Ссылки:

```text
iOS AppStore [ru]     https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973
iOS AppStore [global] https://apps.apple.com/us/app/happ-proxy-utility/id6504287215
Android Google Play   https://play.google.com/store/apps/details?id=com.happproxy
Android APK           https://github.com/Happ-proxy/happ-android/releases/latest
Windows Releases      https://github.com/Happ-proxy/happ-desktop/releases/latest
macOS AppStore [ru]   https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973
macOS AppStore global https://apps.apple.com/us/app/happ-proxy-utility/id6504287215
macOS DMG             https://github.com/Happ-proxy/happ-desktop/releases/latest
Apple TV App Store    https://apps.apple.com/us/app/happ-proxy-utility-for-tv/id6748297274
Android TV GooglePlay https://play.google.com/store/apps/details?id=com.happproxy
```

## 9. Happ routing

Текущий base payload:

```json
{
  "Name": "goida.fun - Smart",
  "GlobalProxy": "true",
  "RemoteDNSType": "DoH",
  "RemoteDNSDomain": "https://cloudflare-dns.com/dns-query",
  "RemoteDNSIP": "1.1.1.1",
  "DomesticDNSType": "DoH",
  "DomesticDNSDomain": "https://dns.yandex.ru/dns-query",
  "DomesticDNSIP": "77.88.8.8",
  "DirectSites": ["geosite:category-ru"],
  "ProxySites": [],
  "DirectIp": [
    "geoip:ru",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "224.0.0.0/4",
    "255.255.255.255/32"
  ],
  "DomainStrategy": "IPIfNonMatch",
  "FakeDNS": "false"
}
```

Важно: ключ должен быть `DirectIp`, не `DirectIP`.

### Per-user routing injection

Если используем `user_routing_overrides`:

```text
target=direct  -> add to DirectSites
target=home    -> add to ProxySites + link should route via home-capable profile
target=foreign -> add to ProxySites
target=auto    -> remove override
```

Если Remnawave Config Profile содержит server-side manual rules, их можно читать только как fallback/global rules.

## 10. Device logic

### Device id extraction

```python
def extract_hwid(user_agent):
    if user_agent.startswith("Happ/"):
        return user_agent.split("/")[3][:128]
    match = re.search(r"(?:hwid|device[-_ ]?id)[:=/ ]([A-Za-z0-9_.:-]{4,128})", user_agent, re.I)
    return match.group(1) if match else ""
```

Normalized device id:

```text
hwid:<value>
```

### Guard

```python
if client_ip in SERVER_IPS:
    allow_without_counting()
elif no_hwid:
    fake_unsupported_client()
elif device_is_new and known_devices_count >= device_limit:
    fake_device_limit()
else:
    remember_device()
    real_subscription()
```

### Why local, not Remnawave HWID at first

Remnawave strict HWID возвращает отказ раньше твоего UI. Тогда пользователь увидит тупой 404/ошибку клиента, а не branded explanation. Поэтому:

```text
HWID_DEVICE_LIMIT_ENABLED=false в Remnawave на первом этапе
device enforcement = локальный subscription server
```

## 11. Payment/expiration logic

У пользователя есть:

```text
client_profiles.paid_until
client_profiles.free_access
users.status
remnawave.expireAt
```

Правило:

```python
paid = free_access or paid_until >= today_msk()
```

Если `paid == False`:

- Remnawave user можно оставить enabled или технически disabled.
- Публичная подписка всё равно отдаёт fake `expired`.
- Для безопасности можно синхронизировать `expireAt` в Remnawave.

Рекомендуемый порядок:

1. Bot меняет `client_profiles.paid_until`.
2. Bot вызывает `backend.set_expire_at(username, paid_until end-of-day UTC)`.
3. Subscription endpoint на каждом запросе сам проверяет `client_profiles.paid_until`.
4. Если Remnawave/локальная база расходятся, выигрывает локальная бизнес-логика.

## 12. User lifecycle

### Create user

```text
/adduser name
  -> create local users row with token
  -> create/remna user
  -> add user to default squads
  -> sync paid_until/traffic limits
  -> return https://ru.goida.fun/subscribe/<token>
```

### Delete user

```text
/delete name
  -> insert into deleted_subs(token, name, now, 'deleted')
  -> delete/disable Remnawave user
  -> delete local users row or mark status=deleted
  -> keep token tombstone forever
```

### Disable user

```text
users.status='disabled'
backend.disable_user(name)
subscription returns fake disabled
```

### Migrate user legacy -> remnawave

```text
create remna user
store remna_user_uuid
backend='remnawave'
keep token unchanged
do not force client reimport
subscription starts rendering remna links
```

## 13. Server toggles

Mini App still writes:

```text
client_server_prefs(username, server_key, enabled)
```

Subscription rendering filters links by that table.

Optional sync to Remnawave:

```text
enabled=true  -> internal_squads.add_user(user, squad)
enabled=false -> internal_squads.remove_user(user, squad)
```

But do not depend on squad sync for rendering. Rendering must remain deterministic from local prefs.

## 14. Mini App API mapping

### GET /api/me

Local:

- username
- token/key id
- paidUntil
- deviceLimit
- devices
- server prefs
- freeAccess
- paymentRemindersEnabled

Remnawave:

- traffic up/down/total
- status
- available nodes/squads

### POST /api/devices/unbind

Local only:

```sql
DELETE FROM user_devices WHERE token=? AND device_id=?
```

Optionally also delete Remnawave HWID record later.

### POST /api/servers/toggle

Local first:

```sql
UPSERT client_server_prefs
```

Then best-effort Remnawave squad sync.

### POST /api/plan/request

Local only. Remnawave does not know your pricing.

## 15. Admin bot mapping

### Keep local

- `/broadcast`
- `/who`
- plan requests
- payment reminders
- device unbind/reset
- manual routing UI
- subscription stats
- RKN/TSPU/watchdog commands

### Move through backend adapter

- create user
- delete user
- disable user
- rename user
- traffic read
- server availability
- sync expireAt
- sync traffic limit
- sync squads

### Avoid

- Direct Remnawave DB writes.
- Direct 3X-UI SQLite after migration.
- Giving raw Remnawave subscription URLs to users.

## 16. Text catalog: subscription

### Description

```text
smart — оптимальный сервер.
ru-zapret — для ютуба без рекламы (телеграм и дискорд тоже работают)
для youtube-shorts рекомендуется выбирать fin/swe, на smart и ru-zapret хорошо работают только длинные видео

в случае проблем пишите в бота
t.me/vpngoidabot
```

### Profile titles

```text
goida :)
goida :) - лимит превышен
пользователь удален
клиент не поддерживается
подписка закончилась
доступ отключен
```

### Fake node fragments

```text
пользователь удален
обратитесь в @vpngoidabot
клиент не поддерживается
скачайте Happ
лимит: {limit} устройства. купите больше в @vpngoidabot
подписка закончилась
продлите доступ в @vpngoidabot
доступ отключен
напишите в @vpngoidabot
```

### Browser page

```text
goida subscription
скачивай happ и подключайся!
скопировать
скопировано ✓
iOS
iPhone и iPad
Android
телефоны и планшеты
Windows
Windows 10/11
macOS
Mac Intel и Apple Silicon
Apple TV
tvOS
Android TV
TV и приставки
AppStore [ru]
AppStore [global]
Google Play
APK
GitHub Releases
DMG
App Store
```

## 17. Text catalog: Mini App

### Global

```text
goida vpn
не получилось открыть
это приложение открывается только внутри Telegram
твой Telegram ещё не привязан к goida vpn. напиши @vpngoidabot
скопировано
ошибка
```

### Tabs

```text
мой ключ
оплата
сервера
```

### Key screen

```text
активная подписка
трафик всего
подключить
нет Happ? скачай!
выберите устройство
скачивание
устройства
они появятся после первого подключения через Happ
Платформа:
Подключено:
Последняя активность:
докупить устройства
```

### Payment screen

```text
Бесплатный доступ
оплачено до
не указано
уведомление об оплате
бот пришлёт вам уведомление за день до окончания подписки
максимум устройств
/ месяц
+{price}₽ за каждое устройство сверх {baseDevices}
смена тарифа
сейчас {deviceLimit} {devicesWord} · {monthlyPrice}₽
запросить смену тарифа
тариф не изменён
запросить смену тарифа?
отмена
отправить
заявка отправлена админу
```

### Servers screen

```text
сервера
здесь вы можете отключить лишние сервера для удобства в Happ
Показать все серверы
```

### Device actions

```text
Отвязать устройство
Отвязать устройство «{name}»? Его придётся подключить заново.
отмена
отвязать
```

### Server labels

```text
smart 🇸🇨
fin 🇫🇮
swe 🇸🇪
ru-zapret 🇷🇺
США 🇺🇸
Польша 🇵🇱
Турция 🇹🇷
Нидерланды 🇳🇱
Германия 🇩🇪
Финляндия 🇫🇮
```

### Server descriptions

```text
Самый быстрый сервер
Финляндия (10гб/сек)
Швеция (300мб/сек)
Идеально для телевизора
Дополнительный сервер
```

## 18. Text catalog: client bot

### Commands help

```text
/broadcast текст — рассылка всем привязанным клиентам.
/setpaid username YYYY-MM-DD — указать, до какой даты оплачено.
/paid username N — прибавить N месяцев от сегодня/текущего срока.
/setdevices username N — изменить лимит устройств.
/who username — карточка клиента.
/remind — вручную отправить напоминания тем, у кого оплата сегодня.
/start — открыть Mini App.
/link username — привязать Telegram к пользователю из bot.db.
```

### Client/admin UI

```text
панель администратора
👥 пользователи
📢 рассылка
🔔 напоминания
открыть goida vpn
принять
отклонить
←
→
↩ меню
пользователи — {page}/{pages} ({total} всего)
пользователь {username} не найден
ссылка приглашения:
🔴 отвязать
+1 мес
+3 мес
📅 дата
устр.
бесплатно
🔄
введи текст рассылки:
текст не найден
рассылка отправлена: {count}
напоминания отправлены: {count}
введи дату для {username} (YYYY-MM-DD):
предпросмотр рассылки:
✅ отправить
❌ отмена
```

### API errors

```text
not found
unknown server
user not found
bot token is not configured
hash missing
bad initData hash
bad auth_date
initData expired
user missing
telegram account is not linked
CLIENT_BOT_TOKEN is not configured
```

## 19. Text catalog: admin vpn bot

### Common

```text
BOT_TOKEN не найден
не удалось подключиться к Telegram API
бот запущен: @{username}
ошибка: {error}
использование: /user <username>
пользователь {username} не найден
открываю карточку {username}...
```

### User lifecycle

```text
пользователь {name} уже существует
⏳ создаю пользователя {name}...
✅ пользователь {name} создан
удалить пользователя {username}?
клиенты будут удалены из всех инбаундов.
✅ пользователь {username} удалён
пользователей больше нет.
❌ ошибка удаления: {error}
✅ переименован: {old_name} → {new_name}
❌ имя должно быть латиницей без пробелов
❌ пользователь {new_name} уже существует
```

### Subscription/custom links

```text
✅ описание /subscribe-next обновлено
❌ нужен текст описания
✅ доп.ссылки {username} обновлены
✅ доп.ссылки {username} очищены
← карточка
к списку ↜
```

### Devices/payment

```text
✅ лимит изменён
❌ нужно число дней от 1 до 3650
✅ {username} продлён до {paid_until}
```

### Server/domain routing

```text
Правило:
direct (RU)
home (домашний IP)
foreign (FIN/SWE)
auto (по geoip)
✅ применено
ты на этой странице 🙃
❌ неизвестный тег
❌ сервер не найден в конфиге
закрыть
```

### Whitelist/addwl

```text
➕ отправьте:
• vless:// строку
• JSON-файл (массив строк или объектов)
• JSON-текст (если многочастный — склеится за 3с)
❌ файл слишком большой (>1MB)
❌ не удалось скачать файл
❌ не нашли vless:// записей в файле
❌ не нашли vless:// записей
❌ сессия истекла, повторите /addwl
❌ ошибка записи: {error}
✅ добавлено: {count}
дубли: {count}
отменено
```

### Xray/status

```text
⏳ перезапускаю x-ui...
✅ x-ui перезапущен
❌ ошибка: {stderr}
⚙️ xray status
активных: {active}/{total}
```

## 20. Suggested i18n keys

Если переписывать нормально, завести один файл:

```python
TEXT = {
    "sub.profile.real.title": "goida :)",
    "sub.profile.deleted.title": "пользователь удален",
    "sub.profile.expired.title": "подписка закончилась",
    "sub.profile.unsupported.title": "клиент не поддерживается",
    "sub.profile.limit.title": "goida :) - лимит превышен",
    "sub.fake.deleted.main": "пользователь удален",
    "sub.fake.deleted.action": "обратитесь в @vpngoidabot",
    "sub.fake.expired.main": "подписка закончилась",
    "sub.fake.expired.action": "продлите доступ в @vpngoidabot",
    "sub.fake.unsupported.main": "клиент не поддерживается",
    "sub.fake.unsupported.action": "скачайте Happ",
    "sub.fake.limit.main": "лимит: {limit} устройства",
    "sub.fake.limit.action": "купите больше в @vpngoidabot",
    "mini.tabs.key": "мой ключ",
    "mini.tabs.payment": "оплата",
    "mini.tabs.servers": "сервера",
}
```

## 21. Implementation phases

### Phase 1: RemnawaveAdapter read-only

- `get_user`
- `get_traffic`
- `get_available_servers`
- `get_nodes_for_user`
- tests with one remna user

### Phase 2: subscription backend switch

- Add `users.backend`.
- Legacy users still use `LegacyXuiBackend`.
- Test user uses `RemnawaveBackend`.
- Keep public token unchanged.

### Phase 3: fake state matrix

- Add expired fake.
- Add disabled fake.
- Keep deleted/unsupported/limit/browser behavior.
- Add tests for every state.

### Phase 4: Mini App

- Replace x-ui traffic reads with backend traffic.
- Replace hydra catalog reads with backend/squads.
- Keep local payments/devices/prefs.

### Phase 5: bot write operations

- create/delete/rename/renew through backend abstraction.
- mirror squads from `client_server_prefs`.
- mirror paid_until to Remnawave expireAt.

### Phase 6: decommission legacy

- Migrate users by groups.
- Keep tombstones.
- Stop 3X-UI only after all active users are remnawave.

## 22. Tests to write first

```text
test_unknown_token_returns_404
test_deleted_token_returns_deleted_fake_profile
test_browser_request_returns_html_not_links
test_no_hwid_returns_unsupported_fake_profile
test_device_limit_returns_limit_fake_profile
test_expired_user_returns_expired_fake_profile
test_disabled_user_returns_disabled_fake_profile
test_valid_happ_hwid_returns_real_links
test_custom_sub_is_append_only
test_server_prefs_filter_links
test_legacy_sub_requires_flag
test_clash_returns_unsupported_yaml
```

## 23. Notes for Remnawave Panel/Node

- Panel is an admin/backend service, not the public subscription layer.
- Node API port is not browser UI and requires mTLS/key payload.
- Remnawave-generated node key must replace any bootstrap node key.
- Config Profile should contain required Xray rules:
  - WS `heartbeatPeriod: 30`.
  - YouTube UDP/443 block where needed.
  - no broad port-53 rules except smart-pro exception.
  - no `googleapis`, `gstatic`, `googleusercontent` in YouTube hostlists.

