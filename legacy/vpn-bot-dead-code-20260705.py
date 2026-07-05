# Мёртвые функции, вырезанные из bot/vpn-bot.py 2026-07-05 (repo-cleanup-plan Phase 2).
# Ни одна не вызывалась нигде в репо (3X-UI эпоха, убранные фичи). Хранится для истории.


# --- native_sub_set (vpn-bot.py:1333-1334) ---
def native_sub_set(username: str, enable: bool):
    set_bot_setting(NATIVE_SUB_KEY_PREFIX + username, "1" if enable else "0")

# --- adblock_dns_set (vpn-bot.py:1341-1342) ---
def adblock_dns_set(username: str, enable: bool):
    set_bot_setting(ADBLOCK_DNS_KEY_PREFIX + username, "1" if enable else "0")

# --- xui_add_client (vpn-bot.py:1406-1445) ---
def xui_add_client(inbound_id: int, email: str, client_uuid: str):
    """добавляет клиента в inbound 3X-UI"""
    conn = sqlite3.connect(XUI_DB, timeout=30)
    row = conn.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"inbound {inbound_id} не найден")

    settings = json.loads(row[0])
    clients = settings.get("clients", [])

    # проверяем нет ли уже такого email
    if any(c.get("email") == email for c in clients):
        conn.close()
        return  # уже есть

    clients.append({
        "id": client_uuid,
        "flow": "",
        "email": email,
        "limitIp": 4,
        "totalGB": 0,
        "expiryTime": 0,
        "enable": True,
        "tgId": "",
        "subId": "",
        "comment": "",
        "reset": 0
    })

    settings["clients"] = clients
    conn.execute("UPDATE inbounds SET settings=? WHERE id=?",
                 (json.dumps(settings), inbound_id))
    # создаём запись трафика — без неё 3x-ui не показывает статистику
    conn.execute(
        "INSERT OR IGNORE INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset) VALUES (?, 1, ?, 0, 0, 0, 0, 0)",
        (inbound_id, email)
    )
    conn.commit()
    conn.close()

# --- hysteria_get_status (vpn-bot.py:1838-1845) ---
def hysteria_get_status(username: str) -> bool:
    """проверяет включена ли hysteria для пользователя (поле в bot.db)"""
    conn = sqlite3.connect(BOT_DB, timeout=30)
    row = conn.execute("SELECT hysteria FROM users WHERE name=?", (username,)).fetchone()
    conn.close()
    if row and row[0]:
        return bool(row[0])
    return False

# --- hysteria_set (vpn-bot.py:1848-1852) ---
def hysteria_set(username: str, enable: bool):
    conn = sqlite3.connect(BOT_DB, timeout=30)
    conn.execute("UPDATE users SET hysteria=? WHERE name=?", (1 if enable else 0, username))
    conn.commit()
    conn.close()

# --- get_subscription_content (vpn-bot.py:2093-2107) ---
def get_subscription_content(username: str) -> str | None:
    """читает текущее содержимое подписки"""
    user = get_user(username)
    if not user:
        return None

    sub_path = os.path.join(SUBS_DIR, user["token"])
    if os.path.exists(sub_path):
        with open(sub_path) as f:
            return f.read()

    # если файла нет — генерируем
    content = generate_subscription(username)
    save_subscription(username, content)
    return content

# --- check_ip_limit (vpn-bot.py:2248-2321) ---
def check_ip_limit(token: str, client_ip: str, content: str, user_agent: str = "") -> str:

    """
    отслеживает уникальные устройства по IP+UA.
    серверные IP игнорируются.
    если уникальных IP больше лимита — возвращает заглушку.
    """
    # игнорируем обращения с серверов впн
    if DEVICE_LIMITS_TEMP_DISABLED:
        return content
    if client_ip in SERVER_IPS:
        return content

    now = datetime.now(timezone.utc).isoformat()
    ua = user_agent[:200] if user_agent else ""
    device_id = get_device_id(client_ip, user_agent)
    if not device_id:
        return unsupported_client_content()

    try:
        conn = sqlite3.connect(BOT_DB, timeout=30)

        existing = conn.execute(
            "SELECT ip FROM user_ips WHERE token=? AND ip=?", (token, device_id)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE user_ips SET last_seen=?, user_agent=? WHERE token=? AND ip=?",
                (now, ua, token, device_id)
            )
        else:
            conn.execute(
                "INSERT INTO user_ips (token, ip, first_seen, last_seen, user_agent) VALUES (?, ?, ?, ?, ?)",
                (token, device_id, now, now, ua)
            )

        conn.commit()
        upsert_device_record(token, device_id, client_ip, user_agent)

        # лимит смотрим из 3X-UI для конкретного пользователя
        user_limit = IP_LIMIT
        try:
            xui_conn = sqlite3.connect(XUI_DB, timeout=30)
            xui_row = xui_conn.execute(
                "SELECT settings FROM inbounds WHERE id=?", (INBOUNDS["smart"]["id"],)
            ).fetchone()
            xui_conn.close()
            if xui_row:
                token_to_email = sqlite3.connect(BOT_DB, timeout=30).execute(
                    "SELECT name FROM users WHERE token=?", (token,)
                ).fetchone()
                if token_to_email:
                    uname = token_to_email[0]
                    for c in json.loads(xui_row[0]).get("clients", []):
                        if c.get("email") == uname:
                            user_limit = c.get("limitIp", IP_LIMIT)
                            break
        except Exception:
            pass

        count = conn.execute(
            "SELECT COUNT(*) FROM user_ips WHERE token=?", (token,)
        ).fetchone()[0]

        conn.close()

        if user_limit != 0 and count > user_limit:
            return limit_exceeded_content(user_limit)

    except Exception:
        pass

    return content

# --- wl_registry_add (vpn-bot.py:4820-4833) ---
def wl_registry_add(vless_url: str):
    """добавляет vless строку в централизованный реестр WL (UUID заменяется на {uuid})"""
    vless_url = _mask_wl_entry_uuid(vless_url)
    os.makedirs(os.path.dirname(WL_REGISTRY_FILE), exist_ok=True)
    existing = []
    try:
        with open(WL_REGISTRY_FILE) as f:
            existing = [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        pass
    if vless_url not in existing:
        existing.append(vless_url)
        with open(WL_REGISTRY_FILE, "w") as f:
            f.write("\n".join(existing) + "\n")
