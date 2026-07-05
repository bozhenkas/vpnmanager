"""Доступ к Remnawave-пользователям: чтение, создание, удаление, устройства."""

import json
import os
import secrets
import uuid

from .transport import pg_quote, remnawave_query, remnawave_restart_all_nodes

REMNAWAVE_NEW_USER_EXPIRE = os.environ.get("REMNAWAVE_NEW_USER_EXPIRE", "2099-12-31 23:59:59")
REMNAWAVE_DEFAULT_DEVICE_LIMIT = int(os.environ.get("REMNAWAVE_DEFAULT_DEVICE_LIMIT", "2"))

# squad'ы, в которые новый юзер попадает по умолчанию при remnawave_create_user
REMNA_BASE_SQUADS = ("SMART_RU_REMNA", "SMART_REMNA", "FRA")


def remnawave_usernames() -> set[str]:
    """один запрос вместо N: набор всех username, мигрированных на Remnawave."""
    raw = remnawave_query("select username from users;")
    return {line for line in raw.splitlines() if line}


def remnawave_user(username: str) -> dict | None:
    safe = username.replace("'", "''")
    raw = remnawave_query(
        "select json_build_object("
        "'uuid', u.uuid, 'shortUuid', u.short_uuid, 'username', u.username, "
        "'deviceLimit', coalesce(u.hwid_device_limit, 0), "
        "'expireAt', u.expire_at, 'status', u.status, "
        "'usedTrafficBytes', coalesce(ut.used_traffic_bytes, 0), "
        "'lifetimeUsedTrafficBytes', coalesce(ut.lifetime_used_traffic_bytes, 0), "
        "'trafficLimitBytes', 0"
        ")::text from users u "
        "left join user_traffic ut on ut.t_id=u.t_id "
        f"where u.username='{safe}' limit 1;"
    )
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def remnawave_user_by_legacy_token(token: str) -> dict | None:
    raw = remnawave_query(
        "select json_build_object("
        "'uuid', uuid, 'shortUuid', short_uuid, 'username', username, "
        "'deviceLimit', coalesce(hwid_device_limit, 0), 'status', status"
        ")::text from users "
        f"where tag={pg_quote('legacy-sub-token:' + token)} limit 1;"
    )
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def remnawave_user_by_short_uuid(short_uuid: str) -> dict | None:
    """ищет RW-пользователя по short_uuid (для случаев когда токен = shortUuid)."""
    raw = remnawave_query(
        "select json_build_object("
        "'uuid', uuid, 'shortUuid', short_uuid, 'username', username, "
        "'deviceLimit', coalesce(hwid_device_limit, 0), 'status', status"
        ")::text from users "
        f"where short_uuid={pg_quote(short_uuid)} limit 1;"
    )
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def remnawave_devices_by_username(username: str) -> list[dict]:
    safe = username.replace("'", "''")
    raw = remnawave_query(
        "select coalesce(json_agg(json_build_object("
        "'hwid', d.hwid, 'platform', d.platform, 'osVersion', d.os_version, "
        "'deviceModel', d.device_model, 'userAgent', d.user_agent, "
        "'createdAt', d.created_at, 'updatedAt', d.updated_at"
        ") order by d.updated_at desc), '[]'::json)::text "
        "from hwid_user_devices d join users u on u.uuid=d.user_uuid "
        f"where u.username='{safe}';"
    )
    try:
        return json.loads(raw or "[]")
    except Exception:
        return []


def remnawave_set_device_limit(username: str, limit: int) -> bool:
    safe = username.replace("'", "''")
    raw = remnawave_query(
        f"update users set hwid_device_limit={int(limit)}, updated_at=now() "
        f"where username='{safe}' returning 1;"
    )
    return bool(raw)


def remnawave_vless_uuid(username: str) -> str:
    return remnawave_query(f"select vless_uuid from users where username={pg_quote(username)} limit 1;").strip()


def remnawave_get_legacy_sub_token(username: str) -> str:
    """извлекает legacy-sub-token из поля tag Remnawave-пользователя."""
    raw = remnawave_query(
        f"select tag from users where username={pg_quote(username)} limit 1;"
    ).strip()
    prefix = "legacy-sub-token:"
    if raw.startswith(prefix):
        return raw[len(prefix):]
    return ""


def remnawave_create_user(
    username: str,
    device_limit: int = REMNAWAVE_DEFAULT_DEVICE_LIMIT,
    expire_at: str = REMNAWAVE_NEW_USER_EXPIRE,
) -> dict:
    existing = remnawave_user(username)
    if existing:
        raw_uuid = remnawave_query(f"select vless_uuid from users where username={pg_quote(username)} limit 1;")
        existing["vlessUuid"] = raw_uuid.strip()
        remnawave_restart_all_nodes()
        return existing
    user_uuid = str(uuid.uuid4())
    vless_uuid = str(uuid.uuid4())
    short_uuid = uuid.uuid4().hex[:16]
    trojan_password = secrets.token_urlsafe(24)
    ss_password = secrets.token_urlsafe(24)
    remnawave_query(
        "insert into users "
        "(uuid, short_uuid, username, status, traffic_limit_bytes, traffic_limit_strategy, expire_at, "
        "trojan_password, vless_uuid, ss_password, hwid_device_limit, created_at, updated_at) values ("
        f"{pg_quote(user_uuid)}, {pg_quote(short_uuid)}, {pg_quote(username)}, 'ACTIVE', 0, 'NO_RESET', "
        f"{pg_quote(expire_at)}, {pg_quote(trojan_password)}, {pg_quote(vless_uuid)}, "
        f"{pg_quote(ss_password)}, {int(device_limit)}, now(), now());"
    )
    remnawave_query(
        "insert into user_traffic (t_id, used_traffic_bytes, lifetime_used_traffic_bytes) "
        f"select t_id, 0, 0 from users where username={pg_quote(username)} "
        "on conflict do nothing;"
    )
    squad_names = list(REMNA_BASE_SQUADS)
    if squad_names:
        names_sql = ",".join(pg_quote(name) for name in squad_names)
        remnawave_query(
            "insert into internal_squad_members (internal_squad_uuid, user_id) "
            "select s.uuid, u.t_id from internal_squads s cross join users u "
            f"where u.username={pg_quote(username)} and s.name in ({names_sql}) "
            "on conflict do nothing;"
        )
    remnawave_restart_all_nodes()
    return {"uuid": user_uuid, "username": username, "deviceLimit": device_limit, "vlessUuid": vless_uuid}


def remnawave_delete_user(username: str) -> bool:
    raw = remnawave_query(f"delete from users where username={pg_quote(username)} returning 1;")
    deleted = bool(raw.strip())
    if deleted:
        remnawave_restart_all_nodes()
    return deleted
