# goida-vpn — каноническая логика роутинга

> **Назначение.** Единый источник истины по роутингу для людей и ИИ-агентов.
> Чтобы не объяснять логику каждый раз заново. Источник правды по фактическим
> спискам доменов/IP — старый 3x-ui конфиг (`/root/xray-config-from-old-ru.json`
> на новом RU, локальная копия `/tmp/old-ru-xray-config.json`).
>
> **Статус:** ЛОГИКА УТВЕРЖДЕНА 2026-06-11 (решения — §10). Перед мутацией прода
> остаётся read-only проверка сервера (§10 → V1–V3), затем чистка профилей.
>
> Дата: 2026-06-11. Цель: перенести точную логику 3x-ui в чистые профили Remnawave.

---

## 1. Глоссарий

| термин | значение |
|---|---|
| **ru** | русский трафик / русский сегмент (geo RU) |
| **foreign** | зарубежный трафик / зарубежный сервер-выход |
| **direct** | прямой выход с RU-ноды (freedom). **Единственный** direct-outbound. На хосте RU весь egress 80/443 проходит обфускацию zapret2 (nft матчит по dst-порту, см. §9) → отдельный `direct-zapret` не нужен, обфускация применяется ко всему. |
| ~~direct-zapret~~ | **УПРАЗДНЁН** (решение 2026-06-11). Сливаем в один `direct`, т.к. хост обфусцирует по порту, а не по метке. |
| **zapret2** | хостовый слой обфускации (nfqws2/tpws) поверх egress RU. Не xray-outbound, а nft+nfqueue на хосте |
| **home / home-exit** | резидентный (жилой) RU-выход — нода `home-goida` `78.107.88.21`. Нужен, чтобы банки/госуслуги/ру-проверяющие видели чистый жилой RU-IP (а не IP ДЦ), и чтобы RU-банкинг работал даже когда юзер за границей |
| **fallback** | поведение для geo-RU: трафик НЕ идёт на сервер. На клиенте (L1) — выход напрямую с устройства; на сервере (L2, страховка) — `direct` с RU-ноды |
| **balancer** | аутбаунд-балансировщик между несколькими равнозначными выходами |

---

## 2. Два слоя роутинга (ВАЖНО)

Роутинг разнесён на два уровня — не путать:

- **L1 — клиентский (JSON-профиль в подписке, Happ routing).**
  Генерится подпиской (`subscription/engine.py`). Главная задача:
  **geo-RU трафик уходит НАПРЯМУЮ с устройства, не доходя до сервера →
  русские сайты НЕ видят IP сервера.** Это основной путь для RU.

- **L2 — серверный (xray routing в config_profile Remnawave на RU-ноде).**
  Применяется на RU-ноде к трафику, который реально дошёл до сервера.
  Делает: выбор зарубежного выхода, спец-категории (банки→home,
  youtube/discord→обфускация, telegram→foreign), и **страховку** по geo-RU
  (если клиент L1-роутинг не применил).

> Инвариант из инцидента 2026-06-05: «Client-side Happ JSON шлёт RU/private
> напрямую с устройства. Server-side RU-правила — только страховка».

---

## 3. Аутбаунды RU-ноды (целевой чистый набор)

| outbound | тип | назначение |
|---|---|---|
| `direct` | freedom | прямой выход с RU; обфускация zapret2 на хосте по порту (единственный direct) |
| `home-exit` | vless→reality | `78.107.88.21` жилой RU-выход (банки/госуслуги/ру-проверяющие) |
| `fin` | vless→reality | `77.110.108.57:443` |
| `fra` | vless→reality | `95.163.152.210:443` |
| `swe` | vless→reality | `89.22.230.5:443` |
| `balancer-foreign` | balancer | selector `[fin, fra]`, strategy `leastLoad`, **fallbackTag `swe`** |
| `hydra-<cc>` / `hydra-bal-<cc>` | vless / balancer | **динамически** управляются sub-updater (см. §7) |
| `blocked` | blackhole | bittorrent, youtube-QUIC |
| `dns-out` | dns | только для DNS (см. §9 hard-rule) |

Чистка относительно 3x-ui: убрать `smart-pro-out`, `socks-proxy-*`,
`inbound-10020/xhttp-test`, **упразднить `direct-zapret`** (слить в `direct`).
Добавить `fra` как полноценный выход (в 3x-ui его не было — были только fin и swe).

---

## 4. Категории трафика (из 3x-ui — источник правды)

Полные списки извлечены из 3x-ui. В чистом конфиге списки «банки» и «госуслуги»
в 3x-ui дублировались (65 и 59 доменов, почти идентичны) → **объединяем в одну
категорию `ru-via-home`**. Сырые IP, лежащие как `domain:` (квирк 3x-ui),
переносим в `ip:`.

| категория | состав | назначение (L2) |
|---|---|---|
| `private` | `geoip:private` | direct |
| `bittorrent` | protocol bittorrent | blocked |
| `youtube-quic` | `geosite:youtube`,`domain:googlevideo.com` UDP/443 | **blocked** (форсим TCP для zapret2) |
| `youtube` | `geosite:youtube`,`domain:googlevideo.com` TCP | **direct** (= с обфускацией хоста) |
| `discord` | discord (домены+UDP-порты) | **direct** (= с обфускацией хоста) |
| `ru-via-home` | банки/госуслуги/маркетплейсы/ру-проверяющие — 65 доменов (sber/tinkoff/vtb/alfa/gazprombank/nalog/gosuslugi/mos/wb/ozon/avito/… + ip-проверяющие 2ip/whoer) | **home-exit** |
| `ru-vpn-checker-ip` | 16 подсетей (Яндекс 77.88/87.250/95.108/178.154…, VK/Облако 84.201/51.250/130.193) | **home-exit** |
| `telegram` | 7 доменов (t.me/telegram.org/telegra.ph/…) + 7 подсетей (91.108.x/149.154.160.0/20) | **balancer-foreign** *(3x-ui: pinned fin; бэклог: попробовать на RU с обфускацией)* |
| `ipleak` | 26 доменов (ipinfo/ipapi/ipify/ifconfig/icanhazip/…) | direct (чтобы IP-чек показывал реальный/нужный) |
| `ru-geo` | `geosite:category-ru`, `ext:ru_geoip.dat:ru`, `ext:itdog_geosite.dat:russia-inside`, `139.45.0.0/16` | **fallback** (L1 device-direct / L2 direct) |

> Полные списки — в Приложении A (ниже) и в 3x-ui конфиге.

---

## 5. Логика по инбаундам

### 5.1 Оптимальный (smart) — `GOIDA_SMART`
Порядок правил (L2, сверху вниз):
1. `private` → direct
2. `bittorrent` → blocked
3. `youtube-quic` (UDP/443) → **blocked**
4. `ru-via-home` (домены) → **home-exit**
5. `ru-vpn-checker-ip` → **home-exit**
6. `youtube` / `discord` → **direct** (обфускация на хосте)
7. `telegram` → **balancer-foreign** *(бэклог: попробовать на RU с обфускацией)*
8. `ipleak` → direct
9. `ru-geo` → **fallback** (L1: с устройства; L2-страховка: direct)
10. **catch-all (foreign)** → **balancer-foreign** `[fin,fra]` + fallback `swe`

### 5.2 Русский (ru) — `GOIDA_RU`
- **ВСЁ → `direct`.** (выход с RU + обфускация хоста). Никакого foreign.
  Профиль для YouTube/Discord/обхода без зарубежного выхода.

### 5.3 Финляндия / Франция / Швеция — `GOIDA_FIN` / `GOIDA_FRA` / `GOIDA_SWE`
Каждый — выделенный инбаунд на свой сервер (НЕ балансировщик):
1. `private` → direct
2. `ru-geo` → **fallback** (L1 device-direct / L2 direct)
3. **catch-all (foreign)** → **свой сервер** (`fin`→fin, `fra`→fra, `swe`→swe)

### 5.4 Hydra (сторонние) — динамический набор
**Управляется автоматически `sub-updater`** — НЕ хардкодим страны.
sub-updater парсит актуальную hydra-подписку, пингует каждый сервер
(несколько проверок доступности), и для КАЖДОГО живого:
- добавляет его как **outbound** (RU-нода идёт на него как клиент),
- поднимает соответствующий **inbound** (RU перераздаёт со своего инбаунда).

Логика каждого hydra-инбаунда (одинаковая):
1. `private` → direct
2. `ru-geo` → **fallback**
3. **catch-all (foreign)** → **выход своей страны**.
   Если у страны несколько живых бэкендов (DE-1, DE-2) → аутбаунд-балансировщик
   `hydra-bal-<cc>` между ними, но инбаунд у юзера ОДИН.

Состав (DE/NL/POL/TUR/USA/…) меняется сам по доступности. Наша чистка профилей
Remnawave не должна ломать эту динамику — выравниваемся под то, что ставит
sub-updater, а не фиксируем список руками.

### 5.5 Home — `HOME_*` (не переименовывать)
Жилой RU-выход. Специальный, в обычной подписке не светится как отдельная
строка. Используется как outbound `home-exit` для категории `ru-via-home`.

### 5.6 Reserve — `GOIDA_RESERVE`
Reality+gRPC :2053 через relay `reserve.goida.fun`. Резервный вход на случай
блокировки основного домена. Логика выхода = как smart (или fin) — уточнить
при реализации.

---

## 6. Зарубежный балансировщик (smart catch-all + telegram)

```yaml
balancer-foreign:
  selector: [fin, fra]      # равнозначные, leastLoad
  strategy: leastLoad        # по observatory (пинг/доступность)
  fallbackTag: swe           # swe вступает ТОЛЬКО когда fin и fra недоступны
```
Отличие от 3x-ui: там `balancer-smart` = `[fin]` (только Финляндия), swe был
отдельным инбаундом. Новая схема: fin+fra в пуле, swe — честный fallback.

---

## 7. Hydra: динамический слой (sub-updater)

**Источник истины = живая hydra-подписка, а не статичный список.**
`sub-updater` (на RU, `/opt/sub-updater/` + `sync_remna_hydra.py`):
1. парсит актуальную hydra-подписку,
2. пингует каждый сервер (несколько проверок доступности),
3. живые → добавляет как outbound + поднимает соответствующий inbound,
4. RU-нода ходит на них клиентом и перераздаёт со своих инбаундов.

Паттерн: **N живых бэкендов одной страны → 1 inbound у юзера + аутбаунд-балансир**
`hydra-bal-<cc>` (roundRobin) между ними.

Снимок из 3x-ui (только как пример формы, НЕ как фикс-список):
| страна | бэкенды | балансир |
|---|---|---|
| NL 🇳🇱 | `nl.north-1winter.cv:443`, `nld.north-1winter.cv:8443`, `nld.north-1winter.cv:443` | roundRobin (3) |
| DE 🇩🇪 | `de.smotri-shop.top:443` | — |
| POL 🇵🇱 | `188.255.163.44:443` | — |
| TUR 🇹🇷 | `try.north-1winter.cv:443` | — |
| USA 🇺🇸 | возвращается, когда живой бэкенд появится в подписке | — |

Чистка профилей Remnawave **не должна конфликтовать** с sub-updater: его
inbound'ы/outbound'ы оставляем под его управлением.

---

## 8. Сводная матрица «инбаунд × категория → выход»

| категория \ инбаунд | SMART | RU | FIN | FRA | SWE | HYDRA-x |
|---|---|---|---|---|---|---|
| private | direct | direct | direct | direct | direct | direct |
| bittorrent | blocked | blocked | blocked | blocked | blocked | blocked |
| youtube-quic udp | blocked | (zapret) | — | — | — | — |
| youtube/discord | direct | direct | foreign* | foreign* | foreign* | hydra* |
| ru-via-home | home-exit | direct | fallback | fallback | fallback | fallback |
| ru-vpn-checker-ip | home-exit | direct | fallback | fallback | fallback | fallback |
| telegram | balancer-foreign | direct | свой сервер | свой сервер | свой сервер | своя страна |
| ipleak | direct | direct | свой сервер | свой сервер | свой сервер | своя страна |
| ru-geo | fallback | direct | fallback | fallback | fallback | fallback |
| **foreign catch-all** | **balancer[fin,fra]→swe** | direct | **fin** | **fra** | **swe** | **своя страна** |

`*` для FIN/FRA/SWE/HYDRA youtube/discord/telegram отдельно НЕ выделяются —
уходят как обычный foreign на свой сервер (специальные правила только в SMART).

---

## 9. Hard-rules (никогда не нарушать)

- **RU-сайты не видят IP сервера** — geo-RU всегда уходит с устройства (L1).
- `googleapis` / `gstatic` / `googleusercontent` — НИКОГДА в youtube-списках.
- Порт 53 в роутинге — только правило `→ dns-out`, больше нигде.
- `youtube-quic` UDP/443 → blocked (форсим TCP/TLS для предсказуемой обфускации).
- Не хот-ренеймить `HOME_VLESS_TCP_REALITY_7443` / `HOME_REMNA` без бэкапа.
- Перед любой мутацией прод — бэкап (путь бэкапа в ответе).
- Источник правды для config_profile — JSON в Postgres Remnawave, не сгенеренный
  на ноде конфиг. После правки — проверять реальной подпиской + логами ноды.
- zapret2 на хосте RU матчит egress по **dst-порту 80/443**, не по SO_MARK
  (проверить на новом RU при реализации — от этого зависит, нужен ли вообще
  отдельный `direct-zapret` или хватает одного `direct`).

---

## 10. ПРИНЯТЫЕ РЕШЕНИЯ (утверждено 2026-06-11)

- **Q1. Зарубежный балансир** → `balancer-foreign = leastLoad[fin,fra]`,
  `fallbackTag = swe` (swe только при падении обоих). ✅
- **Q2. direct/direct-zapret** → **объединить в один `direct`** (хост обфусцирует
  по порту, отдельный маркер не нужен). youtube/discord/«Русский» → `direct`. ✅
- **Q3. Telegram в SMART** → общий `balancer-foreign`. Бэклог: попробовать
  telegram на RU с обфускацией (`direct`). ✅
- **Q4. youtube/discord в SMART** → `direct` (с обфускацией хоста). ✅
- **Q5. Инбаунды** → SMART, RU, FIN, FRA, SWE, HOME, RESERVE — фиксированные;
  HYDRA-* — динамические через sub-updater (по доступности, см. §7). USA вернётся
  сам, когда появится живой бэкенд. Выкинуть smart-pro, xhttp-test, socks. ✅

### Осталось проверить на сервере (read-only, перед мутацией)
- **V1.** nft/zapret2 на новом RU `45.91.54.152` реально матчит egress по
  dst-порту 80/443 (а не по SO_MARK) — подтверждает, что один `direct`
  достаточен и обфускация покрывает весь egress. Если матчит по метке — вернуть
  механизм маркировки для `direct`.
- **V2.** Текущее состояние config_profiles Remnawave (что уже стоит, какие теги,
  привязки squad/node) — снять снимок и составить точный diff к этой спеке.
- **V3.** Что сейчас ставит sub-updater (живой hydra-набор) — чтобы чистка не
  снесла его инбаунды/аутбаунды.

---

## Приложение A. Полные списки категорий

Извлечены из 3x-ui (`/tmp/old-ru-xray-config.json`). При реализации брать
дословно отсюда / из конфига.

**ru-via-home (объединённые банки+госуслуги, 65 уник.):**
sbermegamarket.ru, sbermarket.ru, magnit.ru, magnitmarket.ru, gosuslugi.ru,
esia.gosuslugi.ru, mos.ru, 2ip.ru, 2ip.io, whoer.net, sberbank.ru, tinkoff.ru,
vtb.ru, wildberries.ru, wbcdn.ru, wb.ru, wbbasket.ru, wbx.ru,
wildberries-seller.ru, wbstatic.net, ozon.ru, ozonusercontent.com, ozone.ru,
avito.ru, avito.st, lamoda.ru, lemanapro.ru, vseinstrumenty.ru, sbrf.ru,
sber.ru, tbank.ru, alfabank.ru, raiffeisen.ru, gazprombank.ru, gu-st.ru,
nalog.ru, lkfl.nalog.ru, ivi.ru, okko.tv, wink.ru, more.tv, hh.ru,
headhunter.ru, cian.ru, litres.ru, 2gis.ru, 2gis.com, gismeteo.ru, rambler.ru,
tutu.ru, vkusvill.ru, lenta.com, gorzdrav.spb.ru, gov.spb.ru, mplusdeti.ru,
dixy.ru, lk.sut.ru, cabinet.sut.ru, msk.cloud.vk.com, boosty.to
+ raw IP (перенести в ip:): 213.180.193.226, 213.180.193.135, 84.252.149.208,
188.68.217.194, 46.243.227.98

**ru-vpn-checker-ip (16):**
5.45.192.0/18, 5.255.192.0/18, 37.9.64.0/18, 37.140.128.0/18, 77.88.0.0/18,
84.252.128.0/18, 87.250.224.0/19, 93.158.128.0/18, 95.108.128.0/17,
141.8.128.0/18, 178.154.128.0/18, 185.71.76.0/22, 213.180.192.0/21,
84.201.0.0/16, 51.250.0.0/16, 130.193.0.0/16

**telegram (7 dom + 7 ip):**
t.me, telegram.me, telegram.org, telegram.dog, telegra.ph, web.telegram.org,
api.telegram.org | 91.108.4.0/22, 91.108.8.0/22, 91.108.12.0/22, 91.108.16.0/22,
91.108.56.0/22, 91.105.192.0/23, 149.154.160.0/20

**ipleak (26):**
ipinfo.io, ipapi.co, ipapi.com, ipapi.is, ipify.org, api.ipify.org, ip-api.com,
maxmind.com, geoip.maxmind.com, myip.ru, myip.com, 2ip.ru, 2ip.io, whoer.net,
db-ip.com, ip2location.com, ipgeolocation.io, abstractapi.com, bigdatacloud.net,
ipdata.co, ipv4.icanhazip.com, icanhazip.com, ifconfig.me, ifconfig.co,
checkip.amazonaws.com, ident.me

**ru-geo:** geosite:category-ru, ext:ru_geoip.dat:ru,
ext:itdog_geosite.dat:russia-inside, 139.45.0.0/16

**youtube:** geosite:youtube, domain:googlevideo.com
