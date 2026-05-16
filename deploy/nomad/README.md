# nomad

Nomad 1.7.7 cluster for `goida`, installed on RU, FIN, and SE/SWE.

## live layout

| node | ip | role | local state |
|---|---:|---|---|
| ru | 83.147.255.98 | server + client, current leader may change | vpn entry services stay systemd for now |
| fin | 77.110.108.57 | server + client | `/opt/nomad/volumes/pgdata` |
| se/swe | 89.22.230.5 | server + client | `/opt/nomad/volumes/{grafana,loki}` |

API port `4646` is not public. Use SSH tunnel:

```bash
ssh -p 17904 -L 4646:127.0.0.1:4646 root@89.22.230.5
```

## files

- `monitoring.nomad.hcl` — Grafana + Loki pinned to SE/SWE.
- `b3-migrate-deployer.sh` — helper for moving deployer-bot from RU to FIN.

## hygiene

- keep job files in this directory;
- keep node bootstrap snippets in `nodes/` if they are added later;
- do not commit Nomad variables, tokens, database dumps, or rendered allocation data;
- FIN stores database-like persistent volumes, SE/SWE stores monitoring volumes.
