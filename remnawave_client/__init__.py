"""Единственная точка доступа к Remnawave (docker exec psql + HTTP API).

Остальной код проекта (bot/, client-bot/, intermesh/, infra-backend/) не должен
сам делать `docker exec ... psql` или ходить в Remnawave HTTP API напрямую —
только через этот пакет. Бизнес-логика (сборка ссылок подписки, HTTP-хендлеры)
сюда не входит — она использует эти функции, но живёт в вызывающем коде.
"""

from .transport import (
    REMNAWAVE_API_TOKEN_NAME,
    REMNAWAVE_API_URL,
    REMNAWAVE_DB_CONTAINER,
    REMNAWAVE_PANEL_BASIC_AUTH,
    pg_quote,
    remnawave_api_request,
    remnawave_api_token,
    remnawave_query,
    remnawave_restart_all_nodes,
)
from .users import (
    REMNA_BASE_SQUADS,
    REMNAWAVE_DEFAULT_DEVICE_LIMIT,
    REMNAWAVE_NEW_USER_EXPIRE,
    remnawave_create_user,
    remnawave_delete_device,
    remnawave_delete_user,
    remnawave_devices_by_username,
    remnawave_get_legacy_sub_token,
    remnawave_set_device_limit,
    remnawave_user,
    remnawave_user_by_legacy_token,
    remnawave_user_by_short_uuid,
    remnawave_usernames,
    remnawave_vless_uuid,
)
from .squads import (
    remnawave_active_hydra_squads,
    remnawave_user_hydra_squads,
    remnawave_user_squads,
)

__all__ = [
    "REMNAWAVE_API_TOKEN_NAME",
    "REMNAWAVE_API_URL",
    "REMNAWAVE_DB_CONTAINER",
    "REMNAWAVE_PANEL_BASIC_AUTH",
    "REMNA_BASE_SQUADS",
    "REMNAWAVE_DEFAULT_DEVICE_LIMIT",
    "REMNAWAVE_NEW_USER_EXPIRE",
    "pg_quote",
    "remnawave_active_hydra_squads",
    "remnawave_api_request",
    "remnawave_api_token",
    "remnawave_create_user",
    "remnawave_delete_device",
    "remnawave_delete_user",
    "remnawave_devices_by_username",
    "remnawave_get_legacy_sub_token",
    "remnawave_query",
    "remnawave_restart_all_nodes",
    "remnawave_set_device_limit",
    "remnawave_user",
    "remnawave_user_by_legacy_token",
    "remnawave_user_by_short_uuid",
    "remnawave_user_hydra_squads",
    "remnawave_user_squads",
    "remnawave_usernames",
    "remnawave_vless_uuid",
]
