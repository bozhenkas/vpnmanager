# subscriptions research

Срез собран из:

- `tmp/subs_raw.json` — результат запуска `scripts/parse_subs.py`, сохраненный дамп сторонних подписок.
- `scripts/parse_subs.py` — парсер сторонних VPN-подписок: Xray/sing-box JSON array, classic base64 и plain `vless://`.
- локальный контекст проекта: `sub-updater/updater.py`, `scripts/sync_remna_hydra.py`, `docs/whitelist-sharing.md`, `docs/architecture-brief.md`.

В файл намеренно не перенесены полные subscription URL, UUID, public keys и endpoint credentials. Провайдеры указаны по доменам источников; точные токены остаются только в локальных исходниках/дампах.

## summary

Парсером было исследовано 6 сторонних подписок. В сохраненном дампе `tmp/subs_raw.json` найдено 203 server profiles, после дедупликации по `host:port` — 138 unique endpoints.

Пул протоколов:

| proto | count | note |
|---|---:|---|
| `vless` | 203 | весь собранный пул; других протоколов в дампе нет, хотя парсер умеет больше |

Пул transport/security:

| stack | count | share |
|---|---:|---:|
| `vless + tcp + reality` | 130 | 64.0% |
| `vless + grpc + reality` | 41 | 20.2% |
| `vless + ws + tls` | 19 | 9.4% |
| `vless + xhttp + reality` | 8 | 3.9% |
| `vless + xhttp + tls` | 2 | 1.0% |
| `vless + grpc + tls` | 2 | 1.0% |
| `vless + grpc + none` | 1 | 0.5% |

Главный вывод: внешний рынок в этом срезе уже почти полностью VLESS-first. База — `tcp/reality` и `grpc/reality`; `xhttp/reality` уже присутствует, но пока малой долей. `ws/tls` остается как legacy/compatibility слой. В дампе нет `hysteria2`, `tuic`, `shadowsocks` или `trojan`, хотя `scripts/parse_subs.py` готов их распознать в JSON-outbounds.

Порты:

| port | count |
|---:|---:|
| 443 | 189 |
| 8443 | 8 |
| 2053 | 2 |
| 7443 | 2 |
| 48672 | 1 |
| 12443 | 1 |

Flow:

| flow | count |
|---|---:|
| empty | 127 |
| `xtls-rprx-vision` | 76 |

Security:

| security | count |
|---|---:|
| `reality` | 179 |
| `tls` | 23 |
| `none` | 1 |

Endpoint shape:

| endpoint kind | count |
|---|---:|
| domain host | 106 |
| IPv4 host | 97 |

Routing/DNS собранное:

| item | count |
|---|---:|
| direct domains | 1217 |
| proxy domains | 5 |
| direct IP rules | 19 |
| proxy IP rules | 1 |
| DNS configs | 75 |

Proxy-domain rules в дампе: `geosite:github`, `geosite:google-play`, `geosite:telegram`, `geosite:twitch-ads`, `geosite:youtube`.

Direct/IP rules включают private ranges, `geoip:private`, `geoip:ru`, `geoip:direct`, `geoip:whitelist`, Yandex-related ranges/IPs и несколько точечных IPv4.

DNS:

| queryStrategy | count |
|---|---:|
| `UseIPv4` | 53 |
| `UseIP` | 22 |

Частые DNS upstreams: `1.1.1.1`, `1.0.0.1`, `https://8.8.8.8/dns-query`, `https://77.88.8.8/dns-query`, `77.88.8.8`, `https://cloudflare-dns.com/dns-query`.

## providers

| provider domain | format | servers | unique signal | routing/DNS |
|---|---:|---:|---|---|
| `sub.whitestore.club` | JSON | 16 | `tcp/reality`, `xhttp/reality`, `xhttp/tls` | 89 direct domains, 11 DNS configs |
| `your-durev.com` | plain/base64 | 63 | mostly `tcp/reality`, plus `ws/tls`; many country-labeled remarks | no routing/DNS parsed |
| `sub.9142858.xyz` | JSON | 19 | mixed `tcp/reality`, `xhttp/reality`, `grpc/reality` | 12 direct domains, 5 proxy domains, 19 DNS configs |
| `net4.su` | JSON | 70 | large pool: `tcp/reality` + `grpc/reality` | 23 DNS configs |
| `1984-mini-app.bot.nu` | JSON | 25 | mostly `grpc/reality`, plus `grpc/tls`, `tcp/reality`, one `grpc/none` | 1155 direct domains, 12 DNS configs |
| `sub.fenvpn.ru` | JSON | 10 | pure `ws/tls` pool | 10 DNS configs |

Provider-level transport split:

| provider | transport split |
|---|---|
| `sub.whitestore.club` | 13 `tcp/reality`; 1 `xhttp/reality`; 2 `xhttp/tls` |
| `your-durev.com` | 54 `tcp/reality`; 9 `ws/tls` |
| `sub.9142858.xyz` | 10 `tcp/reality`; 7 `xhttp/reality`; 2 `grpc/reality` |
| `net4.su` | 50 `tcp/reality`; 20 `grpc/reality` |
| `1984-mini-app.bot.nu` | 19 `grpc/reality`; 2 `grpc/tls`; 3 `tcp/reality`; 1 `grpc/none` |
| `sub.fenvpn.ru` | 10 `ws/tls` |

## countries / product labels

`your-durev.com` exposes many human-readable country remarks in plain `vless://` links. Distinct non-generic labels include:

- Albania, Argentina, Armenia, Australia, Belgium, Canada, Chile, Czech Republic, Denmark, Estonia, Finland, France, Germany, Greece, Hong Kong, Hungary, India, Ireland, Italy, Japan, Kazakhstan, Latvia, Lithuania, Moldova, Netherlands, Nigeria, Norway, Poland, Portugal, Romania, Russia, Serbia, Singapore, Spain, Sweden, Switzerland, Turkey, UAE, USA, Ukraine, United Kingdom.
- Special-purpose labels: `САМЫЙ ОПТИМАЛЬНЫЙ СЕРВЕР`, `Gemini и Roblox`, `Белые списки`, `Госуслуги`, `YT без рекламы`.

goida-local Hydra product labels:

- USA, Poland, Turkey, Netherlands, Germany — сторонние Hydra slots in bot/client UI.
- In older architecture docs: Whitestore Hydra had NL/DE/PL/TR concrete external slots; USA existed as logical slot but could be absent/unreachable in live import.

## parser structure

`scripts/parse_subs.py` does four jobs:

1. Fetches fixed subscription sources with Happ-like UA and `X-HWID`.
2. Detects response format:
   - JSON object/array;
   - base64 text;
   - plain text.
3. Parses server outbounds:
   - Xray-style `settings.vnext[].users[]`;
   - Xray-style `settings.servers[]` for Shadowsocks-like entries;
   - sing-box-style top-level `server` / `server_port`.
4. Aggregates:
   - `servers`;
   - `direct_domains`, `proxy_domains`;
   - `direct_ips`, `proxy_ips`;
   - `happ_routings`;
   - `dns_configs`.

Recognized protocols in parser:

- `vless`
- `vmess`
- `trojan`
- `hysteria2`
- `hy2`
- `shadowsocks`
- `ss`
- `tuic`

Recognized routing styles:

- Xray `routing.rules[].outboundTag`
- sing-box-ish `outbound`
- domain keys: `domain`, `domain_suffix`, `domains`
- IP keys: `ip`, `ip_cidr`

Direct outbound tags:

- `direct`
- `bypass`
- `freedom`

Non-proxy ignored tags:

- `block`
- `blocked`
- `blackhole`
- `dns-out`
- `dns`
- `api`
- empty tag

Everything else is treated as proxy.

## raw dump structure

`tmp/subs_raw.json` top-level:

```json
{
  "subs": [],
  "aggregated": {}
}
```

Each `subs[]` item:

```json
{
  "source": "redacted provider URL",
  "format": "json|plain",
  "servers": [],
  "direct_domains": [],
  "proxy_domains": [],
  "direct_ips": [],
  "proxy_ips": [],
  "happ_routings": [],
  "dns_configs": []
}
```

Each parsed server carries:

- `proto`
- `host`
- `port`
- `uuid`
- `flow`
- `network`
- `security`
- `stream_settings`
- `sni`
- `pbk` / `sid` for REALITY when parsed
- `remark`
- `tag`

The saved `aggregated` block contains:

- deduped `direct_domains`
- deduped `proxy_domains`
- deduped `direct_ips`
- deduped `proxy_ips`
- `unique_servers`
- stats counters

## goida integration points

### Hydra

Local code uses third-party subscription data as Hydra backends. Relevant files:

- `sub-updater/updater.py` — production updater: fetches upstream subscription, parses VLESS, groups Hydra/WL servers, updates Xray routing/template.
- `scripts/sync_remna_hydra.py` — Remnawave-era sync for Hydra profiles.
- `bot/vpn-bot.py` — exposes Hydra server toggles and subscription generation.
- `client-bot/client-bot.py`, `client-web/index.html` — UI labels for Hydra slots.
- `deploy/nginx/ru.goida.fun.conf` — `/hydra-{nl,de,pol,tur}` WS ingress paths.

### Whitelist

`docs/whitelist-sharing.md` describes WL as reserve VLESS servers from Whitestore-like Hydra subscription:

- WL profiles are full Xray configs or plain VLESS links.
- UUIDs must be replaced with the account's own subscription UID before use.
- Dead backends are filtered by TCP-connect.
- Entries are deduped by `host:port`.
- WL is designed as direct client fallback without dependency on goida infra.

### Current goida implication

The external-subscription research supports the current goida direction:

- keep stable public profiles on known-good Happ-compatible transports;
- use third-party Hydra/WL as optional fallback, not core dependency;
- treat `xhttp/reality` as a promising but compatibility-sensitive path;
- preserve client-side routing and RU-direct split for Russian resources;
- keep exact provider secrets/tokens out of tracked docs.

## gaps / next parser upgrades

`scripts/parse_subs.py` should be extended before the next research run:

- parse `fp`, `alpn`, `spx`, `mode`, `serviceName`, `authority`, `headerType` from share links and stream settings;
- extract XHTTP `extra`, `xmux`, mode variants (`stream-one`, packet modes, H2/H3 details);
- preserve Hysteria2/TUIC/Shadowsocks details when they appear in JSON;
- classify country/region from remarks into normalized country codes;
- emit safe provider summaries with source URL path/token redacted;
- add `generated_at` and parser version to `tmp/subs_raw.json`;
- avoid storing raw UUID/pbk/sid in public/shareable research artifacts.

