# L1 — critical facts

## servers
| alias | ip              | domain          | location |
|-------|-----------------|-----------------|----------|
| ru    | 83.147.255.98   | ru.goida.fun    | Moscow   |
| fin   | 77.110.108.57   | fin.goida.fun   | Finland  |
| swe   | 89.22.230.5     | swe.goida.fun   | Sweden   |
SSH port: 17904, user: root, key: ~/.ssh/id_rsa

## panel
URL: https://127.0.0.1:25565/penis
Creds: bozhenkas / 75aqtyqQUxfC7C9
DB: /etc/x-ui/x-ui.db (key: xrayTemplateConfig)

## bot
Path: /root/vpn-bot/vpn-bot.py
DB: /root/vpn-bot/bot.db
Subs: /root/vpn-bot/subscriptions/<token>
Sub server: 127.0.0.1:9090/subscribe/<token>
Owner TG ID: 294057781

## sub-updater
Path: /opt/sub-updater/updater.py
WL auto: /opt/sub-updater/whitelist_links.txt
WL manual: /opt/sub-updater/whitelist_manual.txt
Source: https://sub.whitestore.club/Mk93Kvj6vcJMUakG
Headers: User-Agent: v2box_short, X-HWID: f7bdgmo86aik45lc
Interval: 600s

## inbounds (ru)
| id | port  | path        | purpose              |
|----|-------|-------------|----------------------|
| 1  | 10001 | /fi         | → FI exit            |
| 2  | 10002 | /se         | → SE exit            |
| 3  | 10003 | /smart      | yt/discord direct+zapret, else FI/SE |
| 4  | 10004 | /home       | all direct+zapret    |
| 16 | 10005 | /smart-pro  | bozhenkas personal   |
hydra: USA/POL/TUR (7/8/9, 10011-10013), NL(13,10014), DE(14,10015), FI-ws(15,10016)

## reality keys
FI: pbk=klruGq7zVFOaSOTWjGpJh60IGffXxahIOPkbTI3ukyc sid=1cd8699b681516aa sni=web.max.ru
SE: pbk=VGILx6EdomV-ponARdbNlt4OjWCyQMwpdsF256ZKh2o sid=57b260dd31ebbbb1 sni=web.max.ru

## whitelist servers (manual)
WL1-1: 158.160.220.55  pbk=ZC4DzWDW73W4FCu3wnkG4eTbOLDRcHnutTyqbn-XWFo sid=a8904dc9fadc68cb
WL1-2: in.good-1store.cv
WL2-1: 51.250.12.101   pbk=S9wjXFiaNV25ogTVg_jxSN3_sZMKvky7QEaMazEBslM sid=958171c6fbfad8d5
WL2-2: ru.saint-1store.cv
WL3-1: 84.201.149.107  pbk=CAlp9qO94iFo9e_lZ_WtmlF4nJSQlBNJk-etZhXouxY sid=b0ca2d2014b4696c
WL3-2: in.stratagy-some.cv
РЕЗЕРВ: 217.114.14.249 pbk=vVvM5S5WaadN29o_FBplfPNIkVZzqXnc1MJVD5vGAlQ sid=a485076bc8f9b3d8
UUID: 2f1c2451-1b34-4d8b-9592-777a3945c521 (РЕЗЕРВ), 630d86b7-... (WL1-3)
sni: www.ya.ru (all WL)

## services (ru)
AdGuard Home: 127.0.0.1:5353 (dns), 127.0.0.1:3000 (ui)
MTProto: port 8443, secret c5a369161df3b3ec058446659ae604ed, mask: petrovich.ru
geo-files: /usr/local/x-ui/bin/ ru_geosite.dat + ru_geoip.dat, cron 12h
