# device limit

## Upstream 3x-ui model

Лимит хранится в client JSON:

- `clients[].limitIp`
- `0` значит без лимита.

Отдельная таблица хранит замеченные IP:

- `inbound_client_ips.client_email`
- `inbound_client_ips.ips` как JSON.

## Upstream algorithm

3x-ui не считает скачивания подписки устройствами. Он смотрит фактические Xray access logs:

1. Проверяет, есть ли клиенты с `limitIp > 0`.
2. Требует включенный access log.
3. На Linux требует fail2ban.
4. Парсит новые строки access log:
   - source IP;
   - `email`;
   - timestamp.
5. Игнорирует localhost.
6. Держит окно активности около 30 минут.
7. Для лимита считает только IP, которые были замечены в текущем scan window.
8. Если живых IP больше лимита, оставляет старые, а новые пишет в специальный limit log.
9. fail2ban забирает limit log и рвет лишние подключения.
10. Дополнительно клиент временно remove/add через Xray API, чтобы соединение сбросилось сразу.

Ключевой момент: historical fresh IP остаются для отображения, но не занимают live slots, если не были замечены в текущем проходе.

## Current goida algorithm

Сейчас `bot/vpn-bot.py` считает скачивания подписки:

- key: `token`;
- device id: для Happ `hwid` из User-Agent, иначе `ip:<client_ip>`;
- лимит берется из `clients[].limitIp` smart inbound;
- при превышении возвращается stub subscription.

Это проще, но менее точно:

- клиент может скачать подписку с нескольких сетей и занять лимит;
- фактические Xray подключения не проверяются;
- старые устройства не протухают автоматически;
- нет fail2ban/drop active connection.

## Что лучше перенести

Для полноценной проверки устройств лучше взять upstream-подход:

- считать реальные Xray access log events;
- хранить `email -> active devices`;
- добавить TTL/окно активности;
- не считать каждое обновление подписки новым устройством;
- использовать текущий `limitIp` в 3X-UI как source of truth.

Но на первом этапе миграции лучше оставить текущий subscription stub-limit как compatibility guard и отдельно добавить observe-only job:

1. Парсим access log.
2. Пишем события в новую таблицу/файл.
3. Сравниваем с текущим `user_ips`.
4. После тестов включаем enforcement.

## Риски

- Нужен корректный Xray access log path.
- Нужен стабильный email naming across inbounds.
- Нельзя банить серверные IP и reverse proxy.
- fail2ban rules должны совпадать с форматом log line, если будем использовать upstream enforcement.
