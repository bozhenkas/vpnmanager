"""Транспорт к Remnawave: docker exec psql + HTTP API. Единственное место в проекте,
которое имеет право лезть в контейнер remnawave-db или дёргать его HTTP API напрямую.
"""

import base64
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

REMNAWAVE_DB_CONTAINER = os.environ.get("REMNAWAVE_DB_CONTAINER", "remnawave-db")
REMNAWAVE_API_URL = os.environ.get("REMNAWAVE_API_URL", "https://127.0.0.1:30080")
REMNAWAVE_API_TOKEN_NAME = os.environ.get("REMNAWAVE_API_TOKEN_NAME", "codex-migration")
# Значение — только через env (systemd EnvironmentFile/Environment=), не хардкодить.
# На RU лежит в vpn-bot.service.d/remnawave-basic-auth.conf.
REMNAWAVE_PANEL_BASIC_AUTH = os.environ.get("REMNAWAVE_PANEL_BASIC_AUTH", "")


def pg_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def remnawave_query(sql: str) -> str:
    try:
        proc = subprocess.run(
            [
                "docker", "exec", REMNAWAVE_DB_CONTAINER,
                "psql", "-U", "postgres", "-d", "postgres", "-At", "-c", sql,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def remnawave_api_token() -> str:
    env_token = os.environ.get("REMNAWAVE_API_TOKEN", "").strip()
    if env_token:
        return env_token
    raw = remnawave_query(
        "select token from api_tokens "
        f"where token_name={pg_quote(REMNAWAVE_API_TOKEN_NAME)} "
        "order by created_at desc limit 1;"
    )
    return raw.strip()


def remnawave_api_request(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    token = remnawave_api_token()
    if not token:
        raise RuntimeError("Remnawave API token not found")
    url = REMNAWAVE_API_URL.rstrip("/") + path
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if REMNAWAVE_PANEL_BASIC_AUTH:
        basic = base64.b64encode(REMNAWAVE_PANEL_BASIC_AUTH.encode()).decode()
        headers["X-Goida-Basic-Auth"] = f"Basic {basic}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    # панель сейчас доступна через локальный nginx с self-signed/host cert на 30080
    context = ssl._create_unverified_context() if url.startswith("https://127.") else None
    try:
        with urllib.request.urlopen(request, timeout=20, context=context) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"Remnawave API {method} {path} failed: HTTP {exc.code}: {body[:300]}") from exc
    return json.loads(body or "{}")


def remnawave_restart_all_nodes() -> bool:
    try:
        data = remnawave_api_request("/api/nodes/actions/restart-all", "POST", {"forceRestart": True})
        return bool(data.get("response", {}).get("eventSent"))
    except Exception as exc:
        print(f"[remnawave] restart-all failed: {exc}", file=sys.stderr)
        return False
