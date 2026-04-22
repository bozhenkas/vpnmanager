# goida-vpn · memory palace

## L0 — identity (always loaded)
Proj: self-hosted VPN cluster + Telegram mgmt bot + deployer bot for community.
Owner: Leonid (bozhenkas, tg 294057781).
Lang: Python 3.12, bash. No external pip in bot.
Style: terse, lowercase comments, Russian in comments/logs.

## startup protocol
1. Read L0 (this file) — done.
2. Read L1 → `.claude/memory/facts.md`
3. On demand: wing files in `.claude/wings/`

## wings
- `infra`  — servers, xray, routing, nginx, zapret
- `bot`    — vpn-bot.py architecture, state machines, subscription logic
- `deployer` — planned deploy-bot for community (WIP)

## rules
- Never guess server IPs or keys — check `wings/infra/servers.md`
- xrayTemplateConfig is source of truth, not config.json
- Sniffing column is separate in 3X-UI sqlite
- heartbeatPeriod:30 required on WS inbounds
- No port-53 rules in routing array (except smart-pro exception)
- googleapis/gstatic/googleusercontent → never in YouTube lists

## on task start
Search relevant wing before writing code.
After significant changes → append to `wings/*/changelog.md`.
