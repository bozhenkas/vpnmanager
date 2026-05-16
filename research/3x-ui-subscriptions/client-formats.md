# client formats

## Normal subscription

3x-ui умеет генерировать share links для:

- `vmess://...`
- `vless://...`
- `trojan://...`
- `ss://...`
- `hysteria://...`
- `hysteria2://...`

Для VLESS/Reality важны поля:

- `type`, `security`, `encryption`;
- transport settings: `ws`, `grpc`, `tcp`, `httpupgrade`, `xhttp`;
- TLS: `sni`, `fp`, `alpn`;
- Reality: `sni`, `pbk`, `sid`, `spx`, `fp`, `pqv`;
- `flow` только для TCP.

XHTTP пока отложен, но parser/generator лучше сразу проектировать расширяемым по network type.

## Happ

3x-ui отдает routing через headers:

- `Routing-Enable: true|false`
- `Routing: <rules>`

У нас сейчас Happ routing также лежит первой строкой body:

- `happ://routing/onadd/<base64-json>`

Это стоит сохранить на первом этапе, потому что оно уже проверено на live bot. Важно поле `DirectIp`, не `DirectIP`.

Минимум для совместимости:

- body line `happ://routing/onadd/...`;
- header `routing` как сейчас для обратной совместимости;
- дополнительно можно добавить upstream headers `Routing-Enable` и `Routing`.

## V2Ray/Xray JSON

3x-ui строит JSON profile из `sub/default.json`:

- inbound `socks` на `127.0.0.1:10808`;
- inbound `http` на `127.0.0.1:10809`;
- outbound `proxy` подставляется из клиента;
- outbounds `direct`, `block`;
- routing default `MATCH -> proxy`;
- optional `fragment`, `noises`, `mux`, custom rules.

Это полезно как отдельный endpoint, например `/subscribe/<token>/json`, но нельзя ломать текущий `/subscribe/<token>`.

## Clash

3x-ui Clash YAML минималистичный:

- `proxies`;
- один `proxy-groups` select `PROXY`;
- `rules: MATCH,PROXY`.

Для goida этого мало, потому что нам нужны разные routing rules для RU-direct/Happ/Clash/V2Ray. Значит Clash-генератор можно взять как protocol encoder, но rules надо расширять нашими списками direct/proxy/reject.

## Traffic headers

3x-ui common headers:

- `Subscription-Userinfo: upload=...; download=...; total=...; expire=...`
- `Profile-Update-Interval`
- `Profile-Title: base64:<title>`
- `Support-Url`
- `Profile-Web-Page-Url`
- `Announce: base64:<announce>`

У нас сейчас `Subscription-Userinfo` пустой. Можно постепенно начать отдавать реальные значения, но только после тестов клиентов, чтобы не сломать parsing.
