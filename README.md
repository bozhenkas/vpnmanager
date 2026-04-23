# vpnmanager

Self-hosted VPN cluster management bot for Telegram.  
Manages a **cascade** architecture: RU entry server → multiple exit nodes (FI, SE, …).

---

## Architecture

```
[User device]
     │  VLESS/WS
     ▼
[RU entry server]  ← this repo runs here
  ├── xray (3X-UI)        — inbound WS proxy to exit nodes
  ├── zapret / nfqws2     — anti-DPI for Russian ISPs
  ├── AdGuard Home        — DNS ad/tracker blocking
  ├── vpn-bot             — Telegram management bot
  ├── sub-updater         — background subscription sync daemon
  └── nginx               — TLS termination + subscription proxy
       │
       ├──/fi   → WireGuard/VLESS → [FI exit node]
       ├──/se   → WireGuard/VLESS → [SE exit node]
       ├──/smart          — load-balanced smart routing
       └──/subscribe/     → vpn-bot subscription server (port 9090)
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
| **vpn-bot** (`src/bot/`) | Telegram bot: add/remove users, subscriptions, routing |
| **sub-updater** (`src/sub-updater/`) | Background daemon: syncs external subscription lists into 3X-UI |
| **zapret / nfqws2** | Anti-DPI: bypasses Russian ISP deep packet inspection |
| **AdGuard Home** | DNS server with ad/tracker blocking |
| **smart-pro** (`src/smart-pro/`) | Script: applies custom RU-direct routing rules to xray |
| **nginx** | TLS (Let's Encrypt), proxies subscription endpoint |

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

### Custom RU-direct list
`src/smart-pro/apply-ru-direct.py` edits xray's routing table in the 3X-UI SQLite DB directly:
- reads `/etc/smart-pro/ru-direct-custom.txt` (one domain per line)
- inserts/updates rule `custom-ru-direct` → `direct` outbound
- supports `--dry-run`

After running, restart xray: `systemctl restart x-ui`

### Subscription updater daemon
`src/sub-updater/updater.py` runs every 10 minutes:
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

### Docker Compose (alternative)

```bash
cp .env.example .env
# fill in BOT_TOKEN, DOMAIN, PANEL_URL, PANEL_USER, PANEL_PASS
docker compose up -d
```

> Note: `network_mode: host` is required — the bot talks to 3X-UI on `localhost:25565`.

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
src/
  bot/
    vpn-bot.py          — main Telegram bot (stdlib only, no pip)
  sub-updater/
    updater.py          — subscription sync daemon
    whitelist_links.txt — external subscription URLs (not committed)
  smart-pro/
    apply-ru-direct.py  — xray routing rule manager

deploy/
  systemd/
    vpn-bot.service
    sub-updater.service
    smart-pro.service
    nfqws2.service
  nginx/
    ru.goida.fun.conf   — reference nginx vhost with WS proxying
    vpndeployer.ru.conf

docker-compose.yml      — alternative to systemd
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
