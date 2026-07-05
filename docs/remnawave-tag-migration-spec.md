# Remnawave tag migration spec (controlled)

> Phase B deliverable. **No prod execution** until owner OK + backup + RU SSH reachable.
> Reference implementation: `scripts/remna_tag_migration.py` (extends `remna_clean_rename.py` pattern).

## Goals

1. User-visible names → `goida-*` / `hydra-{cc}` style tags and subscription remarks.
2. Keep nginx paths (`/smart`, `/fin`, …) unchanged.
3. Atomic update: profile JSON + `config_profile_inbounds` + `internal_squad_inbounds` + `cpitn` + bot link builders in **one deploy window**.
4. Never hot-rename `HOME_VLESS_TCP_REALITY_7443` or break `cpitn` without backup.

## Rename map

### RU ingress profile (`11111111-7443-4000-8000-000000000001`)

| old tag | new tag | subscription remark |
|---------|---------|---------------------|
| `RU_WS_SMART` | `GOIDA_SMART` | Оптимальный 🇸🇨 |
| `RU_WS_FIN` | `GOIDA_FIN` | Финляндия 🇫🇮 |
| `RU_WS_FRA` | `GOIDA_FRA` | Франция 🇫🇷 |
| `RU_WS_SWE` | `GOIDA_SWE` | Швеция 🇸🇪 |
| `RU_WS_DIRECT` | `GOIDA_RU` | Русский (YouTube, Discord) 🇷🇺 |
| `RU_REALITY_GRPC_RESERVE` | `GOIDA_RESERVE` | Резервный 🇰🇵 (мобильная связь) |
| `RU_WS_HYDRA_DE` | `GOIDA_HYDRA_DE` | Германия 🇩🇪 (сторонний) |
| `RU_WS_HYDRA_NL` | `GOIDA_HYDRA_NL` | Нидерланды 🇳🇱 (сторонний) |
| `RU_WS_HYDRA_POL` | `GOIDA_HYDRA_POL` | Польша 🇵🇱 (сторонний) |
| `RU_WS_HYDRA_TUR` | `GOIDA_HYDRA_TUR` | Турция 🇹🇷 (сторонний) |
| `RU_WS_HOME` | *(unchanged)* | — |

### Routing ruleTags / balancers (same profile)

| old | new |
|-----|-----|
| `BALANCER_FOREIGN_SMART` | `GOIDA_BALANCER_SMART` |
| `foreign-smart-catch-all` | `goida-foreign-smart-catch-all` |
| `direct-catch-all` | `goida-direct-catch-all` |
| `direct-zapret-services-domain` | keep or prefix `goida-` (optional, low priority) |

### Foreign node profile (separate UUID — verify live)

| old | new | caution |
|-----|-----|---------|
| `REMNA_VLESS_TCP_REALITY_7443` | `GOIDA_FOREIGN_REALITY` | **must** update `config_profile_inbounds_to_nodes` + squad `SMART_REMNA` in same transaction |

### Explicitly NOT renamed

- `HOME_VLESS_TCP_REALITY_7443`
- `RU_WS_HOME`
- `HOME_REMNA` squad
- nginx `location` paths

## Pre-flight

1. Backup dir: `/root/deploy-backups/20260606-remna-tag-migration/`
2. DB backup tables (stamp `20260606_tag_migration`):
   - `config_profiles`
   - `config_profile_inbounds`
   - `internal_squad_inbounds`
   - `config_profile_inbounds_to_nodes`
   - `hosts` (if host tag refs exist)
3. Export profile JSON: `config_profiles.config` for RU + foreign profiles.
4. Confirm 5/5 nodes connected.
5. Record UTC timestamp for diagnostic log window.

## Execution order (single window)

```
1. --dry-run scripts/remna_tag_migration.py   # diff only
2. backup tables + JSON export
3. scripts/remna_tag_migration.py --apply
4. scripts/remna_host_cleanup.py --dry-run    # :7443 hosts
5. scripts/remna_host_cleanup.py --apply      # if dry-run OK
6. rsync vpn-bot + client-bot (tag-aware paths unchanged)
7. docker restart remnawave ONCE
8. remnawave push config to all nodes / bot-triggered restart
9. shadow-tests (below)
```

## Bot / client code updates (same window)

Files that reference old tags or squad-driven paths:

- `bot/vpn-bot.py` — remarks only (paths stay); hydra squad names unchanged in this wave
- `client-bot/client-bot.py` — catalog keys unchanged
- `subscription/engine.py` — routing ruleTag refs if embedded in JSON builder
- `scripts/remna_routing_spec.py` — update canonical tags after migration

## Shadow-tests (mandatory)

Users: `bozhenkas`, `test-sub`

For each profile: smart, fin, fra, swe, direct, reserve, hydra-de (if enabled):

1. Fetch `/subscribe/<token>` body (Happ JSON).
2. Run Xray from exact JSON (not hand-built).
3. Correlate `/var/log/goida/ru-ws-diagnostic.log` + remnanode access on RU + foreign node.
4. Happ ping on FIN/FRA/SWE labels.

## Rollback

```bash
# restore from backup tables (example stamp)
psql -c "delete from config_profile_inbounds where profile_uuid='11111111-7443-4000-8000-000000000001';"
psql -c "insert into config_profile_inbounds select * from config_profile_inbounds_bak_STAMP;"
# repeat for config_profiles, internal_squad_inbounds, cpitn
docker restart remnawave
```

Rollback script: `scripts/remna_tag_migration.py --rollback STAMP`

## Host cleanup (Phase C1)

Separate script `scripts/remna_host_cleanup.py`:

- DELETE `hosts` WHERE `port=7443` AND `address != '78.107.88.21'` AND remark NOT ILIKE '%home%'
- Verify subscription still emits only `ru.goida.fun:443` + `reserve.goida.fun:443`

## Risk matrix

| risk | mitigation |
|------|------------|
| cpitn cleared (incident 2026-06-05) | backup + atomic script; no manual SQL |
| WS 101 but foreign rejects UUID | verify `SMART_REMNA` includes foreign inbound after rename |
| Happ remark mismatch | deploy bots same window as DB |
| ephemeral flow patch lost | no `docker compose pull` in this window |
