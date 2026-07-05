# Telegram direct-path probe report (Phase F)

> Routing smart/fin/fra/swe/reserve **unchanged**. Research scope: `RU_WS_DIRECT` / `GOIDA_RU` egress only.

## Setup

`ip-watchdog/rkn-checker.py` extended with `TELEGRAM_ENDPOINTS` (enabled by default via `TELEGRAM_PROBE=1`):

| label | URL |
|-------|-----|
| telegram_org | https://telegram.org/ |
| t_me | https://t.me/ |
| telegram_api | https://api.telegram.org/ |
| telegram_core | https://core.telegram.org/ |

Results appended to status payload as `telegram_results` (same `rkn-check` verdict model as VPN endpoints).

Deploy on RU (when SSH reachable):

```bash
rsync -av ip-watchdog/rkn-checker.py root@45.91.54.152:/root/goida-scripts/
systemctl restart rkn-checker.timer   # or first install from deploy/systemd/
```

## Interpretation matrix

| verdict | tcp_ok | likely cause | direct zapret action |
|---------|--------|--------------|----------------------|
| OK | yes | reachable | none |
| TLS_BLOCK | yes | camouflage cert / DPI TLS | try zapret domain list only if TCP stable |
| TCP_RESET | no | IP/port block | **do not** add to zapret; fix routing stays foreign on smart |
| TIMEOUT | no | filter or blackhole | probe from RU with `rkn-check`; compare smart foreign path |

## Proposed minimal zapret diff (apply only after live probes show TCP_OK + TLS_BLOCK)

**Only if** RU direct egress probes show DPI (not IP block):

1. Add to zapret domain/ipset for RU node **direct inbound traffic** (not smart routing rules):
   - `telegram.org`, `t.me`, `api.telegram.org`, `core.telegram.org`
   - Telegram IP ranges from live `telegram_results[].sys_ip` samples
2. File targets (verify on server): `/opt/zapret2/config`, RU remnanode zapret hook — **no** Happ JSON routing edits.
3. Rollback: remove domains from list + reload zapret2.

## Live data pending

RU `45.91.54.152` was **unreachable** for agent SSH/HTTPS on 2026-06-06. Fill probe table after first `rkn-checker` run on RU:

```
# example log fields
telegram_org → OK|TLS_BLOCK|TCP_RESET  Nms
t_me         → ...
```

Owner micro-OK required before applying zapret diff.

## Explicit non-goals

- No Telegram → `DIRECT` rule on smart profile
- No Remnawave routing JSON changes in this wave
- No `docker compose pull` on Remnawave panel
