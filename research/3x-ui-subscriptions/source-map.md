# source map

## Upstream 3x-ui

| Файл | Что важно |
| --- | --- |
| `sub/sub.go` | Инициализация subscription-сервера, чтение settings, paths, flags, Happ routing config. |
| `sub/subController.go` | HTTP routes: normal sub, JSON sub, Clash sub; common headers; HTML page mode. |
| `sub/subService.go` | Поиск клиентов по `subId`, агрегация traffic, генерация ссылок всех протоколов, external proxy, remarks. |
| `sub/subJsonService.go` | Генерация Xray JSON profile из `default.json`, fragment/noises/mux/rules. |
| `sub/subClashService.go` | Генерация Clash YAML, proxies, proxy-groups, basic `MATCH,PROXY`. |
| `sub/default.json` | Базовый Xray JSON клиент: socks/http inbound, proxy/direct/block outbounds, routing. |
| `sub/links.go` | Маленький provider для получения ссылок по `subId` из других частей панели. |
| `database/model/model.go` | Модель `Client`: `limitIp`, `subId`, `enable`, `expiryTime`, `totalGB`, `tgId`, `reset`. |
| `web/job/check_client_ip_job.go` | Реализация лимита устройств/IP через Xray access log и fail2ban. |
| `web/service/inbound.go` | Lifecycle клиентов: delete, rename, reset IP limit, clear client IPs, запись `subId`. |
| `web/controller/inbound.go` | API endpoints для sub links и client IPs. |
| `web/service/tgbot.go` | Дополнительная логика telegram-бота 3x-ui: генерация/показ subscription URLs. |

## Goida current

| Файл | Что сохранять |
| --- | --- |
| `bot/vpn-bot.py` | `/subscribe/<token>`, генерация VLESS/Happ, `deleted_subs`, `user_ips`, `custom_sub`, `hydra`, `hysteria`, `wl`. |
| `sub-updater/updater.py` | Сборка Xray routing/outbounds, hydra, whitelist registry. Не источник HTTP-подписок, но влияет на доступные links/routes. |
| `.Codex/wings/bot/ru-bot.md` | Текущая память по live bot/subscription/Happ. |
| `.Codex/wings/infra/routing-ru.md` | Текущая routing-карта RU. |

## Локальные якоря

- `users.token` — стабильный внешний subscription id.
- `deleted_subs.token` — tombstone для удаленного пользователя.
- `user_ips(token, ip)` — текущая грубая проверка количества устройств.
- `clients[].limitIp` в 3X-UI sqlite — текущий per-user лимит, читается из `smart` inbound.
- `clients[].subId` сейчас пустой в нашем коде, поэтому upstream поиск по `subId` напрямую не подходит.
