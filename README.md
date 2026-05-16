# goida-vpn

Self-hosted VPN cluster management bot for Telegram.  
Manages a **cascade** architecture: RU entry server → multiple exit nodes (FI, SE, …).

---

## Architecture

```
[Home server, RU ISP]          [Cloudflare DNS]
  ip-watchdog (timer 5m) ──────► A ru.goida.fun
        │ probe fail → failover       ↕ TTL=60
        └──► POST /notify ──────────────────────────────┐
                                                         │
[User device]                                            │
     │  VLESS/WS → ru.goida.fun                         │
     ▼                                                   ▼
[RU entry server 83.147.255.98 / .168]  ← this repo runs here
  ├── xray (3X-UI)        — inbound WS proxy to exit nodes
  ├── zapret / nfqws2     — anti-DPI for Russian ISPs
  ├── AdGuard Home        — DNS ad/tracker blocking
  ├── vpn-bot             — Telegram management bot + /notify relay
  ├── sub-updater         — background subscription sync daemon
  └── nginx               — TLS termination + subscription proxy
       │
       ├──/fi       → VLESS → [FIN exit node 77.110.108.57]
       ├──/se       → VLESS → [SWE exit node 89.22.230.5]
       ├──/home     → VLESS → [Home server — RU geo routing]
       ├──/smart    — load-balanced (FIN/SWE/hydra)
       └──/subscribe/ → vpn-bot subscription server (port 9090)
```

### Subscription URL
`https://ru.yourdomain.com/subscribe/<token>`  
Returns a newline-separated list of `vless://` links, base64-encoded for clients like Hiddify / v2rayN / NekoBox.

---

## Stack

| Component | Role |
|-----------|------|
| **xray** (via 3X-UI) | Core proxy engine, VLESS+WS inbounds |
| **3X-UI** | Web panel, manages xray config via SQLite |
| **vpn-bot** (`bot/`) | Telegram bot: add/remove users, subscriptions, routing |
| **subscription** (`subscription/`) | Happ-first subscription renderer and legacy fallback logic |
| **sub-updater** (`sub-updater/`) | Background daemon: syncs external subscription lists into 3X-UI |
| **zapret / nfqws2** | Anti-DPI: bypasses Russian ISP deep packet inspection |
| **AdGuard Home** | DNS server with ad/tracker blocking |
| **ip-watchdog** (`ip-watchdog/`) | DNS failover: мониторит primary IP с домашнего сервера (рос. ISP), при блокировке переключает A-запись через Cloudflare API, алерты через `/notify` relay |
| **nginx** | TLS (Let's Encrypt), proxies subscription endpoint |
| **Nomad** (`deploy/nomad/`) | Cluster job files and node notes for RU/FIN/SE |

---

## Features

### Multi-exit-node routing
Each inbound on the RU server tunnels to a specific exit node:

| Path | Tag | Exit |
|------|-----|------|
| `/fi` | `inbound-10001` | Finland 🇫🇮 |
| `/se` | `inbound-10002` | Sweden 🇸🇪 |
| `/smart` | `inbound-10003` | Smart (load-balanced) |
| `/smart-pro` | `inbound-10005` | Smart Pro (owner only) ⚡ |
| `/home` | `inbound-10004` | Home server (LAN routing) |

You can add more exit nodes: create a new inbound in 3X-UI on port `1000X`, add an entry to `INBOUNDS` in `vpn-bot.py`, and restart xray.

### RU-services via home server (`/home`)
Traffic tagged for `inbound-10004` is routed through your home server — useful for Russian government services (Gosuslugi, banking, etc.) that geo-block foreign IPs. The home server just needs a VLESS outbound in xray config pointing back to RU.

### zapret / nfqws2 — anti-DPI
`nfqws2.service` applies TCP fragmentation tricks in the POSTROUTING chain. Config lives in `/opt/zapret2/lua/`. Host list is pulled from `zapret-hosts.txt` and updated via `sub-updater`.

### Subscription updater daemon
`sub-updater/updater.py` runs every 10 minutes:
- fetches `whitelist_links.txt` (external subscription URLs)
- parses VLESS links
- upserts them as clients into 3X-UI via the panel API
- a fixed UUID is used so existing clients' links don't break

---

## Bot commands

| Command | Description |
|---------|-------------|
| `/start`, `/help` | Usage info |
| `/users` | List all users (inline buttons) |
| `/adduser <name>` | Create user + 3X-UI clients for all inbounds |
| `/xray` | xray process status + observatory latencies |
| `/ping` | Bot health check |
| `/info` | Open source notice |
| `/invite <name>` | (owner) Generate one-time invite link |
| `/invites` | (owner) List last 20 invites |

**Inline (per user):**
- View traffic, last seen, subscription URL
- Regenerate subscription token
- Toggle inbound access
- Delete user

### Invite system
Owner generates a link: `https://t.me/BOT_USERNAME?start=inv_CODE`  
User follows the link → bot creates 3X-UI clients → sends subscription URL.  
Invite codes are one-time and stored in `bot.db`.

---

## Deployment

### Prerequisites
- Ubuntu 22.04 / 24.04
- [3X-UI](https://github.com/MHSanaei/3x-ui) installed and running
- nginx + certbot
- Python 3.12

### Systemd (recommended)

```bash
# bot
cp deploy/systemd/vpn-bot.service /etc/systemd/system/
systemctl enable --now vpn-bot

# subscription updater
cp deploy/systemd/sub-updater.service /etc/systemd/system/
systemctl enable --now sub-updater
```

### ip-watchdog (домашний сервер)

```bash
mkdir -p /opt/ip-watchdog /etc/ip-watchdog /var/lib/ip-watchdog
cp ip-watchdog/watchdog.py /opt/ip-watchdog/
cp ip-watchdog/watchdog.env.example /etc/ip-watchdog/watchdog.env
# заполнить CF_TOKEN, NOTIFY_TOKEN
chmod 600 /etc/ip-watchdog/watchdog.env
cp deploy/systemd/ip-watchdog.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now ip-watchdog.timer
```

Подробнее: [`ip-watchdog/README.md`](ip-watchdog/README.md)

### nginx

```bash
cp deploy/nginx/ru.goida.fun.conf /etc/nginx/sites-available/yoursite.conf
# edit server_name and ssl_certificate paths
ln -s /etc/nginx/sites-available/yoursite.conf /etc/nginx/sites-enabled/
certbot --nginx -d yourdomain.com
systemctl reload nginx
```

---

## Directory structure

```
bot/
  vpn-bot.py
  ru_direct_domains.py
subscription/
  __init__.py
  engine.py
sub-updater/
  updater.py
  whitelist_manual.example.txt
ip-watchdog/
  watchdog.py           — DNS failover, Cloudflare API, /notify relay
  watchdog.env.example
  README.md
deploy/
  systemd/
    vpn-bot.service
    sub-updater.service
    nfqws2.service
  nginx/
    ru.goida.fun.conf   — reference nginx vhost with WS proxying
    vpndeployer.ru.conf
  nomad/
    monitoring.nomad.hcl
    b3-migrate-deployer.sh
    README.md
  zapret2/ru/
tests/
web/
research/
legacy/
deployer-bot/          — ignored nested repo (собственный .git)
.env.example
```

---

## Key design decisions

- **stdlib only** for `vpn-bot.py` — no pip, works on a bare Python 3.12 install.
- **3X-UI SQLite** is the single source of truth — bot reads/writes `xrayTemplateConfig` directly when needed, uses the panel API for client management.
- **Fixed UUIDs** per user — subscription links stay stable across xray restarts.
- **`heartbeatPeriod: 30`** set on all WS inbounds — prevents connection drops behind NAT.
- **No port 53 in routing rules** — avoids breaking DNS resolution inside the proxy.

---

## License

MIT — see [LICENSE](LICENSE).
