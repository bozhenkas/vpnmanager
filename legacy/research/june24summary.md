# Summary: VPN/proxy обходы на 2026-06-24

Главный вывод: в 2026 уже нельзя ставить на один "магический" протокол. Нужен набор профилей: стабильный REALITY, новый XHTTP, UDP fallback, split routing и отдельный desync-слой для RU egress.

## Лучшие кандидаты

1. `VLESS + REALITY + TCP/Vision` на `:443`
   - Все еще самый практичный stable base.
   - Важно: не использовать подозрительный target/SNI, не слушать нестандартный порт без причины, не ломать flow.

2. `VLESS + gRPC + REALITY`
   - Хороший fallback/reserve профиль.
   - Особенно полезен там, где нужно выглядеть как HTTP/2-like трафик.

3. `VLESS + XHTTP + REALITY`
   - Самый интересный next-gen путь.
   - Xray-core `v26.3.27` сильно продвинул XHTTP/3, BBR, Finalmask.
   - Риск: GUI/клиенты, особенно Happ, могут импортировать advanced fields криво.

4. `Hysteria2`
   - Лучший UDP/QUIC fallback.
   - Hysteria `v2.9.2` добавила Gecko obfuscation: fragmentation QUIC handshake packets.
   - Хорош для inter-node egress, если TCP/SSH режется.

5. `zapret`/`byedpi`/`GoodbyeDPI`
   - Это не VPN-протокол, а DPI desync слой.
   - В goida правильно использовать для RU direct egress YouTube/Discord, а не для всего трафика.

## Что видно по сторонним подпискам

Нужный файл найден: `scripts/parse_subs.py`.

Это парсер сторонних VPN-подписок: читает Xray JSON/base64/plain, вытаскивает серверы, протоколы, routing rules, DNS и Happ routing.

Связанные файлы:

- `tmp/subs_raw.json` - дамп результата.
- `sub-updater/updater.py` - production updater для сторонних hydra/WL серверов.
- `.claude/lekanta-runtime/external_subscription_test.py` - smoke внешней подписки через Xray.

Из `tmp/subs_raw.json`: 6 подписок, 203 сервера, 138 unique host:port.

Топ стеков:

- `vless tcp reality`: 130
- `vless grpc reality`: 41
- `vless ws tls`: 19
- `vless xhttp reality`: 8

Вывод: рынок уже сидит в основном на VLESS REALITY TCP/gRPC; XHTTP появился, но пока не доминирует.

## ntc.party (форум ValdikSS, прочитан через FRA IPv6, 2026-06-24/25)

Доступ закрыт: `ntc.party` только по IPv6, читал гостем с FRA (единственная нода с маршрутом). Подробности по темам — `june24research.md` Appendix B. Ключевое:

1. **REALITY не сломан** (t/16061, 130k). Бьёт «сибирский блок» — поведенческий по TLS-fingerprint+подсети. Chrome/Safari/iOS-fingerprint палятся, Firefox/Edge/OkHttp проходят. → у Резервного сменили `fp=chrome→firefox`.
2. **ТСПУ блокирует сам SSH** (t/13275): key-auth дропается, password выживает; входящий трафик с зарубежных подсетей режется. → плечо Резервного увели с SSH-туннеля на HY2-UDP. Наблюдал вживую 25.06: мак→foreign SSH заморожен, мак→RU работает.
3. **QUIC/HY2 режут на мобильных** (t/20340): Tele2 — полный блок QUIC, Мегафон деградирует. → HY2 как server↔server egress ОК; клиентский «Лайт» (HY2 до телефона) на мобиле ненадёжен, основной вход держать на VLESS Reality.
4. **XHTTP**: работает на xray, но **sing-box выпилил поддержку** (t/13855) → только xray/V2RayN; бьётся с нашим Happ-`n/a`. XHTTP оставляем advanced, не default.
5. **Банят IP целых хостинг-подсетей** (Hetzner/OVH/DO/Selectel/Timeweb/Beget; 149k-тред + «16кб блок» t/22516). → провайдер/AS Резервного критичен (брали чистую RU-AS), зарубежный выход спрятан за HY2.
6. **Уязвимость VLESS-клиентов** (t/23871): локальный прокси → exit-IP детектируется; Минцифры разослало методичку детекта personal-VPN.

Сводно: новые данные форума **подтвердили и усилили** решения по Резервному (firefox-fp, HY2 вместо SSH, чистая AS, split-routing «русское direct с устройства»), а не противоречат им.

## Что делать в goida

- Stable default не трогать без Happ shadow-smoke.
- XHTTP/Reality держать как advanced/next-gen и тестировать на Happ, v2rayN, Hiddify/sing-box отдельно.
- Reserve/mobile профиль через gRPC REALITY + HY2 egress выглядит правильным.
- Telegram никогда не отправлять в `DIRECT`.
- RU/private трафик держать client-side direct, особенно для reserve.
- `googleapis/gstatic/googleusercontent` не добавлять в YouTube/zapret lists.

## Ограничения

- Habr в этой сессии не удалось прочитать напрямую: fetch/search упирался в 403/недоступный внешний маршрут.
- ntc.party тоже не прочитан: локальный DNS не резолвит, IPv6 connect не прошел, SSH через FRA timed out during banner exchange.
- Поэтому форумные/Habr выводы не внесены как факты. Файл `june24research.md` опирается на GitHub/docs и локальные дампы проекта.

## Основные источники

- Xray-core `v26.3.27`: https://github.com/XTLS/Xray-core/releases/tag/v26.3.27
- XHTTP discussion: https://github.com/XTLS/Xray-core/discussions/4113
- Xray examples: https://github.com/XTLS/Xray-examples/tree/main/VLESS-XHTTP-Reality
- sing-box `v1.13.13`: https://github.com/SagerNet/sing-box/releases/tag/v1.13.13
- Hysteria `v2.9.2`: https://github.com/apernet/hysteria/releases/tag/app/v2.9.2
- zapret `v72.12`: https://github.com/bol-van/zapret/releases/tag/v72.12
- ByeDPI `v0.17.3`: https://github.com/hufrea/byedpi/releases/tag/v0.17.3
- AmneziaWG: https://github.com/amnezia-vpn/amneziawg-go
- Happ desktop `2.18.3`: https://github.com/Happ-proxy/happ-desktop/releases/tag/2.18.3
- v2rayN `7.22.7`: https://github.com/2dust/v2rayN/releases/tag/7.22.7

