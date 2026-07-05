# Research: VPN/proxy anti-blocking methods, 2026-06-24

Дата среза: 2026-06-24. Фокус: практические VPN/proxy-протоколы и транспорты для обхода DPI/блокировок в российском контексте, плюс применимость к goida-vpn.

## Ограничения сбора

- Habr: прямой fetch/search из этой сессии не дал пригодного доступа. Попытки открыть `habr.com/ru/search` через сетевой fetch были отклонены/заканчивались 403 на внешнем маршруте. В этот файл не внесены неподтвержденные утверждения "с Habr".
- ntc.party: локальный DNS не резолвит `ntc.party`; `curl -6` не подключился. Read-only SSH через FRA в момент проверки не прошел banner exchange. Форумные наблюдения ntc.party не прочитаны и не подменены догадками.
- GitHub/docs: доступны. Основной технический слой ниже опирается на upstream release notes/docs и локальные дампы проекта.

## Короткий вывод

На июнь 2026 рабочая стратегия уже не "один протокол победит DPI", а набор разных профилей:

1. Основной клиентский профиль: `VLESS + REALITY` на `:443`, с правильным flow/fingerprint/SNI и без экзотики, которая палит IP.
2. Новый сильный кандидат: `VLESS + XHTTP + REALITY`, особенно с XHTTP/3/BBR/Finalmask, но клиентская совместимость пока важнее чистой поддержки в core.
3. Резерв для мобильных/жестких сетей: `Hysteria2`/QUIC с obfuscation/packet fragmentation и портовой вариативностью.
4. Фронт для RU-ресурсов: split routing + direct с устройства, а не гонять российские сайты через иностранный IP.
5. RU-egress bypass: `zapret`/`byedpi`/`GoodbyeDPI` остаются отдельным классом desync-инструментов для YouTube/Discord/точечных сайтов; это не замена VPN-протоколу, а слой обхода на egress.
6. Клиентская диверсификация критична: Happ, v2rayN, Hiddify/sing-box, mihomo поддерживают разные подмножества новых функций, поэтому "core поддерживает" не равно "работает у пользователя".

## Источники и свежесть

### Xray-core

- URL: https://github.com/XTLS/Xray-core/releases/tag/v26.3.27
- Published: 2026-03-27.
- Release focus: Finalmask, mKCP, Hysteria, XHTTP, REALITY, TLS ECH, WireGuard, VLESS Reverse Proxy, browser-like HTTP headers.
- Практические пункты:
  - Finalmask добавил `header-custom`, `Sudoku`, TCP fragment, UDP noise, dialer-proxy, XHTTP/3 coverage. Это расширяет пространство маскировок поверх разных транспортов.
  - XHTTP/3 получил BBR по умолчанию и параметры `force-brutal`, `udpHop`.
  - Xray добавил полный Hysteria 2 inbound/transport.
  - REALITY теперь предупреждает про non-443 и "steal apple/icloud" как действия, которые по опыту повышают риск IP-ban.
  - TLS ECH/uTLS: обновлены fingerprint-и Firefox/Safari, добавлен X25519MLKEM768-like Chrome, ECH force query по умолчанию стал строже.
  - HTTP headers browser masquerading расширен на XHTTP/WS/HU/gRPC; можно выбирать `firefox`/`edge`/`golang` UA.

Вывод для goida-vpn: текущий Remnawave/Xray `26.3.27` соответствует свежему upstream. Самое ценное для goida: XHTTP/Reality, Hysteria2 fallback, Finalmask для будущих экспериментов, строгая дисциплина REALITY target/SNI/port.

### XHTTP / Beyond REALITY

- URL: https://github.com/XTLS/Xray-core/discussions/4113
- URL examples: https://github.com/XTLS/Xray-examples/tree/main/VLESS-XHTTP-Reality
- Смысл: XHTTP позиционируется как эволюция HTTP-like transport для REALITY: лучше переносит современные HTTP паттерны, поддерживает stream modes, H2/H3, extra/xmux.
- Практические риски:
  - URI/share-link/GUI поддержка отстает от core.
  - Happ в goida уже показал симптом: native Xray smoke проходит, но Happ может импортировать/отображать XHTTP profile некорректно (`n/a`, трафик не идет).
  - XHTTP без корректной client-side упаковки хуже, чем проверенный gRPC/TCP REALITY.

Вывод: XHTTP держать как next-gen профиль и тестовый/advanced маршрут, но public default менять только после Happ/v2rayN/Hiddify smoke на реальных клиентах.

### sing-box

- URL: https://github.com/SagerNet/sing-box/releases/tag/v1.13.13
- Published: 2026-06-04.
- Репозиторий свежий: релизы и Android SFA assets обновлялись 2026-06-08.
- Роль: универсальный core/client/server toolkit для VLESS, TUIC, Hysteria2, Shadowsocks, WireGuard/TUN, rule-based routing.
- Вывод: лучший кандидат для клиентской диверсификации и тестирования non-Xray стеков. В goida полезен как эталонный клиент/генератор JSON, но Happ остается продуктовым клиентом, значит совместимость Happ важнее идеальной sing-box поддержки.

### Hysteria 2

- URL: https://github.com/apernet/hysteria/releases/tag/app/v2.9.2
- Published: 2026-05-23.
- Release notes: важные security fixes, Gecko obfuscation, QUIC handshake packet fragmentation, ACL fixes, DoH resolver fixes.
- Сильные стороны:
  - UDP/QUIC транспорт часто переживает TCP/SSH-specific freeze.
  - Хорошо подходит для inter-node egress fallback, когда TCP-туннели режутся.
  - Port hopping/UDP hop полезны против простого tuple-based блокирования.
- Слабые стороны:
  - QUIC/UDP могут быть полностью заблокированы или деградированы у некоторых операторов.
  - Требует отдельной клиентской поддержки; не все GUI одинаково хорошо импортируют HY2.

Вывод для goida-vpn: подтвердилось текущей архитектурой reserve: HY2 как egress FIN/SWE fallback практичнее, чем пытаться держать SSH/TCP-туннель через DPI.

### zapret

- URL: https://github.com/bol-van/zapret
- Latest release: https://github.com/bol-van/zapret/releases/tag/v72.12
- Published: 2026-03-12.
- Release notes: nfqws умеет сборку QUIC crypto fragments с произвольным overlap/repeats.
- Роль: desync/fake/fragment слой для обхода DPI на уровне egress, особенно TCP TLS/QUIC.
- В goida уже используется для RU direct egress YouTube/Discord. Это правильно: не делать YouTube "VPN-протоколом", а применять zapret к direct-трафику на RU node, где это нужно.
- Ограничение: zapret требует постоянной подстройки под оператора/сайт и может ломаться от обновлений DPI.

### ByeDPI

- URL: https://github.com/hufrea/byedpi
- Latest release: https://github.com/hufrea/byedpi/releases/tag/v0.17.3
- Published: 2025-09-22.
- Release notes: `--tlsminor`, `--fake-tls-mod=msize`, корректировки fake TLS от оригинального размера.
- Роль: lightweight DPI bypass, ближе к клиентскому/локальному desync-инструменту.
- Вывод: полезен как источник идей для TLS desync и быстрых пользовательских обходов, но для goida server-side ближе `zapret`, потому что он уже встроен в RU egress.

### GoodbyeDPI

- URL: https://github.com/ValdikSS/GoodbyeDPI
- Latest release: https://github.com/ValdikSS/GoodbyeDPI/releases/tag/0.2.2
- Release старый: 2022-03-21, но репозиторий живой по activity.
- Роль: Windows DPI circumvention utility.
- Вывод: важен как массовый пользовательский baseline и набор техник desync, но не как серверный VPN-протокол.

### AmneziaWG / WireGuard family

- URL: https://github.com/amnezia-vpn/amneziawg-go
- Репозиторий активен: pushed 2026-06-17.
- Роль: WireGuard-like протокол с обфускацией, ориентирован на сети, где vanilla WireGuard fingerprint режется.
- Плюсы:
  - Нативный VPN UX, TUN, привычная модель ключей.
  - Может быть удобнее для клиентов вне Happ/Xray экосистемы.
- Минусы:
  - Все еще узнаваемый класс UDP VPN, если оператор режет UDP wholesale.
  - Потребует отдельной серверной плоскости и клиентской поддержки.

Вывод: держать как альтернативный продуктовый слой/backup, не смешивать с Remnawave-first без отдельного тестового стенда.

### mihomo / Clash.Meta lineage

- URL: https://github.com/MetaCubeX/mihomo
- Latest release observed: https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.27
- Published: 2026-06-06.
- Роль: rule-based proxy core/client ecosystem. Поддерживает provider-ы, группы, routing logic, много протоколов.
- Вывод: полезен для power users и сравнения routing DSL, но в goida Clash output сейчас deliberate unsupported stub для public flow. Если возвращать Clash, надо отдельно поддержать все новые транспорты и Happ routing parity.

### Hiddify

- URL: https://github.com/hiddify/hiddify-app
- Latest release observed: https://github.com/hiddify/hiddify-app/releases/tag/v4.1.1
- Published: 2026-03-05.
- Описание репозитория: multi-platform auto-proxy client with Sing-box, Xray, TUIC, Hysteria, Reality, Trojan, SSH.
- Вывод: хороший smoke-client для проверки multi-protocol подписок. Но публичный goida UX сейчас Happ-first, значит Hiddify скорее тестовый/advanced client.

### Happ

- URL: https://github.com/Happ-proxy/happ-desktop
- Latest release observed: https://github.com/Happ-proxy/happ-desktop/releases/tag/2.18.3
- Published: 2026-06-24.
- Роль: ключевой goida-клиент. Главная практическая проблема: поддержка форматов подписки и импорт advanced Xray fields может отставать от Xray-core.
- Вывод: любую смену default transport проверять Happ shadow-smoke, а не только `xray run`/HTTP 204.

### v2rayN

- URL: https://github.com/2dust/v2rayN
- Latest release observed: https://github.com/2dust/v2rayN/releases/tag/7.22.7
- Published: 2026-06-12.
- Release note важный для будущего: Xray планирует отключить `allowInsecure` 2026-08-01, вместо этого нужен certificate pinning/fingerprint.
- Вывод: не строить новые профили на `allowInsecure`; держать корректные TLS/REALITY параметры и fingerprint.

### Shadowsocks-rust / ShadowTLS / NaiveProxy

- Shadowsocks-rust: https://github.com/shadowsocks/shadowsocks-rust, latest release observed `v1.24.0` on 2025-12-10.
- NaiveProxy: https://github.com/klzgrad/naiveproxy
- ShadowTLS: https://github.com/ihciah/shadow-tls
- Роль:
  - Shadowsocks-2022 остается легким proxy baseline, но без дополнительной маскировки легче классифицируется.
  - ShadowTLS и NaiveProxy полезны как HTTPS-like camouflage, но требуют отдельной операционной поддержки и не являются Remnawave-first.
- Вывод: полезны для отдельного "простого emergency profile", но не как главный стек goida.

## Сравнение методов

| Метод | Устойчивость к DPI | Клиентская поддержка | Операционная сложность | Для goida-vpn |
|---|---:|---:|---:|---|
| VLESS TCP REALITY Vision | высокая при правильном :443/SNI/fp | высокая | средняя | основной foreign/inter-node профиль |
| VLESS gRPC REALITY | высокая, хорошо через HTTP/2-like профиль | средняя/высокая | средняя | хороший reserve/fallback |
| VLESS XHTTP REALITY | перспективно очень высокая | пока неровная | высокая | тестовый next-gen, не default без Happ smoke |
| XHTTP/3 + Finalmask | перспективно высокая | новая/неровная | высокая | лаборатория/advanced |
| Hysteria2 | высокая в сетях, где UDP жив | средняя | средняя | лучший UDP fallback/inter-node egress |
| TUIC v5 | средняя/высокая | средняя | средняя | альтернативный QUIC, меньше goida value сейчас |
| AmneziaWG | средняя/высокая | отдельные клиенты | средняя/высокая | отдельный backup-продукт |
| Shadowsocks-2022 | средняя | высокая | низкая | emergency/simple, не главный |
| ShadowTLS/NaiveProxy | высокая при хорошем camouflage | средняя | средняя | отдельный профиль, если уходить от Xray |
| zapret/byedpi/GoodbyeDPI | высокая точечно для сайтов | не VPN-клиент | высокая настройка | RU egress для YouTube/Discord |
| MASQUE/ECH/HTTP3 proxy | перспективно | низкая/экспериментальная | высокая | watchlist |

## Локальные полевые данные goida

Найден "тот самый" файл ресерча сторонних подписок:

- `scripts/parse_subs.py`
  - docstring: "парсер сторонних VPN-подписок (xray JSON-config array + classic base64)".
  - Извлекает: серверы, routing rules direct/proxy, IP rules, DNS configs, Happ routing.
  - Поддержанные протоколы в парсере: `vless`, `vmess`, `trojan`, `hysteria2`, `hy2`, `shadowsocks`, `ss`, `tuic`.
  - Встроенный список источников: Whitestore, your-durev, 9142858, net4, 1984-mini-app, fenvpn.

Связанные файлы:

- `tmp/subs_raw.json`: сохраненный дамп парсинга сторонних подписок.
- `sub-updater/updater.py`: production updater, который забирает стороннюю подписку, парсит VLESS, группирует hydra/WL и меняет Xray/Remnawave routing.
- `tmp/lekanta-sub-updater.py`: исторический/временный Lekanta updater с похожей логикой.
- `.claude/lekanta-runtime/external_subscription_test.py`: gitignored smoke-тест внешней подписки через Xray.

Статистика `tmp/subs_raw.json`:

- subs: 6
- servers: 203
- unique host:port: 138
- transport/security:
  - `vless tcp reality`: 130
  - `vless grpc reality`: 41
  - `vless ws tls`: 19
  - `vless xhttp reality`: 8
  - `vless xhttp tls`: 2
  - `vless grpc tls`: 2
  - `vless grpc none`: 1
- routing stats:
  - direct domains: 1217
  - proxy domains: 5
  - direct IPs: 19
  - proxy IPs: 1

Полевой вывод: коммерческие/сторонние подписки уже в основном сидят на VLESS REALITY TCP/gRPC, XHTTP появился, но пока как меньшая доля. Это совпадает с goida-архитектурой: TCP/gRPC REALITY как stable base, XHTTP как next-gen, HY2 как отдельный fallback.

## Рекомендации для goida-vpn

### Оставить stable base

- Не ломать Happ JSON-routing и Remnawave-first.
- Основной профиль держать на проверенных WS/TLS ingress + foreign Reality outbounds, пока Happ XHTTP не стабилен.
- Для reserve оставить gRPC REALITY на `:443` и HY2 egress fallback.

### Развивать next-gen аккуратно

- XHTTP/Reality:
  - тестировать `stream-one`/`packet-up`/H2/H3 варианты отдельно;
  - сравнивать Happ, v2rayN, Hiddify/sing-box;
  - smoke считать пройденным только real client path, не только Xray HTTP 204.
- Finalmask:
  - рассматривать как experimental branch после чистого baseline;
  - не включать массово без наблюдения по операторам.

### Усилить split routing

- RU/private/direct rules должны оставаться client-side, особенно для reserve/mobile profile.
- Telegram не отправлять `DIRECT`.
- `googleapis/gstatic/googleusercontent` не добавлять в YouTube/zapret lists.

### Держать несколько fallback-классов

- TCP REALITY/gRPC для сетей с живым TLS/TCP.
- HY2/QUIC для сетей, где TCP/SSH режется, но UDP жив.
- zapret/direct для YouTube/Discord на RU egress.
- WL/direct external profiles как emergency, если Remnawave path деградирует.

### Что проверить дальше

1. ~~Реально прочитать ntc.party~~ — **СДЕЛАНО, см. Appendix B ниже** (читано через FRA по IPv6, гостевой доступ, сессия 2026-06-24/25).
2. Повторить Habr search из браузера/домашней сети и добавить ссылки, если есть свежие практические посты.
3. Обновить `scripts/parse_subs.py` под новые поля Xray 26.3.27: Finalmask `fm`, XHTTP extra, Hysteria2 inbound/transport, WireGuard/AmneziaWG-like sections, TLS ECH fields.
4. Сравнить `tmp/subs_raw.json` с новым запуском парсера на 2026-06-24, потому текущий дамп от 2026-06-14.

---

## Appendix B: ntc.party (форум ValdikSS) — прочитано через FRA IPv6, сессия 2026-06-24/25

> Метод: `ntc.party` доступен **только по IPv6** (`2a02:e00:ffec:4b8::1`); из RU/мака не резолвится/не маршрутизируется. Читал гостевым доступом (Discourse JSON API: `/latest.json`, `/c/<cat>/<id>.json`, `/t/<id>.json`) **с FRA `95.163.152.210`** (единственная нода кластера с рабочим IPv6-маршрутом до этого префикса; у RU нет global IPv6, у HOME есть IPv6 но нет маршрута до ntc.party, reserve без IPv6). Логин не требовался — большинство тем читаются гостем. Это закрывает «Ограничение», указанное в начале файла и в `june24summary.md` (в прошлой сессии форум прочитать не удалось).

> **Полевое наблюдение прямо во время сбора (2026-06-25):** SSH `мак → foreign-ноды (FIN/SWE/FRA :17904)` периодически **зависает на banner exchange** (TCP-handshake проходит — `nc` коннектится, но SSH-протокол замораживается), при этом SSH `мак → RU` (русский IP) работает. Это **живое подтверждение** темы t/13275 ниже: ТСПУ замораживает SSH к зарубежным IP с домашней сети. Интермиттентно (окна ~120–600с). Прямое следствие для нас: **хорошо, что Резервный больше НЕ зависит от SSH-туннеля** (перевели на HY2-UDP).

### Календарь активности (на момент чтения, июнь 2026)
Самые живые/просматриваемые темы: «Блокировка Cloudflare/OVH/Hetzner/DigitalOcean» (r1047, 149k просм.), «Сообщения о подтверждённых блокировках ТСПУ» (r931, 256k), «Мобильная сеть 09.05.2025…» (r1132, 123k), «Блокировка VLESS-…-Reality? (Нет, частичная блокировка TLS)» (r1203, 130k), «CloudFlare Warp — первый среди всех» (r849, 95k), «Zapret2: обсуждение» (r1013), «Блокировка Discord (обсуждение + обход)» (r580, 60k), «Недоступность Hetzner» (r629).

### Ключевые темы и выводы (OP + свежие посты)

**1. REALITY не сломан — бьёт поведенческий «сибирский блок».** (t/16061 «Блокировка VLESS-xtls-rprx-vision-Reality в России? (Нет, частичная блокировка TLS)», 1203 поста, 130k.)
- Консенсус прямо в заголовке: **частичная блокировка TLS**, не взлом REALITY. Шифрование по-прежнему неотличимо от настоящего TLS.
- Детект **поведенческий, по входному хопу**: TLS-fingerprint + репутация подсети/AS. Свежий пост (0ka, 18.06): «триггернуть блок через отпечаток **сафари** → потом даже обычный curl начинает блочиться»; «под сиб-блок хрома сегодня попали новые хостинги (avoro и др.)».
- **Следствие для goida (применено):** в строке Резервного сменили `fp=chrome → fp=firefox` (Chrome/Safari/iOS — во флагнутой группе fingerprint'ов; Firefox/OkHttp/Edge пока проходят). Не плодить >3 параллельных TLS к одному SNI с интервалом <100мс.

**2. ТСПУ блокирует САМ SSH.** (t/13275 «Блокировка SSH-протокола на ТСПУ», r34.)
- OP: после того как на сервере засветился детектируемый VPN-протокол (OpenVPN/Shadowsocks), **доступ по SSH к этому серверу блокируется**; характерно — **key-auth дропается, password-auth выживает**; дамп показал заморозку всех пакетов в обе стороны после определённого пакета.
- Свежие посты (09.06): «дропается весь ВХОДЯЩИЙ трафик с зарубежных подсетей», иногда рандомно успевает подключиться.
- **Следствие для goida (применено):** плечо Резервный→зарубеж переведено с `ssh -L` туннеля на **HY2-UDP** — SSH сам является целью ТСПУ, строить на нём egress нельзя.

**3. Hysteria2/QUIC режут на мобильных операторах.** (t/20340 «Hysteria2 не работает на Теле2 (полный блок QUIC)», r23.)
- OP: на **Tele2 (t2)** соединения HY2/QUIC не встают (проходит ~100КБ запроса, ~50 байт ответа), **QUIC не работает даже с российскими сервисами** (VK, Dzen, Yandex) → полный блок QUIC у оператора. На проводных — идеально.
- Свежее (31.05): деградация HY2 и на **Мегафоне** («через раз стал подключаться»). Один юзер: REALITY на московском сервере дропается, перешёл на HY2/h3 — пока держится на разных операторах.
- **Следствие для goida:** HY2 как **server↔server** egress (Резервный→FIN/SWE по магистрали ДЦ) — ОК, проверено. Но клиентский **«Оптимальный Лайт» (HY2 до телефона) на мобильном Wi-Fi/Tele2 ненадёжен**; на мобиле VLESS+Reality (TCP) держится лучше QUIC. Подтверждает выбор VLESS Reality gRPC для входа Резервного.

**4. XHTTP: работает на xray, но клиентская поддержка дырявая.** (t/13855 «Тестируем XHTTP», r434, 53k.)
- XHTTP «шинкует соединение» → выглядит как интенсивный браузинг; хорош через CDN; работает Reality с чужим доменом, steal-oneself, TLS+fallback.
- **Главный риск:** **sing-box выпилил/дропнул поддержку XHTTP** → импортируют только xray/V2RayN. Бьётся с нашим симптомом Happ XHTTP (`n/a`, Xray #5918).
- **Следствие для goida:** XHTTP оставляем как next-gen/advanced, не public default до Happ shadow-smoke. (Совпадает с основным выводом research-файла.)

**5. VLESS через Мегафон — отдельная боль.** (t/24119 «Есть ли проблемы для VLESS через Мегафон?», r11.)
- xhttp+Reality, SNI `ozon.ru`, ASN 33842 — не проходит тест соединения именно на **Мегафоне** (на МТС ок). Свежее подозрение (waj, 23.06): **тайминговые блоки во время БС** (от расстояния до базовой станции), самолётный режим временно оживляет.
- **Следствие:** выбор SNI и оператор критичны; на мобиле возможны временны́е/локационные блоки, не только протокольные.

**6. Критическая уязвимость VLESS-клиентов + методичка Минцифры.** (t/23871 «Найдена критическая уязвимость VLESS клиентов», r57, исследование.)
- Автор: все мобильные xray/sing-box клиенты поднимают **локальный прокси** → per-app split-tunneling обходится, **exit-IP прокси детектируется гарантированно**; Минцифры разослало методичку детекта personal-VPN и потребовало внедрения шпионских модулей в аккредитованные росс. приложения (контекст MAX).
- ValdikSS уточняет: любое приложение может биндиться на любой сетевой интерфейс (`SO_BINDTODEVICE`, ядро ≥5.7) → утечка inherent, не баг конкретного клиента. Про Happ: HWID = «как старфорс» (нужен сервисам для anti-share, не юзеру).
- **Следствие для goida:** аргумент за чистые AS/подсети и поведенческий anti-trigger; HWID-gate у нас уже есть.

**7. «16КБ блокировка» + бан IP зарубежных хостеров.** (t/22516 «16кб блокировка», r147, 28k; t/«Блокировка Cloudflare/OVH/Hetzner/DO», 149k; «Недоступность Hetzner», r629.)
- DPI инспектирует **только первые ~14–25КБ** соединения (для HTTP до ~24–32КБ). Обход: слать **whitelisted SNI** в fake-пакетах (стратегии zapret `--dpi-desync-fake-tls-mod=...sni=белый_домен`, GoodbyeDPI `--fake-with-sni`, byedpi `--fake-tls-mod`). Инструменты проверки: Cheburcheck, DPI Detector (Runni), dpi-ch (hyperion).
- **IP целых хостинг-подсетей банят** (Timeweb/Selectel/Beget работают только из РФ-ДЦ; Hetzner/OVH/DO/Cloudflare-подсети режут у точек обмена трафиком).
- **Следствие для goida (учтено):** провайдер/AS нового Резервного критичен — брали RU-located вход на «чистой» AS, не на известной VPN-хостинг-подсети. Зарубежный выход (FIN/SWE) спрятан за HY2-туннелем.

**8. Архитектура ТСПУ.** (t/22307 «Архитектура и принцип работы ТСПУ», r71.)
- Обсуждение TTL-фейков (низкий TTL часто не работает из-за анти-фейк защиты ТСПУ), зарубежные хостеры заблочены ближе к точкам обмена трафиком. Утекла методичка Минцифры по детекту VPN/proxy (ocr-конспект в треде).

### Прочие заметные темы (титулы, для watchlist)
- «CloudFlare Warp — первый среди всех» (r849, 95k) + «Usque — masque-клиент для Warp» (r204) — WARP/MASQUE как массовый обход; watchlist.
- «Блокировка Discord (обсуждение + обход)» (r580, 60k) — актуально для нашего zapret-пути Discord.
- «Turnable: VPN/прокси через TURN (обход БС)» (r110) — TURN-релеи как обход во время мобильных шатдаунов (БС).
- «Мосты WebTunnel в Tor Browser» (r270), «MTProto в 2026 году» (r117), «Mikrotik + VLESS + REALITY» (r83), «Sing-box vs Xray vs …?» (r30), «RealityScanner показывает странную картину» (r20) — индикаторы того, что комьюнити мониторит детектируемость REALITY.
- «Блокировка SSH-протокола на ТСПУ», «Блокировка хендшейков Telegram», «БС в зависимости от IMEI» — расширение детекта на новые протоколы/идентификаторы.

### Свод применимости к goida (что уже сделали / что на радар)
- ✅ `fp=chrome → firefox` на Резервном (t/16061).
- ✅ Уход с SSH-туннеля на HY2-UDP для Резервного (t/13275).
- ✅ RU-located вход Резервного на чистой AS; зарубежный выход за туннелем (t/16kb, t/Hetzner).
- ✅ Золотое правило split-routing (русское direct с устройства, server-side RU→block) — не палить IP сервера русским ресурсам.
- 📌 На радар: HY2-до-клиента ненадёжен на Tele2/Мегафон (QUIC-блок) — основной клиентский профиль держать на VLESS Reality; рассмотреть TURN/WARP-MASQUE как доп. fallback во время БС; следить за RealityScanner/детектом REALITY по поведению.

> Свежий June-25 дельта-срез не сделан: в момент дозапроса SSH `мак→FRA` заморожен ТСПУ (см. полевое наблюдение выше). Данные Appendix B — по чтению форума в сессии 2026-06-24/25; для регулярного мониторинга держать FRA-IPv6-доступ к ntc.party как постоянный канал.
