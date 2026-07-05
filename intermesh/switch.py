#!/usr/bin/env python3
"""
goida-intermesh-switch — серверная ручка переключения RU→exit транспорта.

назначение: дать будущему анализатору одну локальную POST-точку, которая
переключает линк fin/swe между транспортами или выводит его в disabled/repair.
решение «когда переключать» живёт не здесь; это только actuator.

целевая модель:
  remnanode REMNA_FI/SWE/FRA -> 127.0.0.1:18001/18002/18003 -> xray-intermesh
  xray-intermesh routing: in-fin -> out-fin-xhttp-reality | out-fin-hy2-fallback | block
                       in-fra -> out-fra-epilepsy     | out-fra-hy2-fallback | block

эндпоинты (bind 127.0.0.1, токен в X-Goida-Switch-Token):
  GET  /status
  POST /switch {"link":"fin","stage":"xhttp-reality|hy2-fallback"}
  POST /switch {"link":"fra","stage":"epilepsy|hy2-fallback"}
  POST /switch {"link":"fin","mode":"disabled|repair", "stage":"hy2-fallback"}
  POST /probe  {"link":"fin"}

legacy socat-backend оставлен только для локального rollback ранней версии.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# --- конфиг из env (дефолты = будущие прод-пути на RU) -----------------------
BIND_HOST = os.environ.get("SWITCH_BIND", "127.0.0.1")
BIND_PORT = int(os.environ.get("SWITCH_PORT", "9099"))
CONF_DIR = Path(os.environ.get("SWITCH_CONF_DIR", "/etc/goida-intermesh"))
LINKS_FILE = Path(os.environ.get("SWITCH_LINKS", str(CONF_DIR / "links.json")))
STATE_FILE = Path(os.environ.get("SWITCH_STATE", str(CONF_DIR / "state.json")))
TOKEN_FILE = Path(os.environ.get("SWITCH_TOKEN_FILE", str(CONF_DIR / "token")))
TOKEN_ENV = os.environ.get("SWITCH_TOKEN", "")
PROBE_TIMEOUT = float(os.environ.get("SWITCH_PROBE_TIMEOUT", "5"))

APPLY_ENABLED = os.environ.get("SWITCH_APPLY", "1") == "1"
BACKEND = os.environ.get("SWITCH_BACKEND", "xray").strip().lower()
XRAY_UNIT = os.environ.get("SWITCH_XRAY_UNIT", "xray-intermesh.service")
XRAY_ROUTING_FILE = Path(
    os.environ.get("SWITCH_XRAY_ROUTING_FILE", str(CONF_DIR / "routing.generated.json"))
)
REMNA_PROFILE_NAME = os.environ.get("SWITCH_REMNA_PROFILE_NAME", "ru-ws-ingress")
REMNA_CONTAINER = os.environ.get("SWITCH_REMNA_DB_CONTAINER", "remnawave-db")
REMNA_RESTART_CMD = os.environ.get("SWITCH_REMNA_RESTART_CMD", "docker restart remnanode")

CANON_STAGES = ("xhttp-reality", "epilepsy", "hy2-fallback")
CANON_MODES = ("active", "disabled", "repair")
STAGE_ALIASES = {
    "primary": "xhttp-reality",
    "xhttp": "xhttp-reality",
    "xhttp-reality": "xhttp-reality",
    "reality": "xhttp-reality",
    "epilepsy": "epilepsy",
    "epilepsy-primary": "epilepsy",
    "pg": "epilepsy",
    "postgres": "epilepsy",
    "postgres-camouflage": "epilepsy",
    "fallback": "hy2-fallback",
    "hy2": "hy2-fallback",
    "hy2-fallback": "hy2-fallback",
    "hysteria2": "hy2-fallback",
    "hysteria2-salamander": "hy2-fallback",
}
MODE_ALIASES = {
    "active": "active",
    "up": "active",
    "disabled": "disabled",
    "disable": "disabled",
    "down": "disabled",
    "off": "disabled",
    "block": "disabled",
    "repair": "repair",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("intermesh-switch")


# --- утилиты состояния/конфига ----------------------------------------------
def _read_token() -> str:
    if TOKEN_ENV:
        return TOKEN_ENV.strip()
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def _clean_links(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(k): v for k, v in data.items()
        if isinstance(v, dict) and not str(k).startswith("_")
    }


def load_links() -> dict[str, dict[str, Any]]:
    return _clean_links(json.loads(LINKS_FILE.read_text()))


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text())
    except OSError:
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True))
    tmp.replace(STATE_FILE)


def _tcp_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def _split_hostport(target: str) -> tuple[str, int] | None:
    host, sep, port = str(target).rpartition(":")
    if not sep or not host:
        return None
    try:
        return host, int(port)
    except ValueError:
        return None


def canonical_stage(stage: str) -> str:
    got = STAGE_ALIASES.get(str(stage).strip().lower())
    if got not in CANON_STAGES:
        raise ValueError(f"stage must be one of {CANON_STAGES}")
    return got


def canonical_mode(mode: str) -> str:
    got = MODE_ALIASES.get(str(mode).strip().lower())
    if got not in CANON_MODES:
        raise ValueError(f"mode must be one of {CANON_MODES}")
    return got


def default_stage(linkcfg: dict[str, Any]) -> str:
    return canonical_stage(str(linkcfg.get("default_stage", "xhttp-reality")))


def default_desired(linkcfg: dict[str, Any]) -> dict[str, Any]:
    return {"mode": "active", "stage": default_stage(linkcfg), "updated_at": 0}


def normalize_state(raw: dict[str, Any], links: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """понимает новый state.links и старый flat-state вида {"fin":"fallback"}."""
    raw_links = raw.get("links") if isinstance(raw.get("links"), dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for link, cfg in links.items():
        old = raw_links.get(link, raw.get(link))
        desired = default_desired(cfg)
        if isinstance(old, dict):
            desired["mode"] = canonical_mode(str(old.get("mode", desired["mode"])))
            desired["stage"] = canonical_stage(str(old.get("stage", desired["stage"])))
            desired["updated_at"] = int(old.get("updated_at", old.get("ts", 0)) or 0)
        elif isinstance(old, str) and old:
            token = old.strip().lower()
            if token in MODE_ALIASES and canonical_mode(token) != "active":
                desired["mode"] = canonical_mode(token)
            else:
                desired["stage"] = canonical_stage(token)
            desired["updated_at"] = int(raw.get(f"_{link}_ts", 0) or 0)
        out[link] = desired
    return out


def state_of(link: str) -> str:
    links = load_links()
    state = normalize_state(load_state(), links)
    return state.get(link, default_desired({}))["stage"]


def desired_from_body(body: dict[str, Any], current: dict[str, Any], linkcfg: dict[str, Any]) -> dict[str, Any]:
    stage_raw = body.get("stage")
    mode_raw = body.get("mode", body.get("action"))
    desired = dict(current)

    if stage_raw is not None and str(stage_raw).strip().lower() in MODE_ALIASES:
        mode = canonical_mode(str(stage_raw))
        if mode != "active":
            desired["mode"] = mode
            # repair без stage ремонтирует дефолтный транспорт; disabled stage оставляет для статуса.
            if mode == "repair" and not body.get("repair_stage"):
                desired["stage"] = default_stage(linkcfg)
            desired["updated_at"] = int(time.time())
            return desired

    if mode_raw is not None:
        desired["mode"] = canonical_mode(str(mode_raw))

    stage_value = body.get("repair_stage", stage_raw)
    if stage_value is not None:
        desired["stage"] = canonical_stage(str(stage_value))

    if desired["mode"] == "active" and stage_value is None:
        raise ValueError("stage is required for active switch")

    desired["updated_at"] = int(time.time())
    return desired


def serialize_state(states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"links": states}


# --- xray backend ------------------------------------------------------------
def _xray_inbound_tag(link: str, cfg: dict[str, Any]) -> str:
    return str(cfg.get("inbound", f"in-{link}"))


def _xray_block_tag(cfg: dict[str, Any]) -> str:
    return str(cfg.get("block", "block"))


def _xray_outbound_tag(link: str, cfg: dict[str, Any], stage: str) -> str:
    outbounds = cfg.get("outbounds")
    if isinstance(outbounds, dict) and outbounds.get(stage):
        return str(outbounds[stage])
    return f"out-{link}-{stage}"


def active_outbound(link: str, cfg: dict[str, Any], desired: dict[str, Any]) -> str:
    if desired["mode"] == "disabled":
        return _xray_block_tag(cfg)
    return _xray_outbound_tag(link, cfg, desired["stage"])


def status_outbound(link: str, cfg: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
    if BACKEND in ("remnawave", "remna"):
        return {
            "outbound": remna_outbound_tag(link, cfg),
            "target": remna_stage_target(cfg, desired),
        }
    return {"outbound": active_outbound(link, cfg, desired)}


def render_xray_routing(
    links: dict[str, dict[str, Any]],
    states: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rules = []
    for link in sorted(links):
        cfg = links[link]
        desired = states.get(link, default_desired(cfg))
        rules.append({
            "type": "field",
            "inboundTag": [_xray_inbound_tag(link, cfg)],
            "outboundTag": active_outbound(link, cfg, desired),
        })
    return {
        "_goida": {
            "managed_by": "goida-intermesh-switch",
            "generated_at": int(time.time()),
        },
        "routing": {
            "domainStrategy": "AsIs",
            "rules": rules,
        },
    }


def apply_xray(
    links: dict[str, dict[str, Any]],
    states: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    routing = render_xray_routing(links, states)
    if APPLY_ENABLED:
        XRAY_ROUTING_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = XRAY_ROUTING_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(routing, ensure_ascii=False, indent=2, sort_keys=True))
        os.chmod(tmp, 0o640)
        tmp.replace(XRAY_ROUTING_FILE)
        r = subprocess.run(
            ["systemctl", "reload-or-restart", XRAY_UNIT],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"systemctl reload-or-restart {XRAY_UNIT} failed: {r.stderr.strip()}"
            )
    return {"backend": "xray", "unit": XRAY_UNIT, "routing_file": str(XRAY_ROUTING_FILE)}


# --- remnawave backend -------------------------------------------------------
def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_json(value: object) -> str:
    return "$json$" + json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "$json$::jsonb"


def _psql(sql: str) -> str:
    r = subprocess.run(
        [
            "docker", "exec", "-i", REMNA_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-At", "-c", sql,
        ],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def _safe_ident(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_") or "switch"


def remna_outbound_tag(link: str, cfg: dict[str, Any]) -> str:
    if cfg.get("remnawave_outbound"):
        return str(cfg["remnawave_outbound"])
    return {"fin": "REMNA_FI", "swe": "REMNA_SWE", "fra": "REMNA_FRA"}.get(link, f"REMNA_{link.upper()}")


def remna_stage_target(linkcfg: dict[str, Any], desired: dict[str, Any]) -> str:
    if desired["mode"] == "disabled":
        return str(linkcfg.get("disabled_target", "127.0.0.1:9"))
    targets = linkcfg.get("stage_targets")
    if isinstance(targets, dict) and targets.get(desired["stage"]):
        return str(targets[desired["stage"]])
    probe = linkcfg.get("probe")
    if isinstance(probe, dict) and probe.get(desired["stage"]):
        return str(probe[desired["stage"]])
    if desired["stage"] == "xhttp-reality":
        return str(linkcfg.get("xhttp-reality", linkcfg.get("primary", "")))
    if desired["stage"] == "epilepsy":
        return str(linkcfg.get("epilepsy", ""))
    return str(linkcfg.get("hy2-fallback", linkcfg.get("fallback", "")))


def rewrite_remnawave_profile(
    cfg: dict[str, Any],
    link: str,
    linkcfg: dict[str, Any],
    desired: dict[str, Any],
) -> dict[str, Any]:
    target = remna_stage_target(linkcfg, desired)
    hp = _split_hostport(target)
    if not hp:
        raise ValueError(f"bad remnawave target for {link}: {target!r}")
    host, port = hp
    tag = remna_outbound_tag(link, linkcfg)
    outbounds = cfg.get("outbounds")
    if not isinstance(outbounds, list):
        raise ValueError("remnawave profile has no outbounds list")
    outbound = next((item for item in outbounds if item.get("tag") == tag), None)
    if outbound is None:
        raise ValueError(f"outbound not found in remnawave profile: {tag}")
    vnext = outbound.setdefault("settings", {}).setdefault("vnext", [])
    if not vnext:
        raise ValueError(f"outbound {tag} has no vnext")
    vnext[0]["address"] = host
    vnext[0]["port"] = int(port)
    return {"outbound": tag, "target": f"{host}:{port}"}


def load_remnawave_profile() -> dict[str, Any]:
    raw = _psql(
        "select config::text from config_profiles "
        f"where name={_sql_str(REMNA_PROFILE_NAME)} limit 1;"
    ).strip()
    if not raw:
        raise RuntimeError(f"remnawave profile not found: {REMNA_PROFILE_NAME}")
    return json.loads(raw)


def save_remnawave_profile(cfg: dict[str, Any], link: str, desired: dict[str, Any]) -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    reason = _safe_ident(f"intermesh_{link}_{desired['mode']}_{desired['stage']}")
    backup = f"config_profiles_bak_{stamp}_{reason}"
    _psql(f"create table {backup} as table config_profiles;")
    _psql(
        "update config_profiles set "
        f"config={_sql_json(cfg)}, updated_at=now() "
        f"where name={_sql_str(REMNA_PROFILE_NAME)};"
    )
    return backup


def restart_remnanode() -> None:
    r = subprocess.run(
        REMNA_RESTART_CMD.split(),
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError(f"{REMNA_RESTART_CMD} failed: {r.stderr.strip() or r.stdout.strip()}")


def apply_remnawave(link: str, linkcfg: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
    cfg = load_remnawave_profile()
    changed = rewrite_remnawave_profile(cfg, link, linkcfg, desired)
    backup = ""
    if APPLY_ENABLED:
        backup = save_remnawave_profile(cfg, link, desired)
        restart_remnanode()
    return {
        "backend": "remnawave",
        "profile": REMNA_PROFILE_NAME,
        "backup": backup,
        **changed,
    }


# --- legacy socat backend ----------------------------------------------------
def _resolve_legacy_target(linkcfg: dict[str, Any], stage: str) -> str:
    if stage == "xhttp-reality":
        return str(linkcfg.get("xhttp-reality", linkcfg.get("primary", "")))
    return str(linkcfg.get("hy2-fallback", linkcfg.get("fallback", "")))


def _legacy_unit(link: str, linkcfg: dict[str, Any]) -> str:
    return str(linkcfg.get("unit", f"goida-stage-{link}.service"))


def apply_legacy_socat(link: str, linkcfg: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
    target = "127.0.0.1:9" if desired["mode"] == "disabled" else _resolve_legacy_target(linkcfg, desired["stage"])
    if not _split_hostport(target):
        raise ValueError(f"bad legacy target for {link}: {target!r}")

    unit = _legacy_unit(link, linkcfg)
    env_path = CONF_DIR / f"{link}.env"
    if APPLY_ENABLED:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        tmp = env_path.with_suffix(".env.tmp")
        tmp.write_text(f"TARGET={target}\n")
        os.chmod(tmp, 0o640)
        tmp.replace(env_path)
        r = subprocess.run(["systemctl", "restart", unit], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f"systemctl restart {unit} failed: {r.stderr.strip()}")

    listen = int(linkcfg["listen"])
    return {
        "backend": "socat",
        "unit": unit,
        "target": target,
        "listen": listen,
        "listen_up": _tcp_open("127.0.0.1", listen),
    }


# --- применение переключения -------------------------------------------------
def apply_desired(link: str, desired: dict[str, Any]) -> dict[str, Any]:
    links = load_links()
    if link not in links:
        raise KeyError(link)
    states = normalize_state(load_state(), links)
    states[link] = desired

    if BACKEND == "xray":
        applied = apply_xray(links, states)
    elif BACKEND in ("remnawave", "remna"):
        applied = apply_remnawave(link, links[link], desired)
    elif BACKEND in ("socat", "legacy"):
        applied = apply_legacy_socat(link, links[link], desired)
    else:
        raise ValueError("SWITCH_BACKEND must be xray, remnawave, or socat")

    save_state(serialize_state(states))
    cfg = links[link]
    return {
        "link": link,
        "mode": desired["mode"],
        "stage": desired["stage"],
        "outbound": active_outbound(link, cfg, desired) if BACKEND == "xray" else applied.get("target"),
        **applied,
    }


def apply_switch(link: str, linkcfg: dict[str, Any], stage: str) -> dict[str, Any]:
    """обратная совместимость для старых тестов: stage primary/fallback тоже работает."""
    desired = {"mode": "active", "stage": canonical_stage(stage), "updated_at": int(time.time())}
    if BACKEND == "xray":
        links = load_links()
        states = normalize_state(load_state(), links)
        states[link] = desired
        applied = apply_xray(links, states)
        cfg = links.get(link, linkcfg)
        return {
            "link": link,
            "mode": "active",
            "stage": desired["stage"],
            "outbound": active_outbound(link, cfg, desired),
            **applied,
        }
    res = apply_legacy_socat(link, linkcfg, desired)
    return {"link": link, "mode": "active", "stage": desired["stage"], **res}


def probe_link(link: str, cfg: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
    probe_map = cfg.get("probe") if isinstance(cfg.get("probe"), dict) else {}
    target = probe_map.get(desired["stage"]) or _resolve_legacy_target(cfg, desired["stage"])
    hp = _split_hostport(str(target))
    return {
        "link": link,
        "mode": desired["mode"],
        "stage": desired["stage"],
        "listen": cfg.get("listen"),
        "listen_up": _tcp_open("127.0.0.1", int(cfg["listen"])) if cfg.get("listen") else None,
        "target": target if hp else None,
        "target_up": _tcp_open(*hp) if hp else None,
    }


# --- HTTP ручка --------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "goida-intermesh-switch/2.0"

    def log_message(self, fmt, *args):  # noqa: N802 — глушим дефолтный access-лог
        return

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        want = _read_token()
        got = self.headers.get("X-Goida-Switch-Token", "")
        return bool(want) and hmac.compare_digest(want, got)

    def _body(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length", "0") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") != "/status":
            return self._send(404, {"error": "not found"})
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        try:
            links = load_links()
            states = normalize_state(load_state(), links)
        except (OSError, ValueError) as e:
            return self._send(500, {"error": f"config: {e}"})
        out = {}
        for link, cfg in links.items():
            desired = states[link]
            out[link] = {
                "mode": desired["mode"],
                "stage": desired["stage"],
                "inbound": _xray_inbound_tag(link, cfg),
                "listen": cfg.get("listen"),
                "listen_up": _tcp_open("127.0.0.1", int(cfg["listen"])) if cfg.get("listen") else None,
                "updated_at": desired.get("updated_at", 0),
                **status_outbound(link, cfg, desired),
            }
        return self._send(200, {"backend": BACKEND, "links": out, "ts": int(time.time())})

    def do_POST(self):  # noqa: N802
        path = self.path.rstrip("/")
        if not self._authed():
            return self._send(401, {"error": "unauthorized"})
        body = self._body()
        try:
            links = load_links()
        except OSError as e:
            return self._send(500, {"error": f"config: {e}"})

        link = str(body.get("link", ""))
        if link not in links:
            return self._send(400, {"error": f"unknown link '{link}'", "known": list(links)})

        states = normalize_state(load_state(), links)
        if path == "/probe":
            return self._send(200, probe_link(link, links[link], states[link]))

        if path == "/switch":
            try:
                desired = desired_from_body(body, states[link], links[link])
                res = apply_desired(link, desired)
            except Exception as e:  # noqa: BLE001 — actuator возвращает ошибку анализатору
                log.error("switch link=%s body=%s failed: %s", link, body, e)
                return self._send(500, {"error": str(e), "link": link})
            log.info(
                "switch link=%s mode=%s stage=%s outbound=%s backend=%s",
                link, res["mode"], res["stage"], res["outbound"], res["backend"],
            )
            return self._send(200, {"ok": True, **res})

        return self._send(404, {"error": "not found"})


def main() -> int:
    if not _read_token():
        log.error("нет токена (SWITCH_TOKEN / %s) — отказ старта", TOKEN_FILE)
        return 2
    srv = ThreadingHTTPServer((BIND_HOST, BIND_PORT), Handler)
    log.info(
        "intermesh-switch on %s:%s conf=%s backend=%s apply=%s",
        BIND_HOST, BIND_PORT, CONF_DIR, BACKEND, APPLY_ENABLED,
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
