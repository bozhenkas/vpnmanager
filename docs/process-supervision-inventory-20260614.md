# Process Supervision Inventory & Hardening Plan — 2026-06-14

Read-only audit of every goida-vpn process across the cluster. Goal: every
process is a tracked, supervised entity (systemd unit or container) with
auto-restart, **resource limits**, journald logging and boot-enable.

> Incident context: a co-located Remnawave panel once OOM-thrashed a 2 GB RU box
> and restarted the shared Xray, dropping all VPN servers. **Per-process
> MemoryMax/CPUQuota is a hard requirement** to make any single process
> impossible to starve the box.

Classification: **A** = already a systemd unit · **B** = container · **C** =
UNTRACKED bare process / cron-hack / while-loop.

---

## Headline findings

1. **No genuinely untracked (C) processes were found on any server.** The
   task's premise — that `sync_remna_hydra.py` runs as a bare unitless
   `while true` loop — is **stale**. It is now wrapped inside
   `sub-updater.service` on RU (see below). Every other project process is
   either a systemd unit, a container, or a PM2-managed app under
   `pm2-root.service`.
2. **The real, cluster-wide gap is resource limits.** Essentially every goida
   unit and the Remnawave docker stack run with `MemoryMax=infinity`,
   `MemoryHigh` unset, no `CPUQuota`, and `OOMScoreAdjust=0`. Nothing prevents a
   repeat of the OOM incident. This is the priority fix.
3. **Two fragile patterns** worth fixing even though they are technically
   supervised:
   - `sub-updater.service` ExecStart is `bash -lc 'while true; … sleep 1800'`.
     The bash wrapper never exits, so `Restart=always` can never act on a crash
     of the inner python — a hung/looping python is invisible to systemd.
     Should be a `oneshot` + `.timer` (matches the existing hwid-inspector /
     warn-collect / ip-watchdog pattern already used in this repo).
   - FIN `elections` app runs under **PM2** (`pm2-root.service` → pm2-runtime),
     adding a second supervisor layer with no resource accounting. Out of scope
     for VPN but noted.

---

## RU — 45.91.54.152 (ru.goida.fun) · 4 GB, 2 GB swap · **most critical box**

Hosts the live Remnawave panel + node + the RU Xray (remnanode) + both bots +
zapret2. This is the box from the OOM incident.

| Process | Class | Unit / container | RSS (MB) | Start mechanism | Proposed action |
|---|---|---|---|---|---|
| remnawave backend (node main/scheduler/processors) | B | `remnawave` (compose) | ~590 total | docker compose | Add `mem_limit`/`cpus` in compose (largest risk) |
| remnawave-db (postgres17) | B | `remnawave-db` | ~28 + conns | docker compose | `mem_limit` |
| remnawave-redis (valkey) | B | `remnawave-redis` | ~11 | docker compose | `mem_limit` |
| remnanode + rw-core + supervisord (RU Xray) | B | `remnanode` | ~215 | docker compose | **Protect**: generous limit, low OOM priority. Never sacrifice. |
| `vpn-bot.py` | A | `vpn-bot.service` | 41 | unit, `Restart=always` | Add limits + `OOMScoreAdjust=600` |
| `client-bot.py` | A | `goida-client-bot.service` | 29 | unit, `Restart=always` | Add limits + `OOMScoreAdjust=600` |
| `sync_remna_hydra.py` | A* | `sub-updater.service` | ~1 | **`bash -lc 'while true; … sleep 1800'`** | **Convert to oneshot + .timer**; add limits |
| telegram-proxy-tunnel (ssh -L 8888→FIN) | A | `telegram-proxy-tunnel.service` | 2 | unit, `Restart=always` | Add small limits, `OOMScoreAdjust=-100` (bots depend on it) |
| remnawave-admin-tunnel (ssh -L 31800→SWE) | A | `remnawave-admin-tunnel.service` | 2 | unit, `Restart=always` | Add small limits |
| `nfqws2` (zapret2 DPI) | A | started by zapret2 unit | ~? | systemd (zapret2) | Protect: low OOM priority, modest limit |
| goida-warn-collect | A | `.timer` + oneshot | n/a | timer | Add MemoryMax |
| hwid-inspector | A | `.timer` + oneshot | n/a | timer | Add MemoryMax |

*A\* = supervised but via the fragile while-loop anti-pattern.*

**No bare PPID=1 project process exists on RU** other than the ssh tunnels
(which are proper units) and zapret2's nfqws2 (started by its unit).

## FIN — 77.110.108.57 (bozhe-fin) · **2 GB, only ~220 MB free, 571 MB swap used** · fragile

Smallest box, heavily loaded (deployer-bot, belekker, dropp-server, daywith,
elections, hysteria, tinyproxy, ru4 egress xray, nomad). Memory pressure is real
here today.

| Process | Class | Unit / container | RSS (MB) | Start mechanism | Proposed action |
|---|---|---|---|---|---|
| `xray-ru4-egress` (private ru-4 egress) | A | `xray-ru4-egress.service` | 36 | unit, `Restart=on-failure` | **Protect** (VPN path): limit + `OOMScoreAdjust=-300` |
| `hysteria server` | A | `hysteria-server.service` | 12 | unit | Protect: limit + negative OOM |
| `tinyproxy` (telegram egress proxy) | A | `tinyproxy.service` | 3 | unit | Protect (bots tunnel through it): limit + neg OOM |
| remnanode (FIN Xray) | B | `remnanode` | ~155 | compose | Protect: limit, low OOM |
| deployer-bot | B | `vpndeployer-bot-1` (+pg/redis) | 13 | compose | `mem_limit`/`cpus` (sacrifice first) |
| belekker bot | B | `belekker_bot_1` (+pg) | 13 | `belekker.service`→compose | `mem_limit` |
| daywith-bot | B | `daywith-bot-1` | ~150 | compose | `mem_limit` |
| dropp-server | B | `dropp-server` | ~? | compose | `mem_limit` |
| `elections` (node) | A* | **PM2** under `pm2-root.service` | 5 | pm2-runtime | Out of scope; note PM2 double-supervision |
| nomad agent | A | `nomad.service` | ~? | unit | Add limit |

*All FIN "bare-looking" python (`bot.main`, `deployer-bot.py`) are inside docker
scopes — verified via `/proc/<pid>/cgroup`. Not type C.*

## FRA — 95.163.152.210 (raspy-peach) · 2 GB · clean

Pure VPN node. **Nothing untracked.** Only `remnanode` container + base OS.

| Process | Class | Unit / container | RSS (MB) | Action |
|---|---|---|---|---|
| remnanode + rw-core + supervisord | B | `remnanode` | ~200 | Protect: `mem_limit`, low OOM |

## SWE — 89.22.230.5 (bozhe-swe) · 4 GB · clean

Hosts the Remnawave admin stack (case211 admin bot + web back/frontend +
its own postgres) and a remnanode + nomad.

| Process | Class | Unit / container | RSS (MB) | Action |
|---|---|---|---|---|
| admin bot `python -m src.main` | B | `goida-remnawave-admin-admin-bot-1` | 210 | `mem_limit`/`cpus` (largest RSS on box) |
| admin web backend (uvicorn) | B | `…-web-backend-1` | 166 | `mem_limit` |
| admin web frontend | B | `…-web-frontend-1` | — | `mem_limit` |
| admin postgres | B | `…-remnawave-admin-db-1` | 27 | `mem_limit` |
| remnanode (SWE Xray) | B | `remnanode` | ~130 | Protect: low OOM |
| nomad agent | A | `nomad.service` | 99 | Add limit |

*`python -m src.main` / uvicorn confirmed inside docker scopes via cgroup. Not
type C.*

## RESERVE / ru-4 — 194.117.80.94 · **576 MB RAM** · tight, fragile

Tiny box. 3x-ui panel + reserve xray + the ru4→FIN ssh tunnel. Low memory makes
limits especially important here.

| Process | Class | Unit | RSS (MB) | Action |
|---|---|---|---|---|
| `x-ui` (3x-ui panel + its xray child) | A | `x-ui.service` | 78 + 22 | Add MemoryMax (panel can grow), modest OOM |
| `xray-reserve-fin` (reserve egress) | A | `xray-reserve-fin.service` | 54 | **Protect** (VPN path): limit + neg OOM |
| `ru4-fin-tunnel` (ssh -L 17905→FIN) | A | `ru4-fin-tunnel.service` | 8 | Add small limit; this is the reserve's lifeline tunnel |

## HOME — 78.107.88.21 (bozhe) · 8 GB · personal box, watchdog runner

Runs the DNS failover watchdog + RKN checker (both proper timers) plus
unrelated personal services (jellyfin, transmission, media-organizer, x-ui).

| Process | Class | Unit | Action |
|---|---|---|---|
| ip-watchdog | A | `ip-watchdog.service` + `.timer` (every 5 min) | Add MemoryMax (oneshot) |
| rkn-checker | A | `rkn-checker.service` + `.timer` (every 10 min) | Add MemoryMax |
| x-ui | A | `x-ui.service` | Add limit |
| media-organizer | A | `media-organizer.service` | Out of scope (personal) |

---

## Recommended resource limits (with rationale)

Sizing rule: `MemoryHigh ≈ 2× observed RSS` (soft throttle), `MemoryMax ≈ 3–4×`
(hard kill), `CPUQuota` generous for VPN-path, tight for bots. `OOMScoreAdjust`
makes non-critical bots die **before** xray/nginx/tunnels under pressure.

### OOM priority ladder (lower = killed last)
| Tier | OOMScoreAdjust | Members |
|---|---|---|
| Never sacrifice (VPN data path) | **-500 … -300** | remnanode/xray, xray-ru4-egress, xray-reserve-fin, hysteria, nfqws2 |
| Lifeline tunnels & proxies | **-100** | telegram-proxy-tunnel, remnawave-admin-tunnel, ru4-fin-tunnel, tinyproxy |
| Control plane | **0** | remnawave panel/db/redis, nomad, postgres |
| Sacrifice first (bots / non-VPN) | **+400 … +600** | vpn-bot, client-bot, sub-updater, admin bot, deployer-bot, belekker, daywith |

### Per-unit limits (new units in `deploy/systemd/proposed/`)

| Unit | RSS | MemoryHigh | MemoryMax | CPUQuota | OOMScoreAdjust |
|---|---|---|---|---|---|
| vpn-bot.service | 41 MB | 120M | 200M | 50% | +600 |
| goida-client-bot.service | 29 MB | 96M | 160M | 50% | +600 |
| sub-updater.service (→timer) | 1 MB | 96M | 160M | 30% | +500 |
| telegram-proxy-tunnel.service | 2 MB | 24M | 48M | 15% | -100 |
| remnawave-admin-tunnel.service | 2 MB | 24M | 48M | 15% | -100 |
| xray-ru4-egress.service (FIN) | 36 MB | 128M | 256M | 200% | -300 |
| xray-reserve-fin.service (ru-4) | 54 MB | 160M | 300M | 150% | -300 |
| ru4-fin-tunnel.service | 8 MB | 24M | 48M | 15% | -100 |
| x-ui.service (ru-4, 576 MB box) | 100 MB | 150M | 220M | 80% | 0 |
| hysteria-server.service (FIN) | 12 MB | 64M | 128M | 100% | -300 |
| tinyproxy.service (FIN) | 3 MB | 32M | 64M | 30% | -100 |

### Remnawave / container limits (compose `mem_limit` / `cpus` — **do not apply**, propose only)

RU compose `/opt/...` (the OOM-incident box, 4 GB total — leave headroom for OS + bots + Xray):
```yaml
remnawave:        { mem_limit: 768m, cpus: "1.0" }    # 3 node procs ~590MB today
remnawave-db:     { mem_limit: 512m, cpus: "0.75" }
remnawave-redis:  { mem_limit: 128m, cpus: "0.25" }
remnanode:        { mem_limit: 512m, cpus: "1.5" }    # VPN path — generous, NOT first to die
```
SWE admin stack `/opt/remnawave-admin/docker-compose.yml` (4 GB box):
```yaml
admin-bot:          { mem_limit: 384m, cpus: "0.75" }  # 210MB today
web-backend:        { mem_limit: 320m, cpus: "0.75" }  # 166MB today
web-frontend:       { mem_limit: 256m, cpus: "0.5"  }
remnawave-admin-db: { mem_limit: 256m, cpus: "0.5"  }
```
FIN (2 GB, already swapping — limits here are urgent):
```yaml
vpndeployer-bot:  { mem_limit: 256m, cpus: "0.5" }
belekker_bot:     { mem_limit: 256m, cpus: "0.5" }
daywith-bot:      { mem_limit: 256m, cpus: "0.5" }
remnanode:        { mem_limit: 384m, cpus: "1.0" }    # VPN path — protect
```

---

## Ordered apply plan (one unit at a time; each with verify + rollback)

Do **non-VPN bots first** (safe to bounce), then tunnels, then VPN-path units
last and most carefully. Apply limits via drop-ins so the base unit is untouched
and rollback is `rm drop-in && daemon-reload`.

For every step the pattern is:
```bash
# apply
mkdir -p /etc/systemd/system/<unit>.d
install -m644 <unit>-limits.conf /etc/systemd/system/<unit>.d/limits.conf
systemctl daemon-reload && systemctl restart <unit>
# verify
systemctl show <unit> -p MemoryMax,MemoryHigh,CPUQuota,OOMScoreAdjust,ActiveState,SubState
journalctl -u <unit> -n 30 --no-pager        # confirm clean start, no OOM kill
# rollback
rm /etc/systemd/system/<unit>.d/limits.conf && systemctl daemon-reload && systemctl restart <unit>
```

Order:
1. **RU** vpn-bot.service (verify bot answers in Telegram)
2. **RU** goida-client-bot.service (verify mini-app/sub endpoint 200)
3. **RU** sub-updater.service — **two changes**: (a) refactor to oneshot+timer,
   (b) add limits. Verify: `systemctl start sub-updater.service` runs once and
   exits 0; `systemctl list-timers | grep sub-updater`; confirm a Remnawave
   sync still lands. Rollback: restore old `sub-updater.service` (the current
   while-loop) from `git`/backup and `daemon-reload`.
4. **RU** telegram-proxy-tunnel + remnawave-admin-tunnel (verify `curl` through
   127.0.0.1:8888 and 127.0.0.1:31800 still works; bots stay up).
5. **FIN** tinyproxy, hysteria, then **xray-ru4-egress** (verify egress IP /
   handshake before+after; this is a live VPN path — do during low traffic).
6. **ru-4** ru4-fin-tunnel, x-ui, then **xray-reserve-fin** (verify reserve
   path; 576 MB box — watch `free -m` after each).
7. **Containers last**: apply compose `mem_limit`/`cpus` per box, recreating one
   service at a time (`docker compose up -d <svc>`), VPN-path `remnanode`
   absolutely last on each box. Verify container `State=running healthy` and a
   live VPN connect before moving on.

### Verification that the OOM ladder works (after rollout)
On RU under synthetic memory pressure, the kernel should kill `vpn-bot` /
`sub-updater` (+600/+500) long before `remnanode` (-300) or the tunnels (-100).
Confirm via `journalctl -k | grep -i oom` ordering — **test on a maintenance
window only**, never live.

---

## Explicit flags (as requested)

- **`sync_remna_hydra.py` while-loop**: NOT unitless — it is the ExecStart of
  `sub-updater.service` as `bash -lc 'while true; do … sleep 1800; done'`. Still
  fragile: `Restart=always` is dead code because the bash wrapper never exits;
  a crashing/hanging inner python is invisible to systemd. **Refactor to
  oneshot + `.timer`.** (proposed file included.)
- **autossh / ssh tunnels**: there is no `autossh` anywhere — all tunnels are
  plain `ssh -N` wrapped in proper `Restart=always` units
  (telegram-proxy-tunnel, remnawave-admin-tunnel on RU; ru4-fin-tunnel on ru-4).
  Fine, but unbounded memory — add small caps.
- **deployer-bot on FIN**: containerized (`vpndeployer-bot-1`), supervised by
  docker. Risk is the **2 GB box is already swapping (571 MB swap used,
  ~220 MB free)** with no container limits — `mem_limit` here is the most urgent
  container action in the fleet.
- **`ru4-fin-tunnel.service` (reserve's xray lifeline)**: proper unit,
  `Restart=always`, but no limit. It is the reserve path's only link to FIN;
  give it a tiny cap and `OOMScoreAdjust=-100` so it is not collateral on the
  576 MB box.
- **PM2 `elections` on FIN**: double-supervised (systemd→pm2-runtime→node), no
  resource accounting. Non-VPN; left as a note.
