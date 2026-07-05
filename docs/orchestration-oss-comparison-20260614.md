# Lightweight workload distribution + observability — OSS comparison (2026-06-14)

Desk research only. No servers touched. Goal: pick a stack for a small cross-provider fleet that
gives (1) a native dashboard "what runs where + per-node CPU/RAM/disk + health", (2) tiny
control-plane RAM, (3) tolerance of cross-provider + one NAT-only node, (4) "simplified k8s",
explicitly NOT full Kubernetes.

## The fleet (constraints every option is judged against)

| Node | CPU/RAM | Notes |
|---|---|---|
| RU | 2 vCPU / 4 GB | primary ingress + Remnawave + Xray + nginx + zapret. Must stay lean. |
| FRA | 1 vCPU / 2 GB | idle — the offload target. |
| SWE | 1 vCPU / 4 GB | only ~2.8 GB free disk. Control/monitoring candidate but disk-bound. |
| FIN | 1 vCPU / 2 GB | exit + deployer-bot. Has Nomad binary + was a `goida` DC member. |
| HOME | 4 vCPU / 8 GB | behind residential NAT — outbound-only, EHOSTUNREACH inbound. Exit-only. |
| ru-4 | 1 vCPU / 576 MB | disk 80% full. Too small to host control plane or agents that matter. |

Incumbent: **Nomad v1.7.7** binary still on FIN+SWE; intact Grafana(53 MB)/Loki(73 MB) volumes on
SWE under `/opt/nomad/volumes`; old DC name `goida` (ru/fin/swe).

Two separable concerns — keep them separate, the best answer combines tools:
- **(a) placement/scheduling** — decide/move where bots & sub-updater run.
- **(b) observability** — per-node CPU/RAM/disk + health dashboard, plus the Grafana/Loki/Prom wish.

---

## Tool-by-tool (current 2026 facts)

### Nomad (+ optional Consul) — incumbent
- **License:** BUSL 1.1 (since Aug 2023). **Not OSI open-source** anymore; "source-available",
  free for non-production and for production within HashiCorp's Additional Use Grant (no competing
  managed Nomad service). For a private VPN fleet this grant is fine. Latest line is v2.0 (Apr 2026);
  your installed **1.7.7 is old but works** and reviving it is zero-license-cost.
- **Control-plane RAM:** single Go binary; agent idles ~60–80 MB, server stays <100 MB for a small
  cluster. Excellent for RAM-constrained boxes.
- **Native UI:** yes — built-in web UI shows jobs, allocations per node, client node resources
  (CPU/MEM), and health/drain state. It is a real "what runs where + node resources" view, though
  plainer than the PaaS dashboards. No built-in log/metrics charts (that's the Grafana/Loki job).
- **Scheduling:** real scheduler — bin-packing, constraints, restart/reschedule, batch+service+system
  jobs. Can run raw `exec`/`raw_exec` (your python bots, no Docker needed) as well as Docker.
- **NAT tolerance:** **weak spot.** Server↔client wants reliable RPC; the classic server→client
  comms and `exec` driver expect reachable clients. HOME (inbound-blocked) is awkward to run as a
  normal client. Workable only if HOME is left out of the scheduler (treat it as exit-only, which it
  already is) — i.e. Nomad covers ru/fin/swe(/fra), not HOME.
- **Ops cost:** moderate. HCL job specs, server/client roles, gossip. But **you already paid this
  cost once** — binaries + DC + volumes survive.
- **Fit:** strong on (a) + decent (b)-via-UI; HOME stays out. Reviving is cheap.

### Docker Swarm mode
- **License:** Apache-2.0 (in Docker Engine). True OSS.
- **Control-plane RAM:** very light; manager overhead is small (tens of MB beyond dockerd).
- **Native UI:** **none.** CLI only (`docker node/service ls`). Any dashboard = bolt on Portainer or
  Swarmprom (Prometheus+Grafana+cAdvisor). So Swarm alone fails hard need #1.
- **Scheduling:** yes — services, replicas, constraints, rolling updates. Mature enough, but in
  **maintenance mode** for ~5+ years; no real feature investment. Still shipped and supported.
- **NAT tolerance:** poor for HOME — overlay network + raft managers need mutual reachability; a
  NAT-only node can't be a manager and is painful as a worker.
- **Ops cost:** low if you know Docker; requires everything be containerized (bots → images).
- **Fit:** viable scheduler **only paired with Portainer/Netdata** for the dashboard; HOME excluded.

### k3s / k0s (lightweight Kubernetes)
- **License:** Apache-2.0 (both). True OSS, CNCF.
- **Control-plane RAM:** k3s server officially wants **2 GB RAM + 2 CPU** for control plane alone;
  real idle ~512 MB+ and climbs with workloads. k0s similar. On 1-vCPU/2-GB boxes this eats a
  dangerous fraction; tests show instability under real workloads on ≤1 GB nodes.
- **Native UI:** none built-in; add Rancher/Headlamp/Lens + Prometheus stack.
- **Scheduling:** full k8s scheduler — powerful but this is exactly the "full Kubernetes" the owner
  rejected (YAML, etcd/datastore, CNI, kubelet churn).
- **NAT tolerance:** poor; agents need stable API-server connectivity, HOME is a problem.
- **Ops cost:** high vs the rest; defeats the "simplified" requirement.
- **Fit:** **rejected** — too heavy for 2-GB nodes, too complex for the stated goal.

### Portainer CE (+ Agent)
- **License:** CE is zlib/OSS (free, unlimited nodes). BE is free ≤3 nodes, paid above — you don't
  need BE.
- **Control-plane RAM:** Portainer server ~100–256 MB; agent per node small.
- **Native UI:** **excellent** — manages Docker standalone/Swarm/k8s, shows containers per
  environment, basic per-container CPU/MEM stats, host info, console/logs. Good "what runs where".
- **Scheduling:** **no real scheduler.** It's a management/visibility GUI over Docker/Swarm/k8s —
  it places containers where you tell it, doesn't bin-pack or reschedule on failure by itself.
- **NAT tolerance:** Agent uses an outbound model (Edge Agent specifically tunnels out to the
  server) → **Edge Agent is genuinely NAT-friendly**, good for HOME.
- **Ops cost:** low. Click-ops.
- **Fit:** great **dashboard/visibility layer**, especially with Edge Agent for HOME; pair with
  Swarm if you want scheduling, or just use it to operate plain Docker per node.

### Komodo (formerly Monitor) — strong contender
- **License:** **GPL-3.0, fully OSS, no paid tier / no feature gates.**
- **Control-plane RAM:** Core + MongoDB typically <256 MB; Periphery agent is a small stateless Rust
  binary (very light). Good for RAM-constrained fleet.
- **Native UI:** **yes, and aimed exactly at this use case** — one web UI across many servers showing
  containers/compose stacks per server, **live CPU/MEM/DISK per node**, alerts on thresholds, and a
  browser shell/terminal. This is the cleanest match for hard-need #1.
- **Scheduling:** **declarative deploy, not a bin-packer.** Core sends instructions; Periphery
  executes Docker/compose on the target you choose. You pick the node; it deploys/redeploys and
  monitors. So it's "git-driven placement + monitoring", not auto-rescheduling.
- **NAT tolerance:** Core→Periphery is the normal model (Core reaches agent). For HOME there's a
  passive/agent-initiated option, but treat HOME as out-of-band; for ru/fin/swe/fra it's clean.
- **Ops cost:** low–moderate. Rust/TS, MongoDB dependency, compose-centric (containerize the bots).
- **Fit:** **best single tool for (a)-lite + (b)** if you're willing to containerize. Modern, GPL,
  light, purpose-built multi-server dashboard with real resource metrics.

### Coolify
- **License:** Apache-2.0 OSS.
- **Control-plane RAM:** heavy — **500 MB–1.2 GB at idle** (more with monitoring). On a 2/4 GB node
  that's a large tax.
- **Native UI:** very polished PaaS dashboard, multi-server (Swarm under the hood), app/resource view.
- **Scheduling:** deploy-oriented PaaS; Swarm-based multi-node.
- **NAT tolerance:** poor for HOME (SSH-reachable servers expected).
- **Ops cost:** low to use, but the footprint is the dealbreaker here.
- **Fit:** **rejected** for control plane — too fat for these boxes. Nice product, wrong fleet.

### Dokploy
- **License:** Apache-2.0 OSS.
- **Control-plane RAM:** **lightest PaaS — ~350 MB idle, ~0.8% CPU.** Docker-Swarm-based multi-node.
- **Native UI:** modern dashboard, multi-server, per-app + basic resource view, built-in
  monitoring; can scale across Swarm nodes.
- **Scheduling:** Swarm scheduling + compose workflows.
- **NAT tolerance:** poor for HOME (expects SSH-reachable remote servers).
- **Ops cost:** low.
- **Fit:** **best of the PaaS trio** if you want a Heroku-like UX; still excludes HOME and means
  Swarm + containerized bots. Viable #3 if Komodo/Nomad feel too bare.

### CapRover
- **License:** Apache-2.0 OSS.
- **Control-plane RAM:** moderate ~300–400 MB. Swarm-based.
- **Native UI:** app-centric dashboard; weaker multi-node resource visibility than Komodo/Dokploy.
- **Scheduling:** Swarm.
- **NAT tolerance / fit:** similar limits; nothing it does better than Dokploy for this fleet → skip.

### Dokku
- **License:** MIT OSS.
- **Single-server only** — no multi-node, no cross-host scheduling. Fails the core "distribute across
  the fleet" requirement. **Rejected** (fine for one box, not for this job).

### Uncloud — interesting wildcard
- **License:** Apache-2.0 OSS (psviderski/uncloud), actively developed (updates through Apr 2026).
- **Control-plane RAM:** **no central control plane / no quorum** — each machine keeps a synced copy
  of cluster state P2P. Very light; nothing heavy to host on SWE.
- **Native UI:** **weak point — CLI-first, no mature resource dashboard yet.** Great at placement,
  not at the "per-node CPU/RAM/disk" pane you need. Would still need Netdata/Komodo for (b).
- **Scheduling:** deploys containers across hosts, service discovery, ingress.
- **NAT tolerance:** **best in class — automatic WireGuard mesh with peer discovery + NAT
  traversal.** This is the one design that natively absorbs HOME behind NAT and cross-provider hosts.
- **Ops cost:** low conceptually; young project, smaller community, less battle-tested.
- **Fit:** the **only option that cleanly includes HOME**. Compelling if you must schedule onto the
  NAT box — but pair with a dashboard tool, and accept early-project risk.

### Netdata — observability-only angle
- **License:** Agent is GPL-3.0 OSS; self-hosted Parent/Child is free. Cloud is paid per node
  (~$4.50/node/mo Business) but **not required** — local parents work fully offline.
- **Footprint:** child ~3% of a core / ~150 MB RAM; a fully offloaded child <2% CPU, <150 MB,
  **zero disk I/O** — ideal for the disk-bound SWE and the tiny nodes.
- **UI:** **excellent per-node CPU/RAM/disk/net/health out of the box**, near-zero config, real-time.
  Parent node aggregates all children into one dashboard.
- **Scheduling:** none — pure monitoring.
- **NAT tolerance:** child→parent is **outbound streaming** → HOME streams out to the parent with no
  inbound needed. **Covers HOME for observability even when no scheduler can.**
- **Fit:** **best drop-in answer for concern (b)** with the lightest footprint and the only one that
  monitors HOME without inbound. Pairs with any scheduler.

---

## Scoreboard (this fleet)

| Tool | License | CP RAM | Native "where+resources" UI | Scheduler | NAT/HOME | Ops cost |
|---|---|---|---|---|---|---|
| **Nomad (revive)** | BUSL | ~60–100 MB | yes (plain) | real | weak (HOME out) | already paid |
| Docker Swarm | Apache | tens of MB | no | yes (maint. mode) | weak | low |
| k3s/k0s | Apache | 512 MB–2 GB | no (add-on) | full k8s | weak | high |
| Portainer CE | OSS | ~100–256 MB | **yes** | no (GUI only) | **Edge=good** | low |
| **Komodo** | **GPL-3** | **<256 MB** | **yes (CPU/MEM/DISK)** | deploy, not bin-pack | core→agent | low–med |
| Coolify | Apache | 0.5–1.2 GB | yes | Swarm | weak | low |
| Dokploy | Apache | ~350 MB | yes | Swarm | weak | low |
| CapRover | Apache | 300–400 MB | partial | Swarm | weak | low |
| Dokku | MIT | small | partial | single-node only | n/a | low |
| Uncloud | Apache | ~none (P2P) | **weak/CLI** | yes | **WireGuard=best** | low (young) |
| Netdata | GPL-3 | child ~150 MB | **yes (monitor only)** | none | **outbound=best** | very low |

---

## Recommendation

Split the two concerns — no single tool nails both "real scheduling onto a NAT node" **and** "polished
resource dashboard" for this exact fleet, so combine.

### #1 — Komodo (placement+ops, concern a-lite) + Netdata parents (concern b)
- **Why Komodo over reviving Nomad:** Komodo is true GPL OSS (no BUSL grey area), lighter to operate,
  and its UI is *purpose-built* for "what runs where + live CPU/RAM/DISK + health + alerts + shell" —
  which is hard-need #1 verbatim. It's compose-centric, matching how you'd ship the python bots and
  sub-updater as containers, and Core+Mongo stays <256 MB so it can live on SWE/FIN without crowding
  RU.
- **Why add Netdata:** it's the lightest, zero-config per-node metrics dashboard, writes ~zero disk
  (good for SWE's 2.8 GB), and — uniquely — its **child→parent streaming is outbound**, so **HOME is
  monitored despite NAT** even though nothing can schedule onto it. Run the Netdata parent on SWE
  (or FIN); children everywhere including HOME and ru-4.
- **Grafana/Loki/Prometheus wish:** keep it scoped. Netdata already satisfies the live-metrics need.
  Add Prometheus+Grafana only if you want long-retention dashboards/log search; if so, run that
  single stack on SWE — but watch SWE's 2.8 GB free disk (Loki/Prom retention must be capped) and
  consider FIN instead. The intact Grafana/Loki volumes on SWE can be reused.
- **Footprint sanity:** RU runs only light agents (Komodo Periphery + Netdata child, both small),
  protecting the Remnawave/Xray box. Control plane (Komodo Core, Netdata parent) lands on SWE or FIN.

### When to pick #2 instead — Revive Nomad (+ Netdata)
Choose this if you (a) want a **true scheduler** that bin-packs and **auto-reschedules** the bots on
failure, and (b) prefer running the python workloads as `raw_exec`/`exec` **without containerizing**.
You already have the binaries, the `goida` DC, and the Grafana/Loki volumes on SWE — revival cost is
low and it covers ru/fin/swe(/fra) cleanly. Caveats: BUSL (fine for private use), older 1.7.7 (plan
an upgrade), HCL learning curve, and HOME stays outside the scheduler. Pair with Netdata for the
dashboard either way — Nomad's own UI shows allocations/node resources but no metric charts.
Revive-vs-switch verdict: **switch to Komodo for ops simplicity + cleaner UI + clean license** unless
you specifically need real rescheduling/non-container exec, in which case **revive Nomad** — it's the
cheaper-to-resurrect path and the scheduler is genuinely better.

### When to pick #3 — Dokploy (+ its built-in monitoring)
If the owner wants a **Heroku-like PaaS UX** over bare Docker/Komodo and is fine with Docker Swarm
under the hood: Dokploy is the lightest PaaS (~350 MB), modern UI, multi-server. Still excludes HOME
and still needs Netdata if you want HOME monitored.

### Wildcard — Uncloud, only if HOME must run scheduled workloads
The single design here that **natively includes the NAT box** (auto WireGuard mesh + NAT traversal).
If a real requirement emerges to *schedule* containers onto HOME (not just monitor it), Uncloud is the
right primitive — accept its young-project risk and bolt Netdata/Komodo on for the dashboard, since
Uncloud's own UI is CLI-first and thin.

### Hard rejects for this fleet
- **k3s/k0s** — too much control-plane RAM for 2-GB nodes and exactly the "full k8s" the owner
  rejected.
- **Coolify** — 0.5–1.2 GB idle is too fat for these boxes.
- **Dokku** — single-server only; can't distribute.
- **CapRover** — nothing it beats Dokploy at here.
- **Docker Swarm alone** — no native dashboard (fails need #1); only as a scheduler under
  Portainer/Dokploy, and it's in long-term maintenance mode.

### One-line answer
**Komodo (GPL, light, purpose-built multi-server dashboard) for placement+ops, Netdata parent/child
for per-node metrics + the NAT box.** Revive Nomad instead of Komodo only if you need true
auto-rescheduling or want to run the bots un-containerized — it's cheap to bring back and the
scheduler is stronger, but heavier to operate and BUSL-licensed.

---

### Sources
- Nomad footprint / UI / 2026 release & BUSL: hostmycode.com VPS-orchestration-2026; tech.breakingcube.com Nomad-vs-k3s; developer.hashicorp.com release-notes v2.0; hashicorp.com BUSL announcement; endoflife.date/nomad.
- Komodo (GPL, <256 MB, multi-server CPU/MEM/DISK UI, Core/Periphery scheduler-vs-monitor): komo.do; blog.saltdata.ro managing-docker-komodo; noted.lol/komodo; deepwiki.com moghtech/komodo periphery-service.
- PaaS RAM (Coolify 0.5–1.2 GB, Dokploy ~350 MB, CapRover 300–400 MB): massivegrid.com dokploy-vs-coolify-vs-caprover; cherryservers.com coolify-vs-dokploy.
- Docker Swarm maintenance-mode / no native dashboard: github.com/docker/roadmap#175; portainer.io docker-swarm-monitoring-tools.
- k3s/k0s control-plane RAM: docs.k3s.io/installation/requirements; github.com/k3s-io/k3s discussion 3558; portainer.io comparing-k0s-k3s-microk8s.
- Portainer CE/BE + Edge agent: portainer.io pricing / take-3; docs.portainer.io licensing.
- Uncloud (Apache, P2P no control plane, WireGuard NAT traversal): github.com/psviderski/uncloud; uncloud.run/blog connect-docker-containers-across-hosts-wireguard.
- Netdata (GPL agent, child ~150 MB / zero disk, outbound parent/child streaming): netdata.cloud netdata-parents-streaming-replication; learn.netdata.cloud scalability; cubeapm.com netdata-pricing-review.
- Dokku single-server only: dokku.com; haloy.dev self-hosted-deployment-tools-compared.
