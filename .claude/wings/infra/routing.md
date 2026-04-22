# wing: infra

## routing iron rules
- RU-domain → direct (всегда, независимо от inbound)
- inbound-10001 (/fi)  → всё не-RU через FI
- inbound-10002 (/se)  → всё не-RU через SE
- inbound-10003 (smart) → YouTube/Discord direct+zapret; остальное FI/SE leastPing
- inbound-10004 (home)  → всё direct+zapret
- inbound-10005 (smart-pro) → SOCKS5 балансер port 20000, sticky sessions

## xray config rules (критично)
1. xrayTemplateConfig в x-ui.db — источник истины
2. inbounds секция в template ВСЕГДА пустая — 3X-UI инжектит из своих таблиц
3. sniffing хранится в отдельной колонке, не в settings JSON
4. Не добавлять port-53 в routing rules array (кроме smart-pro — там нужно перед SOCKS5)
5. DNS через dns-out rule до catch-all правил
6. Observatory + leastPing — не писать кастомный балансер

## anti-dpi stack
Layer 1: Xray Freedom fragment (tlshello, 100-200 bytes)
Layer 2: zapret/nfqws — iptables POSTROUTING mangle, NFQUEUE
Применяется к: YouTube, Discord, полузаблокированные ресурсы

## geo-files
Источник: runetfreedom/russia-v2ray-rules-dat
Путь: /usr/local/x-ui/bin/ru_geosite.dat + ru_geoip.dat
Cron: каждые 12h, после обновления restart x-ui
⚠️ googleapis.com, gstatic.com, googleusercontent.com — НИКОГДА в YouTube списках

## nginx
Fallback сайт: 127.0.0.1:9000
Подписки: /subscribe/ → 127.0.0.1:9090
WS таймауты: proxy_read_timeout 3600s, proxy_send_timeout 3600s

## adguard home
DNS: 127.0.0.1:5353
UI: 127.0.0.1:3000
Списки: HaGeZi Pro, AdGuard DNS, TIF
routeOnly: false на всех inbounds
Priority DoH для: instagram/facebook/tiktok (1.1.1.1)

## smart-pro (bozhenkas only)
UUID: ae45bbfa-722d-4e77-9898-5750787074ee
Go SOCKS5 балансер: port 20000, leastPing, sticky sessions
SOCKS5 inbounds: 20001-20008

## changelog
2026-03-xx: heartbeatPeriod:30 на WS inbounds 10001/10002/10003/10004
2026-03-xx: BBR активен на всех нодах, TCP буферы 16MB
2026-04-xx: hydra inbounds NL(13), DE(14), FI-ws(15) добавлены
