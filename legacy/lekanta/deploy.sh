#!/usr/bin/env bash
# Деплой фаз 4-7 для lekanta.ru
# Запускать: bash deploy.sh <BOT_TOKEN>
set -e
HOST="root@194.55.236.151"
BOT_TOKEN="${1:?Укажи BOT_TOKEN как первый аргумент}"

echo "=== 1. Копируем файлы ==="
scp "$(dirname "$0")/bot.py"           "$HOST:/root/vpn-bot/vpn-bot.py"
scp "$(dirname "$0")/sub-updater.py"   "$HOST:/opt/sub-updater/updater.py"
scp "$(dirname "$0")/xray-template.json" "$HOST:/tmp/xray-template.json"

echo "=== 2. Создаём директории и env-файл ==="
ssh "$HOST" bash -s << EOF
set -e
mkdir -p /root/vpn-bot/subscriptions /opt/sub-updater
cat > /root/vpn-bot/.env << 'ENVEOF'
BOT_TOKEN=${BOT_TOKEN}
ENVEOF
chmod 600 /root/vpn-bot/.env
touch /opt/sub-updater/whitelist_manual.txt
EOF

echo "=== 3. Применяем xray template в 3x-ui DB (фаза 4) ==="
ssh "$HOST" python3 << 'PYEOF'
import json, sqlite3
DB = "/etc/x-ui/x-ui.db"
with open("/tmp/xray-template.json") as f:
    tmpl = json.load(f)
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("SELECT key FROM settings WHERE key='xrayTemplateConfig'")
existing = cur.fetchone()
tmpl_str = json.dumps(tmpl, indent=2, ensure_ascii=False)
if existing:
    cur.execute("UPDATE settings SET value=? WHERE key='xrayTemplateConfig'", (tmpl_str,))
else:
    cur.execute("INSERT INTO settings (key, value) VALUES ('xrayTemplateConfig', ?)", (tmpl_str,))
conn.commit()
conn.close()
print("xrayTemplateConfig применён")
PYEOF

echo "=== 4. Устанавливаем PySocks для бота ==="
ssh "$HOST" pip3 install PySocks --break-system-packages -q

echo "=== 5. Создаём systemd-юниты ==="
ssh "$HOST" bash << 'EOF'
cat > /etc/systemd/system/vpn-bot.service << 'SVC'
[Unit]
Description=VPN Telegram Bot (lekanta)
After=network.target x-ui.service

[Service]
Type=simple
WorkingDirectory=/root/vpn-bot
ExecStart=/usr/bin/python3 /root/vpn-bot/vpn-bot.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC

cat > /etc/systemd/system/sub-updater-lekanta.service << 'SVC'
[Unit]
Description=Sub-Updater Hydra (lekanta)
After=network.target x-ui.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/sub-updater/updater.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVC
EOF

echo "=== 6. Запускаем sub-updater первый раз (populate hydra outbounds) ==="
ssh "$HOST" bash -c "cd /opt/sub-updater && python3 updater.py; echo 'sub-updater первый запуск завершён'"

echo "=== 7. Включаем и стартуем сервисы ==="
ssh "$HOST" bash << 'EOF'
systemctl daemon-reload
systemctl enable --now sub-updater-lekanta
systemctl enable --now vpn-bot
# socks-bot инбаунд станет активен после первого перезапуска x-ui от sub-updater
EOF

echo ""
echo "=== Статусы ==="
ssh "$HOST" bash << 'EOF'
echo "--- x-ui ---"
systemctl status x-ui --no-pager -l | tail -5
echo "--- sub-updater ---"
systemctl status sub-updater-lekanta --no-pager -l | tail -5
echo "--- vpn-bot ---"
systemctl status vpn-bot --no-pager -l | tail -5
echo "--- sub-updater лог ---"
tail -20 /var/log/sub-updater-lekanta.log 2>/dev/null || echo "(лог пуст)"
EOF

echo ""
echo "=== Готово ==="
echo "Панель:      https://lekanta.ru:25565/vagina/"
echo "Sub server:  https://lekanta.ru/subscribe/<token>"
echo "Добавить юзера: /adduser <имя> в боте"
