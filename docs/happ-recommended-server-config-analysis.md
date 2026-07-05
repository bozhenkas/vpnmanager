# Анализ клиентского конфига Xray «🌍 Рекомендуемый сервер»

> Полный разбор клиентского конфига Xray-core (формат Happ, macOS).
> Назначение документа: дать второму ИИ-агенту исчерпывающий контекст по логике конфига —
> что делает каждый блок, в каком порядке проходит трафик, какие есть особенности и риски.
>
> Источник: профиль `remarks: "🌍 Рекомендуемый сервер"`, экспорт из клиента Happ
> (`group.su.ffg.happ.plus`). Это **клиентская** сторона (outbound на зарубежные узлы),
> не серверная (inbound на нодах).

---

## 0. TL;DR (одной фразой)

Российский трафик (по `geoip:ru`, `geosite:category-ru` и большому ручному белому списку доменов)
и BitTorrent идут **напрямую** (`freedom`); весь остальной TCP/UDP автоматически распределяется
по 8 зарубежным VLESS-узлам в DE/NL, выбираемым по живому замеру задержки (`leastLoad`),
с откатом на прямое соединение, если все зарубежные узлы недоступны.

```
            ┌─────────────── приложение ───────────────┐
            │  SOCKS5 :10808 (udp)   или   HTTP :10809  │
            └──────────────────┬────────────────────────┘
                               │  sniffing: вскрыть SNI/Host (http,tls,quic)
                               ▼
                       ┌──────────────┐
                       │   routing    │  правила по порядку, first-match-wins
                       └──────┬───────┘
        ┌─────────────┬───────┼────────────────────────┐
        ▼             ▼       ▼                         ▼
   bittorrent     geoip:ru  geosite:category-ru   всё остальное
        │             │       + whitelist (сотни доменов)   │  (tcp,udp)
        ▼             ▼            ▼                         ▼
     direct        direct       direct           Super_Balancer_Auto
                                                  (leastLoad по proxy*)
                                                          │
                                            ┌─────────────┴─────────────┐
                                            ▼                           ▼
                                  proxy…proxy-8 (8 узлов DE/NL)   fallback → direct
```

---

## 1. Точки входа — `inbounds`

Два локальных прокси на петлевом интерфейсе `127.0.0.1` (наружу не слушают — только для приложений на этой же машине).

| Тег | Порт | Протокол | Ключевые настройки |
|-----|------|----------|--------------------|
| `socks` | `10808` | SOCKS5 | `auth: noauth`, **`udp: true`** |
| `http`  | `10809` | HTTP    | `allowTransparent: false` |

### Sniffing (на обоих входах)
```json
"sniffing": { "enabled": true, "destOverride": ["http","tls","quic"], "routeOnly": false }
```
- **Зачем:** приложение часто передаёт прокси уже резолвнутый IP, а не домен. Sniffing вскрывает
  настоящий хост из HTTP-заголовка / TLS SNI / QUIC и **подменяет destination на доменное имя**.
- **Без этого** правила маршрутизации по `domain:` / `geosite:` массово не срабатывали бы — трафик
  улетал бы в балансировщик мимо белого списка.
- `routeOnly: false` → восстановленный домен используется и для маршрутизации, **и** передаётся дальше
  в исходящее соединение (а не только для выбора маршрута).
- `udp: true` на SOCKS критично: QUIC (HTTP/3), игры, VoIP идут по UDP.

---

## 2. Исходящие — `outbounds`

Всего **11** outbound'ов: 8 рабочих VLESS-узлов + 1 особый обходной узел + 2 служебных (`direct`, `block`).

### 2.1. Общая «семья» REALITY-узлов: `proxy` … `proxy-7` (7 узлов)

Все семь построены по одной схеме:

```json
{
  "protocol": "vless",
  "settings": { "vnext": [{ "address": "<узел>", "port": 443,
      "users": [{ "id": "2fc27057-…-7d64a05aa9b5", "encryption": "none", "flow": "" }] }] },
  "streamSettings": {
    "network": "grpc",
    "grpcSettings": { "serviceName": "grpc", "mode": false, "authority": "" },
    "security": "reality",
    "realitySettings": {
      "serverName": "<узел>", "publicKey": "<уникальный>", "shortId": "<уникальный>",
      "fingerprint": "<chrome|qq|random>"
    }
  }
}
```

| Тег | Адрес | Регион | uTLS fingerprint | shortId | publicKey (нач.) |
|-----|-------|--------|------------------|---------|------------------|
| `proxy`   | `de-3.cdn.fast1984.io` | 🇩🇪 DE | `chrome` | `3f660d470aba907a` | `VjoJk_oCD4kY…` |
| `proxy-2` | `de-5.cdn.fast1984.io` | 🇩🇪 DE | `qq`     | `6fe592b49f892f4a` | `IdFgUZViCTwd…` |
| `proxy-3` | `de-6.cdn.fast1984.io` | 🇩🇪 DE | `chrome` | `a4143173b34c8ad3` | `RhSOViYRr9cG…` |
| `proxy-4` | `de-7.cdn.fast1984.io` | 🇩🇪 DE | `random` | `e623c148bb0faa19` | `0jDIe-xwLsYF…` |
| `proxy-5` | `de-8.cdn.fast1984.io` | 🇩🇪 DE | `random` | `2daca42678c31e34` | `pCz0g6_bJvPx…` |
| `proxy-6` | `nl-1.cdn.fast1984.io` | 🇳🇱 NL | `chrome` | `fc38756047cc884a` | `4xHMPSgpvOyu…` |
| `proxy-7` | `nl-2.cdn.fast1984.io` | 🇳🇱 NL | `chrome` | `1mr4N_Er5MMk…` | `1mr4N_Er5MMk…` |

Общие свойства:
- **VLESS** поверх **gRPC** (`serviceName: "grpc"`), порт **443**.
- Один и тот же `id` (UUID пользователя) на всех узлах — единая подписка/учётка.
- `encryption: none`, `flow: ""` — шифрование берёт на себя транспортный слой (REALITY/TLS), не VLESS.
- **REALITY** — маскировка под легитимный TLS: `serverName` == реальный адрес узла, у каждого узла
  свой `publicKey` + `shortId`.
- **uTLS fingerprint варьируется** (`chrome` / `qq` / `random`) — анти-fingerprint, чтобы все
  соединения не выглядели идентично для DPI.

> Семантика тегов: `proxy` — «базовый» тег без номера, остальные нумеруются `-2 … -7`.
> Это важно для селекторов (см. §4): они матчат **по префиксу `proxy`**, поэтому ловят и `proxy`, и `proxy-N`.

### 2.2. Особый обходной узел: `proxy-8` (`de-4.cdn.fast1984.io`)

Выпадает из общей схемы — это «тяжёлый» антицензурный узел поверх WireGuard:

```json
"streamSettings": {
  "network": "grpc",
  "grpcSettings": { "serviceName": "grpc" },
  "security": "tls",                         // ← TLS, НЕ reality
  "tlsSettings": { "serverName": "de-4.cdn.fast1984.io",
                   "alpn": ["h2","http/1.1"], "fingerprint": "firefox" },
  "finalmask": {                             // ← фрагментация TLS ClientHello (анти-DPI)
    "tcp": [{ "type": "fragment",
              "settings": { "packets": "tlshello", "length": "100-200",
                            "delay": "10-20", "maxSplit": 3 } }] },
  "sockopt": {
    "interface": "wg0",                      // ← выход через WireGuard-интерфейс
    "tcpcongestion": "bbr",
    "tcpMaxSeg": 1440, "tcpWindowClamp": 600,
    "tcpUserTimeout": 10000, "tcpKeepAliveIdle": 300,
    "domainStrategy": "AsIs", "happyEyeballs": {}
  }
}
```

Отличия и зачем они:
- **`security: "tls"` вместо `reality`** + `fingerprint: "firefox"` — другой класс маскировки.
- **`finalmask` / fragment `tlshello`** — режет TLS ClientHello на ≤3 части (длина 100–200 байт,
  задержка 10–20 мс) → DPI сложнее склеить и распознать SNI. Это техника обхода активной блокировки.
- **`sockopt.interface: "wg0"`** — соединение уходит через интерфейс WireGuard. То есть `proxy-8`
  работает поверх отдельного WG-туннеля с тюнингом TCP под нестабильный/высоколатентный канал
  (`bbr`, ограничение окна `tcpWindowClamp:600`, `tcpMaxSeg:1440`, агрессивный `tcpUserTimeout:10000`).
- **⚠️ Важно для §4:** тег `proxy-8` тоже начинается на `proxy`, поэтому **он попадает** и в
  `subjectSelector` обсерватории, и в `selector` балансировщика. То есть «особый» узел участвует
  в общей балансировке наравне с REALITY-узлами, хоть и устроен иначе. Это может быть как намеренно
  (резервный обходной путь в общем пуле), так и побочный эффект префиксного матчинга — стоит
  проверить, желаемо ли это поведение.

### 2.3. Служебные

| Тег | Протокол | Роль |
|-----|----------|------|
| `direct` | `freedom`   | прямой выход без прокси (для RU-трафика и как fallback балансировщика) |
| `block`  | `blackhole` | чёрная дыра. **В текущих `routing.rules` не используется** (нет правила, ссылающегося на `block`) |

---

## 3. Замер качества — `burstObservatory`

```json
"burstObservatory": {
  "subjectSelector": ["proxy"],
  "pingConfig": {
    "destination": "http://www.gstatic.com/generate_204",
    "interval": "1m", "timeout": "3s", "sampling": 1, "connectivity": ""
  }
}
```

- Раз в минуту (`interval: 1m`, `timeout: 3s`) пингует `gstatic.com/generate_204` (стандартный
  204-эндпоинт проверки связности) **через каждый** outbound, чей тег матчит `subjectSelector: ["proxy"]`
  → то есть через все `proxy*` (включая `proxy-8`).
- `sampling: 1` — хранит последнее измерение для каждого узла.
- Собранные RTT — это **входные данные для балансировщика** `leastLoad` (см. §4).
- `connectivity: ""` — отдельная проверка связности не настроена (использует только ping-destination).

---

## 4. Маршрутизация — `routing`

```json
"routing": {
  "domainMatcher": "hybrid",
  "domainStrategy": "IPIfNonMatch",
  "rules": [ … ],
  "balancers": [ … ]
}
```

### 4.1. Стратегия резолва
- **`domainStrategy: "IPIfNonMatch"`** — сначала пытается сматчить правило по домену; если ни одно
  доменное правило не подошло, **резолвит домен в IP** (через DNS из §5) и повторяет матчинг по IP-правилам.
  Это позволяет `geoip:ru` ловить даже трафик, пришедший как домен.
- **`domainMatcher: "hybrid"`** — быстрый гибридный матчер доменов (производительнее `linear`).

### 4.2. Правила — порядок важен (first-match-wins)

| # | Условие | Действие | Зачем |
|---|---------|----------|-------|
| 1 | `protocol: ["bittorrent"]` | → `direct` | Торренты мимо прокси — защита зарубежных узлов от abuse/DMCA |
| 2 | `ip: ["geoip:ru"]` | → `direct` | Любой российский IP — напрямую |
| 3 | `domain: ["geosite:category-ru"]` | → `direct` | Российские домены (по встроенной geosite-базе) — напрямую |
| 4 | `domain: [ …сотни доменов… ]` | → `direct` | Ручной белый список (см. §4.3) |
| 5 | `network: "tcp,udp"` + `balancerTag: "Super_Balancer_Auto"` | → балансировщик | Весь остальной трафик — на зарубежные узлы |

> Правило №5 — «catch-all»: ловит весь оставшийся TCP и UDP и отдаёт балансировщику, а не
> конкретному outbound (`balancerTag` вместо `outboundTag`).

### 4.3. Ручной белый список доменов (правило №4)

Сотни конкретных доменов, отправляемых **напрямую**. Категории:
- **Госуслуги / гос**: `gosuslugi.ru` (+ десятки поддоменов), `kremlin.ru`, `government.ru`,
  `duma.gov.ru`, `genproc.gov.ru`, `digital.gov.ru`, `cikrf.ru`, `nsdi.ru`.
- **Банки**: Сбер (`sberbank.ru`, `online.sberbank.ru`, `id.sber.ru`), ВТБ (`vtb.ru`),
  Альфа (`alfabank.ru`), Т-Банк/Тинькофф (`tbank.ru`, `tinkoff.ru`), Почта Банк, ГПБ.
- **VK-экосистема**: `vk.ru`/`vk.com` (+ `cs7777`/`tau` тест-кластеры), `mail.ru` (огромный список
  поддоменов), `ok.ru`, `userapi.com` (`sunN-M.userapi.com` — CDN-серверы фото VK).
- **Яндекс**: `yandex.ru/.com/.net`, `ya.ru`, `dzen.ru`, `kinopoisk.ru`, `market.yandex.ru`,
  `maps`, `strm`, `mc.yandex` (метрика) и т.д.
- **Маркетплейсы / ритейл**: OZON, Wildberries (`wb.ru`, `wildberries.ru/.by`), Avito (+ `img.avito.st`
  шарды 00–99), Магнит, X5 (Пятёрочка/Перекрёсток/Чижик), ВкусВилл, Лента, ВсеИнструменты, Леруа (`lemanapro.ru`).
- **Стриминги / медиа**: Kinopoisk, IVI, Okko, KION, Premier, Wink, MTS Music, OTTPlay, Yappy,
  Rutube, СТС/ТНТ/НТВ/2x2, RBC, Lenta, Gazeta, КП.
- **Сервисы**: 2GIS (карты/тайлы/фото-шарды), HH/Headhunter/Zarplata, ЦИАН, Gismeteo, Lamoda,
  Litres, РЖД (`rzd.ru` + поддомены), Tutu, Aviasales, MAX (`max.ru` — мессенджер), T2 (Tele2).

**Зачем дублировать поверх `geosite:category-ru`:**
1. Часть доменов отсутствует/устарела в встроенной geosite-базе.
2. Банки и госсервисы **ломаются при заходе через зарубежный IP** (антифрод, геоблок, требование
   российской геолокации) → их принудительно гонят напрямую, даже если geosite их не знает.
3. CDN-шарды (`sunN-M.userapi.com`, `NN.img.avito.st`) часто резолвятся в IP, не помеченные как RU.

> ⚠️ В списке встречаются **дубли** (например `tinkoff.ru`, `hh.ru`, `ivi.ru`, `okko.tv` и др.
> перечислены дважды) и несколько записей **без префикса `domain:`** (`wildberries.ru`,
> `www.wildberries.ru`). Записи без префикса Xray трактует как `domain:` (подстрочное вхождение),
> поведение совпадает, но стиль неоднородный. На работу не влияет, но при ревизии списка стоит
> нормализовать.

### 4.4. Балансировщик — `Super_Balancer_Auto`

```json
{
  "tag": "Super_Balancer_Auto",
  "selector": ["proxy"],
  "fallbackTag": "direct",
  "strategy": {
    "type": "leastLoad",
    "settings": { "baselines": ["1s"], "expected": 2, "maxRTT": "1s", "tolerance": 0.01 }
  }
}
```

- **`selector: ["proxy"]`** — пул = все outbound'ы с тегом, начинающимся на `proxy`
  → `proxy`, `proxy-2 … proxy-8` (8 узлов, **включая** обходной `proxy-8`).
- **`strategy.type: "leastLoad"`** — выбирает узел по наименьшей задержке/нагрузке, опираясь на
  RTT из `burstObservatory`.
  - `baselines: ["1s"]` — порог «хорошего» RTT для отсева/группировки кандидатов.
  - `maxRTT: "1s"` — узлы с RTT > 1s считаются непригодными.
  - `expected: 2` — стремится держать пул из ~2 лучших узлов (распределяя между ними).
  - `tolerance: 0.01` — узлы, чей RTT отличается в пределах 1%, считаются равноценными
    (анти-флаппинг: не дёргает выбор из-за микроразницы).
- **`fallbackTag: "direct"`** — **fail-open**: если ни один узел не проходит порог (все легли / RTT хуже
  `maxRTT`), трафик уходит **напрямую**, а не блокируется. Минус для приватности (при падении всех
  узлов «закрытый» трафик пойдёт открыто), плюс для доступности.

---

## 5. DNS

```json
"dns": { "queryStrategy": "UseIP", "servers": ["1.1.1.1", "1.0.0.1"] }
```
- Резолвер — Cloudflare (`1.1.1.1` / `1.0.0.1`).
- **`queryStrategy: "UseIP"`** — запрашивает и A (IPv4), и AAAA (IPv6).
- Используется при `IPIfNonMatch` для резолва доменов перед матчингом IP-правил (`geoip:ru`).
- ⚠️ DNS-запросы идут на публичный Cloudflare; маршрут самих DNS-пакетов подчиняется общим
  правилам routing (53/udp и DoH 443 попадут под catch-all → балансировщик, если не RU).

---

## 6. Логирование — `log`

```json
"log": {
  "loglevel": "Warning",
  "dnsLog": true,
  "access": "…/group.su.ffg.happ.plus/…/Xray/logs/access.log"
}
```
- `loglevel: "Warning"` — только предупреждения и ошибки.
- `dnsLog: true` — пишет DNS-резолвы (полезно для отладки маршрутизации, но это лог истории доменов).
- `access` — путь к access-логу внутри контейнера приложения Happ.

---

## 7. Наблюдения, риски и вопросы к обсуждению

1. **`proxy-8` в общем пуле балансировщика.** Обходной WG/fragment-узел `de-4` участвует в
   `leastLoad` наравне с REALITY-узлами из-за префиксного матчинга `selector: ["proxy"]`. Вопрос:
   это задумано (он как обычный резерв) или его стоило вынести в отдельный селектор/балансировщик и
   задействовать только при срабатывании DPI на основных узлах? Сейчас при хорошем RTT он может
   выбираться для обычного трафика, хотя его профиль (фрагментация, WG, узкое окно) — для тяжёлых условий.

2. **`fallbackTag: "direct"` = fail-open.** При падении всех зарубежных узлов «закрытый» трафик
   пойдёт напрямую (раскрытие + возможный геоблок назначения). Альтернатива — `fallbackTag` на
   `block` для fail-closed, но это убивает доступность. Решение зависит от модели угроз.

3. **`block` (blackhole) не используется.** Объявлен, но ни одно правило на него не ссылается —
   нет блокировки рекламы/телеметрии на уровне ядра. Можно задействовать (например для `geosite:category-ads-all`).

4. **Дубли и нестилизованные записи в whitelist.** Есть повторы доменов и записи без `domain:`.
   Функционально безвредно, но раздувает конфиг и усложняет ревизию. Кандидат на нормализацию/дедуп.

5. **Размер whitelist vs `geosite:category-ru`.** Большой ручной список — это техдолг: его надо
   поддерживать вручную. Стоит свериться с генерацией маршрутов в проекте
   (`subscription/ru_routing.py`, `subscription/engine.py`, `docs/routing-logic.md`,
   `docs/whitelist-sharing.md`) — возможно, часть уже покрывается серверной логикой и дублируется.

6. **DNS-leak / маршрут DNS.** DNS на публичный `1.1.1.1`; для RU-доменов резолв всё равно идёт
   на Cloudflare, и только потом трафик уходит `direct`. Если важно скрывать список доменов от
   наблюдателя на RU-канале — рассмотреть DoH/DoT или RU-DNS для `direct`-веток.

7. **uTLS `random` fingerprint.** `proxy-4`/`proxy-5` используют `random` — каждое соединение с
   разным отпечатком. Иногда `random` может выдать редкий/подозрительный fingerprint; `chrome`
   стабильнее «сливается с толпой». Вопрос баланса разнообразие vs незаметность.

---

## 8. Связанные материалы в репозитории

- `subscription/ru_routing.py` — генерация RU-маршрутов (свериться с whitelist из §4.3).
- `subscription/engine.py` — движок подписки, который строит профили.
- `docs/routing-logic.md` — логика маршрутизации проекта.
- `docs/whitelist-sharing.md` — обмен/поддержка белого списка.
- `docs/happ-routing-remnawave.json` — эталонный happ-routing для Remnawave.
- `.claude/memory/project_infra_routing.md` — RU inbounds/outbounds, routing rules, zapret2.
