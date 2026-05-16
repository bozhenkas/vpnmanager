# subscription flow

## Upstream 3x-ui flow

1. Router регистрирует:
   - `<subPath>:subid` для обычных ссылок;
   - `<subJsonPath>:subid` для Xray JSON;
   - `<subClashPath>:subid` для Clash YAML.
2. Handler берет `subid` из path и вычисляет request host/scheme.
3. `GetSubs(subId, host)` ищет inbounds, где внутри JSON `settings.clients[*].subId == subId`.
4. Для каждого найденного клиента генерируется ссылка по protocol/network/security.
5. Traffic агрегируется по email, чтобы не задвоить клиента в нескольких inbound.
6. Handler выставляет headers и отдает либо base64 body, либо plain body, в зависимости от настройки `subEncrypt`.
7. Если клиент запросил HTML (`Accept: text/html`, `?html=1`, `view=html`), отдаст страницу подписки.

## Upstream matching

Поиск основан на SQLite JSON-функциях по `inbounds.settings`:

- protocol in `vmess`, `vless`, `trojan`, `shadowsocks`, `hysteria`, `hysteria2`;
- inbound `enable = true`;
- `clients[*].subId = requested subId`;
- затем проверяется конкретный client с тем же `SubID`.

## Goida adapter flow

Нам нужен другой lookup layer:

1. Request: `/subscribe/<token>`.
2. Если `token` есть в `users`, получаем `username`.
3. Строим список email по текущим правилам:
   - `smart`: `username`;
   - `smart-pro`: только `bozhenkas`;
   - prefixed inbound: `<prefix><username>`;
   - hydra: `<country-prefix><username>`;
   - append-only `custom_sub`;
   - feature flags `hysteria`, `wl`.
4. Достаем clients из 3X-UI sqlite по email, а не по `subId`.
5. Генерируем форматы через reverse 3x-ui генераторы.
6. Выставляем headers для Happ/v2ray/clash совместимости.
7. Возвращаем base64 normal subscription как сейчас.

## Deleted fallback

Если `token` не найден в `users`, но найден в `deleted_subs`:

- вернуть `200`;
- вернуть base64 stub profile;
- не пытаться искать Xray clients;
- не отвечать upstream-style `400 Error!`.

Это обязательное поведение, чтобы у удаленного пользователя в старой подписке висело понятное "доступа нет".

## Ошибки

Рекомендуемое поведение для неизвестного token:

- `404` для token, которого никогда не было;
- `200` fallback для tombstone token;
- `200` с пустым/stub профилем для активного user без clients только если это осознанная админская ситуация, иначе логировать как ошибку генерации.
