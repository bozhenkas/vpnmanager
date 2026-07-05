"""Hydra squad membership (internal_squads / internal_squad_members)."""

from .transport import pg_quote, remnawave_query


def remnawave_active_hydra_squads() -> list[str]:
    raw = remnawave_query(
        "select distinct s.name "
        "from internal_squads s "
        "join internal_squad_inbounds si on si.internal_squad_uuid=s.uuid "
        "join config_profile_inbounds i on i.uuid=si.inbound_uuid "
        "where s.name like 'HYDRA\\_%\\_REMNA' "
        "and (i.tag like 'RU_WS_HYDRA\\_%' or i.tag like 'GOIDA_HYDRA\\_%') "
        "order by s.name;"
    )
    return [line.strip() for line in raw.splitlines() if line.strip()]


def remnawave_user_squads(username: str) -> set[str]:
    raw = remnawave_query(
        "select s.name "
        "from users u "
        "join internal_squad_members m on m.user_id=u.t_id "
        "join internal_squads s on s.uuid=m.internal_squad_uuid "
        f"where u.username={pg_quote(username)} order by s.name;"
    )
    return {line.strip() for line in raw.splitlines() if line.strip()}


def remnawave_user_hydra_squads(username: str) -> list[str]:
    user_squads = remnawave_user_squads(username)
    return [squad for squad in remnawave_active_hydra_squads() if squad in user_squads]
