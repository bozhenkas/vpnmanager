# wing: bot

## architecture
Single-file bot: `/root/vpn-bot/vpn-bot.py` (~1700 lines)
Sync polling, no asyncio. HTTP sub-server in Thread (port 9090).
No pip dependencies — stdlib + sqlite3 only.

## state machines
```
edit_state[chat_id]   → {"user": name, "message_id": int}
edit_buffer[chat_id]  → {"user": name, "parts": [...], "timer": Thread}  # 3s buffer
rename_state[chat_id] → {"user": name, "message_id": int}
```

## subscription flow
generate_subscription(username) → plain text (vless:// lines + #profile-title)
save_subscription(username, content) → writes plain text to subscriptions/<token>
SubHandler.do_GET → reads file, base64.b64encode(content.encode()) → sends to client
⚠️ файл хранится как plain text, handler сам кодирует — не писать base64 в файл

## user flags (bot.db)
wl=1 → whitelist серверы добавляются в подписку (из WL_FILE)
hydra → определяется по enable в nl-inbound 3X-UI
hysteria → колонка в bot.db

## whitelist logic
WL_FILE = /opt/sub-updater/whitelist_links.txt  (auto, перезаписывается updater)
whitelist_manual.txt  → ручные серверы, мержатся при каждом run_once()
generate_wl_links() → читает WL_FILE построчно
⚠️ updater перезапускает x-ui после изменений — может дропнуть соединения

## key constants
DOMAIN = ru.goida.fun
XUI_DB = /etc/x-ui/x-ui.db
BOT_DB = /root/vpn-bot/bot.db
SUBS_DIR = /root/vpn-bot/subscriptions
SUB_PORT = 9090
PANEL_URL = https://127.0.0.1:25565/penis
CLIENT_UUID = e16a2d64-a690-41da-848e-d9cf5ff1a100  # hydra inbounds

## hydra inbounds
HYDRA_INBOUNDS = {
  "usa": {"id":7, "port":10011, "prefix":"usa-"},
  "pol": {"id":8, "port":10012, "prefix":"pol-"},
  "tur": {"id":9, "port":10013, "prefix":"tur-"},
  "nl":  {"id":13,"port":10014, "prefix":"nl-"},
  "de":  {"id":14,"port":10015, "prefix":"de-"},
  "fi-ws":{"id":15,"port":10016,"prefix":"fi-ws-"},
}

## patches pattern
Всегда через python3 heredoc с string replacement.
Явный OK/FAIL вывод. Бэкап перед правкой.
После патча: systemctl restart vpn-bot && sleep 2 && status | tail -3

## changelog
2026-04-23: edit_buffer — 3s буферизация для многочастных сообщений
2026-04-23: whitelist_manual.txt — ручные WL серверы, updater мержит
2026-04-23: WL серверы в подписке: 7 (WL1-1, WL1-2, WL2-1, WL2-2, WL3-1, WL3-2, РЕЗЕРВ)
