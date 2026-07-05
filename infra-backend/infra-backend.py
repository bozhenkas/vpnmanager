from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
WEB_ROOT = ROOT / "web"

TOKEN = os.environ.get("INFRA_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
OWNER_ID = int(os.environ.get("INFRA_OWNER_ID", "294057781"))
WEB_PORT = int(os.environ.get("INFRA_WEB_PORT", "9082"))
NETDATA_URL = os.environ.get("NETDATA_URL", "http://127.0.0.1:19999")
INIT_DATA_TTL_SECONDS = 3600
SSH_KEY = os.environ.get("INFRA_SSH_KEY", "")
SSH_STRICT_HOST_KEY = os.environ.get("INFRA_SSH_STRICT_HOST_KEY", "accept-new")
SHELL_ENABLED = os.environ.get("INFRA_SHELL_ENABLED", "0") == "1"
SHELL_TIMEOUT_MAX = int(os.environ.get("INFRA_SHELL_TIMEOUT_MAX", "120"))
SHELL_OUTPUT_MAX = int(os.environ.get("INFRA_SHELL_OUTPUT_MAX", "60000"))
AUDIT_LOG = Path(os.environ.get("INFRA_AUDIT_LOG", "/var/log/goida-infra/shell-audit.log"))

# hydra subscription URL — bot-managed setting, pushed to RU + Reserve as
# /etc/goida-hydra/source.env, then an immediate resync is triggered on both
# (instead of waiting for the next sub-updater/reserve-hydra-sync timer tick).
HYDRA_STATE_FILE = Path(os.environ.get("INFRA_HYDRA_STATE_FILE", "/etc/goida-infra/hydra-url.json"))
HYDRA_REMOTE_ENV_PATH = os.environ.get("INFRA_HYDRA_REMOTE_ENV_PATH", "/etc/goida-hydra/source.env")
HYDRA_TARGET_NODES = ("ru", "ru4")  # ru4 = reserve entry in NODES below (host/ssh unchanged, id historical)

# Static node registry — hostname must match Netdata mirrored_hosts entry
NODES = [
    {"id": "ru",  "name": "RU",    "role": "primary", "host": "ru.goida.fun",
     "ssh_host": "45.91.54.152", "ssh_port": 17904,
     "services": ["nginx", "vpn-bot", "goida-client-bot", "remnawave", "remnanode"]},
    {"id": "fin", "name": "FIN",   "role": "vpn",     "host": "bozhe-fin",
     "ssh_host": "77.110.108.57", "ssh_port": 17904,
     "services": ["remnanode", "xray-ru4-egress", "hysteria-server", "tinyproxy"]},
    {"id": "swe", "name": "SWE",   "role": "vpn",     "host": "bozhe-swe.aeza.network",
     "ssh_host": "89.22.230.5", "ssh_port": 17904,
     "services": ["remnanode"]},
    {"id": "fra", "name": "FRA",   "role": "control", "host": "raspy-peach.ptr.network",
     "ssh_host": "95.163.152.210", "ssh_port": 17904,
     "services": ["goida-infra-backend", "netdata", "remnanode"]},
    {"id": "ru4", "name": "RESERVE",  "role": "reserve", "host": "reserve.goida.fun",
     "ssh_host": "31.77.169.26", "ssh_port": 17904,
     "services": ["xray-reserve", "reserve-hydra-sync", "hysteria-client-fin", "hysteria-client-swe"]},
]

_NODE_BY_ID = {n["id"]: n for n in NODES}
_SHELL_LOCKS = {n["id"]: threading.Lock() for n in NODES}

# container classification: vpn / bot / other
_VPN_KEYS = ("remna", "xray", "hysteria", "wireguard", "warp", "zapret",
             "marzban", "x-ui", "3x-ui", "vless", "singbox", "sing-box",
             "outline", "shadowsocks")
_TUNNEL_KEYS = ("tunnel", "ssh", "tinyproxy", "proxy")
_OBS_KEYS = ("netdata", "node_exporter", "prometheus", "grafana", "loki")

ROUTES = [
    {"id": "client_ru", "from": "clients", "to": "ru", "label": "public ingress", "kind": "measured"},
    {"id": "smart_fin", "from": "ru", "to": "fin", "label": "smart foreign / fin", "kind": "inferred"},
    {"id": "smart_fra", "from": "ru", "to": "fra", "label": "smart foreign / fra", "kind": "inferred"},
    {"id": "smart_swe", "from": "ru", "to": "swe", "label": "smart foreign / swe", "kind": "inferred"},
    {"id": "ru_home", "from": "ru", "to": "home", "label": "gov/banks safety", "kind": "inferred"},
    {"id": "reserve_fin", "from": "ru4", "to": "fin", "label": "reserve direct egress", "kind": "inferred"},
    {"id": "hydra", "from": "ru", "to": "hydra", "label": "hydra external", "kind": "inferred"},
]


def classify(name: str) -> str:
    n = name.lower()
    if "bot" in n:
        return "bot"
    if any(k in n for k in _VPN_KEYS):
        return "vpn"
    if any(k in n for k in _TUNNEL_KEYS):
        return "tunnel"
    if any(k in n for k in _OBS_KEYS):
        return "observability"
    return "other"


def relation_for(name: str) -> str:
    group = classify(name)
    if group == "vpn":
        return "vpn path"
    if group == "bot":
        return "control"
    if group == "tunnel":
        return "link"
    if group == "observability":
        return "telemetry"
    return "host"


def validate_init_data(init_data: str) -> dict:
    if not TOKEN:
        raise ValueError("bot token is not configured")
    parsed = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    data = dict(parsed)
    received = data.pop("hash", "")
    if not received:
        raise ValueError("hash missing")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    actual = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(actual, received):
        raise ValueError("bad initData hash")
    auth_date_raw = data.get("auth_date", "")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise ValueError("bad auth_date") from exc
    age = int(time.time()) - auth_date
    if age < 0 or age > INIT_DATA_TTL_SECONDS:
        raise ValueError("initData expired")
    user = json.loads(data.get("user", "{}"))
    if not user.get("id"):
        raise ValueError("user missing")
    return user


def owner_user(headers: dict) -> dict:
    init_data = headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        raise PermissionError("missing initData")
    user = validate_init_data(init_data)
    if int(user["id"]) != OWNER_ID:
        raise PermissionError("not authorized")
    return user


def netdata_get(path: str) -> dict:
    url = f"{NETDATA_URL}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"netdata {path}: HTTP {exc.code}") from exc
    except Exception as exc:
        raise RuntimeError(f"netdata {path}: {exc}") from exc


def _row(host: str, chart: str) -> tuple[list, list]:
    base = f"/host/{urllib.parse.quote(host, safe='')}"
    d = netdata_get(f"{base}/api/v1/data?chart={chart}&after=-1&points=1&group=average")
    labels = d.get("labels", [])
    row = (d.get("data") or [[]])[0]
    return labels, row


def _vals(labels: list, row: list) -> dict:
    return {labels[i]: row[i] for i in range(len(labels)) if i < len(row) and labels[i] != "time"}


def get_online_hosts() -> set[str]:
    try:
        info = netdata_get("/api/v1/info")
        return set(info.get("mirrored_hosts", []))
    except Exception:
        return set()


def list_container_names(host: str) -> list[str]:
    base = f"/host/{urllib.parse.quote(host, safe='')}"
    try:
        charts = netdata_get(f"{base}/api/v1/charts").get("charts", {})
    except Exception:
        return []
    names = set()
    for k in charts:
        if k.startswith("cgroup_"):
            names.add(k[len("cgroup_"):].split(".")[0])
    return sorted(names)


def container_counts(host: str) -> dict:
    counts = {"vpn": 0, "bot": 0, "tunnel": 0, "observability": 0, "other": 0, "total": 0}
    for n in list_container_names(host):
        counts[classify(n)] += 1
        counts["total"] += 1
    return counts


def container_metrics(host: str, name: str) -> dict:
    """cpu% (user+system), mem MiB, pids, running status for one container."""
    out = {"name": name, "group": classify(name), "running": False,
           "relation": relation_for(name), "cpu": None, "mem_mib": None,
           "pids": None, "uptime_sec": None, "image": ""}
    chart = f"cgroup_{name}"
    try:
        labels, row = _row(host, f"{chart}.cpu")
        v = _vals(labels, row)
        out["cpu"] = round(sum(x for x in v.values() if isinstance(x, (int, float))), 1)
        out["running"] = True
    except Exception:
        pass
    try:
        labels, row = _row(host, f"{chart}.mem_usage")
        v = _vals(labels, row)
        out["mem_mib"] = round(v.get("ram", 0) or 0, 1)
    except Exception:
        pass
    try:
        labels, row = _row(host, f"{chart}.pids_current")
        v = _vals(labels, row)
        # pids chart has a single dimension (often 'pids')
        out["pids"] = int(next((x for x in v.values() if isinstance(x, (int, float))), 0))
    except Exception:
        pass
    return out


def ssh_base(node: dict) -> list[str]:
    if not SSH_KEY:
        raise RuntimeError("INFRA_SSH_KEY is not configured")
    return [
        "ssh", "-i", SSH_KEY,
        "-p", str(node.get("ssh_port", 17904)),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        "-o", "LogLevel=ERROR",
        "-o", f"StrictHostKeyChecking={SSH_STRICT_HOST_KEY}",
        f"{node.get('ssh_user', 'root')}@{node['ssh_host']}",
    ]


def run_ssh(node: dict, remote_cmd: str, timeout: int = 10) -> dict:
    started = time.time()
    proc = subprocess.run(
        ssh_base(node) + [remote_cmd],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return {
        "exit_code": proc.returncode,
        "duration_ms": int((time.time() - started) * 1000),
        "output": out[-SHELL_OUTPUT_MAX:],
        "truncated": len(out) > SHELL_OUTPUT_MAX,
    }


def read_node_probe(node: dict) -> dict:
    if not SSH_KEY:
        return {"available": False, "error": "INFRA_SSH_KEY is not configured"}
    services = " ".join(shlex.quote(s) for s in node.get("services", []))
    command = (
        "set -u; "
        "echo '[facts]'; hostname; date -Is; uptime -p; "
        "echo '[services]'; "
        f"for u in {services}; do systemctl is-active \"$u\" 2>/dev/null | sed \"s|^|$u |\"; done; "
        "echo '[top]'; ps -eo comm,%cpu,%mem,rss --sort=-rss | head -n 7; "
        "echo '[warnings]'; journalctl -p warning..alert -n 8 --no-pager 2>/dev/null"
    )
    try:
        result = run_ssh(node, command, timeout=12)
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    return {"available": result["exit_code"] == 0, **parse_probe_output(result["output"])}


def parse_probe_output(text: str) -> dict:
    sections: dict[str, list[str]] = {"facts": [], "services": [], "top": [], "warnings": []}
    current = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line.strip("[]")
            continue
        if current in sections and line:
            sections[current].append(line)
    services = []
    for line in sections["services"]:
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            services.append({"name": parts[0], "state": parts[1]})
    top = []
    for line in sections["top"][1:]:
        parts = line.split()
        if len(parts) >= 4:
            top.append({"name": parts[0], "cpu": parts[1], "mem": parts[2], "rss_kb": parts[3]})
    return {
        "facts": {
            "hostname": sections["facts"][0] if len(sections["facts"]) > 0 else "",
            "time": sections["facts"][1] if len(sections["facts"]) > 1 else "",
            "uptime": sections["facts"][2] if len(sections["facts"]) > 2 else "",
        },
        "services": services,
        "top_processes": top,
        "warnings": sections["warnings"][-8:],
    }


def audit_shell(user: dict, node_id: str, command: str, result: dict, action: str = "shell") -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": user.get("id"),
        "node": node_id,
        "action": action,
        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        "command_preview": command[:200],
        "exit_code": result.get("exit_code"),
        "duration_ms": result.get("duration_ms"),
    }
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def shell_command(node: dict, command: str, timeout: int) -> dict:
    quoted = shlex.quote(command)
    remote = f"bash -lc {quoted}"
    return run_ssh(node, remote, timeout=timeout)


# ── hydra subscription URL (bot-managed setting) ──
#
# Reuses the existing per-node SSH mechanism (ssh_base/run_ssh) instead of
# building parallel infra: writing the shared source.env and (re)starting the
# sync unit are both just remote shell commands, same as handle_shell, but
# triggered internally (not raw owner-supplied shell) and always audited.

def load_hydra_url() -> str:
    try:
        data = json.loads(HYDRA_STATE_FILE.read_text())
        return str(data.get("sub_url", ""))
    except Exception:
        return ""


def save_hydra_url(url: str, user: dict) -> None:
    HYDRA_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sub_url": url,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": user.get("id"),
    }
    tmp = HYDRA_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    os.replace(tmp, HYDRA_STATE_FILE)


def push_hydra_url(node_id: str, url: str, user: dict) -> dict:
    """Write source.env on one node and trigger its sync unit immediately."""
    node = _NODE_BY_ID[node_id]
    unit = "sub-updater.service" if node_id == "ru" else "reserve-hydra-sync.service"
    env_dir = shlex.quote(str(Path(HYDRA_REMOTE_ENV_PATH).parent))
    env_path = shlex.quote(HYDRA_REMOTE_ENV_PATH)
    url_quoted = shlex.quote(url)
    write_cmd = (
        f"mkdir -p {env_dir} && "
        f"printf 'SUB_URL=%s\\n' {url_quoted} > {env_path}.tmp && "
        f"mv {env_path}.tmp {env_path} && "
        f"systemctl start {unit}"
    )
    result = shell_command(node, write_cmd, timeout=90)
    audit_shell(user, node_id, write_cmd, result, action="hydra_url_set")
    return result


def run_remnawave_sql(sql: str) -> list[dict]:
    ru = _NODE_BY_ID["ru"]
    remote = (
        "docker exec remnawave-db psql -U postgres -d postgres "
        "-At -F '|' -c " + shlex.quote(sql)
    )
    data = run_ssh(ru, remote, timeout=15)
    if data["exit_code"] != 0:
        raise RuntimeError(data["output"] or "remnawave sql failed")
    rows = []
    for line in data["output"].splitlines():
        if not line.strip():
            continue
        name, connected, used, tracking, gb_7d, users_7d = (line.split("|") + [""] * 6)[:6]
        rows.append({
            "name": name,
            "connected": connected == "t",
            "traffic_used_bytes": int(used or 0),
            "tracking": tracking == "t",
            "gb_7d": float(gb_7d or 0),
            "users_7d": int(users_7d or 0),
        })
    return rows


def traffic_snapshot() -> dict:
    sql = """
select n.name,
       n.is_connected,
       coalesce(n.traffic_used_bytes, 0),
       n.is_traffic_tracking_active,
       coalesce(round(sum(nuh.total_bytes)/1024.0/1024/1024,3),0) as gb_7d,
       count(distinct nuh.user_id) as users_7d
from nodes n
left join nodes_user_usage_history nuh
  on nuh.node_id=n.id and nuh.created_at >= current_date - interval '7 days'
group by n.id,n.name,n.is_connected,n.traffic_used_bytes,n.is_traffic_tracking_active,n.view_position
order by n.view_position nulls last,n.name;
"""
    nodes = run_remnawave_sql(sql)
    by_name = {n["name"]: n for n in nodes}
    aliases = {
        "ru": "ru-smart-goida",
        "fin": "fin-goida",
        "fra": "fra-goida",
        "swe": "swe-goida",
        "home": "home-goida",
    }
    for route in ROUTES:
        target = aliases.get(route["to"]) or aliases.get(route["from"])
        stats = by_name.get(target, {})
        route["gb_7d"] = stats.get("gb_7d")
        route["users_7d"] = stats.get("users_7d")
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "routes": ROUTES,
        "note": "route bytes use Remnawave node usage where available; inferred edges are topology hints",
    }


def get_node_metrics(node: dict) -> dict:
    host = node["host"]
    result: dict = {"online": False, "cpu": None, "ram": None, "disk": None,
                    "containers": {"vpn": 0, "bot": 0, "other": 0, "total": 0}}

    try:
        labels, row = _row(host, "system.cpu")
        skip = {"guest_nice", "guest", "steal"}
        cpu_pct = sum(v for k, v in _vals(labels, row).items()
                      if k not in skip and isinstance(v, (int, float)))
        result["cpu"] = round(cpu_pct, 1)
        result["online"] = True
    except Exception:
        return result

    try:
        # system.ram is in MiB — exclude the time column from the total
        labels, row = _row(host, "system.ram")
        v = _vals(labels, row)
        total_mib = sum(x for x in v.values() if isinstance(x, (int, float)))
        used_mib = v.get("used", 0) or 0
        result["ram"] = {
            "used_gb": round(used_mib / 1024, 1),
            "total_gb": round(total_mib / 1024, 1),
            "pct": round(used_mib / total_mib * 100, 1) if total_mib else 0,
        }
    except Exception:
        pass

    try:
        # disk_space./ is already in GiB
        labels, row = _row(host, "disk_space./")
        v = _vals(labels, row)
        avail = v.get("avail", 0) or 0
        used = v.get("used", 0) or 0
        reserved = v.get("reserved for root", 0) or 0
        total = used + avail + reserved
        denom = used + avail
        result["disk"] = {
            "used_gb": round(used, 1),
            "total_gb": round(total, 1),
            "avail_gb": round(avail, 1),
            "pct": round(used / denom * 100, 1) if denom else 0,
        }
    except Exception:
        pass

    try:
        result["containers"] = container_counts(host)
    except Exception:
        pass

    return result


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "goida-infra/0.3"

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/me":
            return self.handle_me()
        if path == "/api/nodes":
            return self.handle_nodes()
        if path == "/api/traffic/routes":
            return self.handle_routes()
        if path == "/api/hydra/url":
            return self.handle_hydra_url_get()
        if path.startswith("/api/node/"):
            rest = path.removeprefix("/api/node/").strip("/")
            parts = rest.split("/")
            node_id = parts[0]
            if len(parts) >= 2 and parts[1] == "containers":
                return self.handle_containers(node_id)
            if len(parts) >= 2 and parts[1] == "detail":
                return self.handle_node_detail(node_id)
            return self.handle_node_metrics(node_id)
        if path == "/" or not path.startswith("/api/"):
            return self.serve_static()
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/hydra/url":
            return self.handle_hydra_url_set()
        if path.startswith("/api/node/"):
            rest = path.removeprefix("/api/node/").strip("/")
            parts = rest.split("/")
            if len(parts) >= 2 and parts[1] == "shell":
                return self.handle_shell(parts[0])
        self.send_json(404, {"error": "not found"})

    def _gate_user(self) -> dict | None:
        try:
            return owner_user(self.headers)
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except Exception as exc:
            self.send_json(401, {"error": str(exc)})
        return None

    def _gate(self) -> bool:
        return self._gate_user() is not None

    def handle_me(self) -> None:
        try:
            user = owner_user(self.headers)
            self.send_json(200, {"id": user["id"], "first_name": user.get("first_name", "")})
        except PermissionError as exc:
            self.send_json(403, {"error": str(exc)})
        except Exception as exc:
            self.send_json(401, {"error": str(exc)})

    def handle_nodes(self) -> None:
        if not self._gate():
            return
        online = get_online_hosts()
        result = [{
            "id": n["id"], "name": n["name"], "role": n["role"],
            "online": n["host"] in online,
        } for n in NODES]
        self.send_json(200, {"nodes": result})

    def handle_node_metrics(self, node_id: str) -> None:
        if not self._gate():
            return
        node = _NODE_BY_ID.get(node_id)
        if not node:
            self.send_json(404, {"error": "node not found"})
            return
        metrics = get_node_metrics(node)
        self.send_json(200, {"id": node_id, "name": node["name"], **metrics})

    def handle_containers(self, node_id: str) -> None:
        if not self._gate():
            return
        node = _NODE_BY_ID.get(node_id)
        if not node:
            self.send_json(404, {"error": "node not found"})
            return
        host = node["host"]
        names = list_container_names(host)
        if not names:
            self.send_json(200, {"id": node_id, "containers": []})
            return
        with ThreadPoolExecutor(max_workers=8) as ex:
            items = list(ex.map(lambda nm: container_metrics(host, nm), names))
        # group order vpn, bot, other; then by name
        order = {"vpn": 0, "bot": 1, "tunnel": 2, "observability": 3, "other": 4}
        items.sort(key=lambda c: (order.get(c["group"], 9), c["name"]))
        self.send_json(200, {"id": node_id, "containers": items})

    def handle_node_detail(self, node_id: str) -> None:
        if not self._gate():
            return
        node = _NODE_BY_ID.get(node_id)
        if not node:
            self.send_json(404, {"error": "node not found"})
            return
        metrics = get_node_metrics(node)
        names = list_container_names(node["host"])
        with ThreadPoolExecutor(max_workers=8) as ex:
            containers = list(ex.map(lambda nm: container_metrics(node["host"], nm), names))
        containers.sort(key=lambda c: (c["group"], c["name"]))
        self.send_json(200, {
            "id": node_id,
            "name": node["name"],
            "role": node["role"],
            "metrics": metrics,
            "containers": containers,
            "probe": read_node_probe(node),
        })

    def handle_routes(self) -> None:
        if not self._gate():
            return
        try:
            self.send_json(200, traffic_snapshot())
        except Exception as exc:
            self.send_json(200, {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "nodes": [],
                "routes": ROUTES,
                "error": str(exc),
            })

    def handle_shell(self, node_id: str) -> None:
        user = self._gate_user()
        if not user:
            return
        if not SHELL_ENABLED:
            self.send_json(403, {"error": "shell disabled"})
            return
        node = _NODE_BY_ID.get(node_id)
        if not node:
            self.send_json(404, {"error": "node not found"})
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 65536)
            body = json.loads(self.rfile.read(length) or b"{}")
            command = str(body.get("command", "")).strip()
            timeout = min(max(int(body.get("timeout", 30)), 1), SHELL_TIMEOUT_MAX)
        except Exception as exc:
            self.send_json(400, {"error": f"bad request: {exc}"})
            return
        if not command:
            self.send_json(400, {"error": "empty command"})
            return
        lock = _SHELL_LOCKS[node_id]
        if not lock.acquire(blocking=False):
            self.send_json(409, {"error": "command already running"})
            return
        try:
            try:
                result = shell_command(node, command, timeout)
            except subprocess.TimeoutExpired:
                result = {"exit_code": 124, "duration_ms": timeout * 1000,
                          "output": f"timeout after {timeout}s", "truncated": False}
            except Exception as exc:
                result = {"exit_code": 255, "duration_ms": 0, "output": str(exc), "truncated": False}
            audit_shell(user, node_id, command, result)
            self.send_json(200, result)
        finally:
            lock.release()

    def handle_hydra_url_get(self) -> None:
        if not self._gate():
            return
        self.send_json(200, {"sub_url": load_hydra_url()})

    def handle_hydra_url_set(self) -> None:
        user = self._gate_user()
        if not user:
            return
        if not SHELL_ENABLED:
            self.send_json(403, {"error": "shell disabled"})
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 65536)
            body = json.loads(self.rfile.read(length) or b"{}")
            url = str(body.get("sub_url", "")).strip()
        except Exception as exc:
            self.send_json(400, {"error": f"bad request: {exc}"})
            return
        if not url:
            self.send_json(400, {"error": "empty sub_url"})
            return
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            self.send_json(400, {"error": "sub_url must be an http(s) URL"})
            return
        locks = [_SHELL_LOCKS[n] for n in HYDRA_TARGET_NODES]
        acquired: list[threading.Lock] = []
        try:
            for lock in locks:
                if not lock.acquire(blocking=False):
                    self.send_json(409, {"error": "a node command is already running"})
                    return
                acquired.append(lock)
            results: dict[str, dict] = {}
            for node_id in HYDRA_TARGET_NODES:
                try:
                    results[node_id] = push_hydra_url(node_id, url, user)
                except subprocess.TimeoutExpired:
                    results[node_id] = {"exit_code": 124, "duration_ms": 0,
                                         "output": "timeout", "truncated": False}
                except Exception as exc:
                    results[node_id] = {"exit_code": 255, "duration_ms": 0,
                                         "output": str(exc), "truncated": False}
            save_hydra_url(url, user)
            ok = all(r.get("exit_code") == 0 for r in results.values())
            self.send_json(200 if ok else 207, {"sub_url": url, "results": results})
        finally:
            for lock in acquired:
                lock.release()

    def serve_static(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        file_path = (WEB_ROOT / rel).resolve()
        if WEB_ROOT.resolve() not in file_path.parents and file_path != WEB_ROOT.resolve():
            self.send_error(403)
            return
        if not file_path.exists() or file_path.is_dir():
            file_path = WEB_ROOT / "index.html"
        if not file_path.exists():
            self.send_error(404)
            return
        body = file_path.read_bytes()
        ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store" if file_path.name == "index.html" else "public, max-age=300")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    if not TOKEN:
        print("INFRA_BOT_TOKEN is not set", file=sys.stderr)
        sys.exit(1)
    server = ThreadingHTTPServer(("127.0.0.1", WEB_PORT), ApiHandler)
    print(f"infra-backend listening on 127.0.0.1:{WEB_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
