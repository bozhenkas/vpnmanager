# 3x-ui subscriptions reverse

Дата: 2026-05-11.

Источник: `research/vendor/3x-ui`, upstream `MHSanaei/3x-ui`, commit `8f3202f431373baab81545d8970236929676f71b`.

Цель: забрать в goida-vpn подписочную логику 3x-ui отдельно от прода, проверить ее тестами, а затем аккуратно заменить текущий генератор подписок.

## Что забираем

- Поиск клиентов по `subId` в настройках inbound.
- Генерацию обычных ссылок `vmess`, `vless`, `trojan`, `shadowsocks`, `hysteria`, `hysteria2`.
- Отдельные ответы для plain/base64 subscription, Xray JSON и Clash YAML.
- HTTP headers для клиентов подписок: traffic info, update interval, profile title/support/announce.
- Happ routing headers: `Routing-Enable` и `Routing`.
- IP/device limit из access-log с коротким окном активности.

## Что сохраняем из goida

- Текущие URL: `https://ru.goida.fun/subscribe/<token>`.
- Текущие уникальные `token` из bot-db. Клиенты пользователей не должны добавлять подписки заново.
- Текущую семантику удаления: если пользователь удален, его старый token должен отвечать `200` и отдавать fallback "доступа нет".
- Текущие UUID клиентов в 3X-UI/Xray.
- Текущие локальные добавки: `hydra`, `hysteria`, `wl`, `custom_sub`, special `smart-pro`.

## Главное отличие от upstream

3x-ui считает `subId` полем клиента внутри `inbounds.settings.clients`. Если `subId` не найден, upstream отвечает `400 Error!`.

У нас canonical subscription id уже есть: `users.token` в bot-db. Новый адаптер должен мапить `token -> user -> emails/clients`, а не заставлять клиентов переимпортировать подписки. Для удаленных пользователей нужен tombstone record: token остается валидным, Xray-клиенты могут быть удалены, но HTTP subscription продолжает отдавать stub.

## Предлагаемая директория кода

На следующем шаге можно делать экспериментальную реализацию отдельно, например:

- `research/subscription-engine/` — чистая Python-логика без HTTP-сервера.
- `research/subscription-engine/tests/fixtures/` — sqlite/json фикстуры из обезличенных inbound settings.
- `research/subscription-engine/tests/` — golden tests для happ/v2ray/clash/deleted/ip-limit.

До миграции в `bot/vpn-bot.py` ничего не переносим.
