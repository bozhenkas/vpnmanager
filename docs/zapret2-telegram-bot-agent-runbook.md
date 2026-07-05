# zapret2 for Telegram Bot API on RU server

Задача для ИИ-агента: поставить `zapret2` на отдельный RU-сервер друга так, чтобы сервер сам мог доставать `https://api.telegram.org` для Telegram-ботов.

Важно:

- Это не goida RU server.
- Не трогай Xray, nginx и код бота, если они уже стоят.
- Не ставь широкий профиль под YouTube/Discord/VPN. Нужен узкий outbound TCP/443 anti-DPI только под Telegram.
- Перед изменениями делай backup файлов, которые меняешь.
- Если одна и та же ошибка повторилась дважды, остановись, изучи web/docs и найди 3-5 вариантов исправления. Затем выбери самый эффективный и реализуй.

Источники:

- upstream: <https://github.com/bol-van/zapret2>
- installer: <https://github.com/bol-van/zapret2/blob/master/install_easy.sh>

## 0. Preflight

```bash
set -e

date
uname -a
cat /etc/os-release || true

ip -br addr
ip route get 8.8.8.8
WAN_IFACE="$(ip route get 8.8.8.8 | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
echo "WAN_IFACE=$WAN_IFACE"

curl -4sv --connect-timeout 5 --max-time 12 https://api.telegram.org/ -o /tmp/tg.before 2>/tmp/tg.before.log || true
tail -80 /tmp/tg.before.log
```

Если `curl` уже нормально устанавливает TLS и получает HTTP-ответ от Telegram, не ставь `zapret2`: сначала выясни проблему приложения, токена, DNS или firewall.

## 1. Dependencies

Debian/Ubuntu:

```bash
apt-get update
apt-get install -y git curl ca-certificates iproute2 iptables nftables make gcc g++ pkg-config lua5.3 liblua5.3-dev
```

## 2. Download zapret2

```bash
mkdir -p /opt
if [ ! -d /opt/zapret2/.git ]; then
  git clone https://github.com/bol-van/zapret2 /opt/zapret2
fi

cd /opt/zapret2
git pull --ff-only || true
```

Если есть release/prebuilt binaries в репозитории или архиве, используй их. Если бинарников нет, собери:

```bash
cd /opt/zapret2
make -j"$(nproc)" || make
```

Проверь бинарник:

```bash
find /opt/zapret2 -type f \( -name 'nfqws2' -o -name 'nfqws' \)
```

## 3. Telegram hostlist

```bash
mkdir -p /opt/zapret2/ipset

cat >/opt/zapret2/ipset/list-telegram.txt <<'EOF'
api.telegram.org
telegram.org
core.telegram.org
oauth.telegram.org
t.me
telegram.me
telegram.dog
EOF

: >/opt/zapret2/ipset/list-exclude.txt
: >/opt/zapret2/ipset/list-exclude-user.txt
: >/opt/zapret2/ipset/ipset-exclude.txt
: >/opt/zapret2/ipset/ipset-exclude-user.txt
```

## 4. Minimal config

Сохрани старый конфиг, если есть:

```bash
ts="$(date +%Y%m%d-%H%M%S)"
[ -f /opt/zapret2/config ] && cp -a /opt/zapret2/config "/opt/zapret2/config.bak.$ts"

WAN_IFACE="$(ip route get 8.8.8.8 | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"

cat >/opt/zapret2/config <<EOF
# zapret2 minimal config for Telegram Bot API on RU server
# goal: outbound HTTPS to api.telegram.org
# scope: only TCP/443 + Telegram hostlist

IFACE_WAN=$WAN_IFACE
WS_USER=tpws

SET_MAXELEM=65536
IPSET_OPT="hashsize 32768 maxelem \$SET_MAXELEM"

DESYNC_MARK=0x40000000
DESYNC_MARK_POSTNAT=0x20000000

NFQWS2_ENABLE=1
NFQWS2_PORTS_TCP=443
NFQWS2_PORTS_UDP=
NFQWS2_TCP_PKT_OUT=16
NFQWS2_TCP_PKT_IN=8
NFQWS2_UDP_PKT_OUT=0
NFQWS2_UDP_PKT_IN=0

NFQWS2_OPT="
--filter-tcp=443 --filter-l7=tls --hostlist=/opt/zapret2/ipset/list-telegram.txt --hostlist-exclude=/opt/zapret2/ipset/list-exclude.txt --hostlist-exclude=/opt/zapret2/ipset/list-exclude-user.txt --ipset-exclude=/opt/zapret2/ipset/ipset-exclude.txt --ipset-exclude=/opt/zapret2/ipset/ipset-exclude-user.txt --payload=tls_client_hello --lua-desync=multisplit:pos=1:seqovl=568
"

MODE_FILTER=none
FLOWOFFLOAD=donttouch
INIT_APPLY_FW=1
EOF
```

## 5. Install and start service

Сначала попробуй штатный installer upstream:

```bash
cd /opt/zapret2
chmod +x install_easy.sh || true
./install_easy.sh
```

В интерактиве выбирай:

- Linux server / generic Linux.
- Firewall backend: `nftables` для современного Debian/Ubuntu, иначе `iptables`.
- `nfqws`/`nfqws2`.
- Не включай лишние профили YouTube/Discord/QUIC.
- IPv6 выключить, если на сервере нет нормального IPv6.

После installer верни минимальный `/opt/zapret2/config`, если installer его перезаписал.

Если installer не сделал systemd unit, создай unit вручную:

```bash
cat >/etc/systemd/system/zapret2.service <<'EOF'
[Unit]
Description=zapret2 anti-DPI
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
WorkingDirectory=/opt/zapret2
ExecStart=/opt/zapret2/init.d/sysv/zapret2 start
ExecStop=/opt/zapret2/init.d/sysv/zapret2 stop
ExecReload=/opt/zapret2/init.d/sysv/zapret2 restart
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
```

Запусти:

```bash
systemctl daemon-reload
systemctl enable --now zapret2
systemctl status zapret2 --no-pager
```

## 6. Verify

```bash
curl -4sv --connect-timeout 5 --max-time 12 https://api.telegram.org/ -o /tmp/tg.after 2>/tmp/tg.after.log || true
tail -100 /tmp/tg.after.log

systemctl status zapret2 --no-pager
journalctl -u zapret2 -n 120 --no-pager
```

Успех:

- TLS handshake проходит.
- `curl` получает HTTP-ответ от `api.telegram.org`, даже если это `404`, `302` или `200`.
- Python/Node Telegram bot больше не получает `ConnectTimeout`, `Connection reset`, `Network is unreachable`, `TLS handshake timeout`.

Если есть bot token:

```bash
BOT_TOKEN='PASTE_TOKEN_HERE'
curl -4sS --connect-timeout 5 --max-time 12 "https://api.telegram.org/bot${BOT_TOKEN}/getMe"
```

## 7. Rollback

Если стало хуже:

```bash
systemctl disable --now zapret2 || true
/opt/zapret2/init.d/sysv/zapret2 stop || true
iptables-save | grep -i zapret || true
nft list ruleset | grep -i zapret || true
```

Вернуть backup конфига:

```bash
ls -lah /opt/zapret2/config.bak.*
cp -a /opt/zapret2/config.bak.YYYYMMDD-HHMMSS /opt/zapret2/config
systemctl restart zapret2
```

## 8. If it still does not work

Не перебирай флаги вслепую. Сначала собери диагностику:

```bash
curl -4sv --connect-timeout 5 --max-time 12 https://api.telegram.org/ -o /dev/null
openssl s_client -connect api.telegram.org:443 -servername api.telegram.org -brief </dev/null
resolvectl query api.telegram.org || nslookup api.telegram.org || dig api.telegram.org
systemctl status zapret2 --no-pager
journalctl -u zapret2 -n 200 --no-pager
nft list ruleset | grep -iE 'zapret|queue|443' -C 3 || true
iptables-save | grep -iE 'zapret|NFQUEUE|443' -C 3 || true
```

Затем пробуй варианты по одному, каждый раз делая `systemctl restart zapret2` и повторяя `curl -4sv https://api.telegram.org/`:

1. `--lua-desync=multisplit:pos=1:seqovl=681`
2. `--lua-desync=multidisorder:pos=1`
3. `--lua-desync=fakedsplit:pos=1`
4. Добавить fake TLS blob, если в `/opt/zapret2/binaries/` есть `tls_clienthello_www_google_com.bin`.
5. Переключить firewall backend `nftables` ↔ `iptables`, если NFQUEUE не цепляет трафик.

## 9. Notes

Цель этой установки не “починить весь интернет”, а только дать серверу исходящий доступ к Telegram Bot API. Поэтому конфиг намеренно узкий: TCP/443, TLS ClientHello, Telegram hostlist.
