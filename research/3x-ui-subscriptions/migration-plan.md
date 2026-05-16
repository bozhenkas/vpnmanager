# migration plan

## Фаза 0: reverse only

Сделано здесь: разобрать upstream 3x-ui и зафиксировать, что переносим.

Никаких prod-изменений.

## Фаза 1: isolated engine

Создать отдельный Python engine вне `bot/vpn-bot.py`:

- input: bot user record, x-ui inbound settings, traffic rows, request metadata;
- output: response body, headers, status code;
- no network calls;
- no writes to `/etc/x-ui/x-ui.db`;
- no restart Xray.

Минимальный API:

- `generate_plain_subscription(user, inbounds, extras, options)`
- `generate_json_subscription(user, inbounds, options)`
- `generate_clash_subscription(user, inbounds, options)`
- `generate_deleted_subscription(token)`
- `check_subscription_device_guard(token, request, policy)`

## Фаза 2: fixtures

Нужны обезличенные fixtures:

- smart inbound VLESS WS/TLS;
- smart-pro inbound;
- hydra inbound;
- Reality outbound/client, если решим поддерживать в подписке;
- deleted user tombstone;
- user with `custom_sub`;
- user with `limitIp = 0`;
- user with `limitIp = 4`;
- Happ UA with hwid.

## Фаза 3: golden tests

Тесты должны зафиксировать:

- `/subscribe/<existing-token>` остается тем же маршрутом;
- token не меняется;
- basic v2ray clients получают base64 links;
- Happ получает `happ://routing/onadd/...`, `DirectIp`, и routing headers;
- Clash получает YAML с expected proxies/rules;
- JSON endpoint получает валидный Xray config;
- deleted token получает `200` и stub;
- unknown token получает `404`;
- `custom_sub` append-only, не подменяет основные links;
- `smart-pro` виден только `bozhenkas`;
- `googleapis`, `gstatic`, `googleusercontent` не попадают в YouTube/direct mistakes.

## Фаза 4: shadow mode

На RU можно будет добавить отдельный debug endpoint, например:

- `/subscribe-next/<token>`
- `/subscribe-next/<token>/json`
- `/subscribe-next/<token>/clash`

И сравнить live current vs next без миграции клиентов.

## Фаза 5: migration

Только после тестов:

1. Встроить engine в `bot/vpn-bot.py`.
2. Оставить старый `/subscribe/<token>`.
3. Сохранить `deleted_subs`.
4. Сохранить `custom_sub`, `hydra`, `hysteria`, `wl`.
5. Включить JSON/Clash endpoints как добавку, а не замену.
6. Добавить changelog в `.Codex/wings/bot/changelog.md`.

## Явные запреты

- Не писать `subId` в существующих клиентах без отдельного подтверждения.
- Не менять UUID клиентов.
- Не менять URL подписок.
- Не удалять tombstone tokens.
- Не мигрировать в prod до golden tests и ручной проверки Happ/Clash/V2Ray.
