#!/usr/bin/env bash
# B3: переезд deployer-bot с RU (83.147.255.98) на FIN (77.110.108.57)
# запускать локально, нужны SSH-доступы к обоим серверам
set -euo pipefail

RU_HOST="83.147.255.98"
RU_PORT="17904"
FIN_HOST="77.110.108.57"

# уточнить порт FIN перед запуском
FIN_PORT="${FIN_SSH_PORT:-22}"

REPO_URL="git@github.com:bozhenkas/vpndeployer.git"   # поправить если другой remote
DEPLOY_DIR="/opt/vpndeployer"
PG_DUMP_LOCAL="$(dirname "$0")/../../Downloads/backups/ru/deployer-postgres.sql"

ssh_ru() { ssh -p "$RU_PORT" "root@$RU_HOST" "$@"; }
ssh_fin() { ssh -p "$FIN_PORT" "root@$FIN_HOST" "$@"; }
scp_to_fin() { scp -P "$FIN_PORT" "$@" "root@$FIN_HOST:$DEPLOY_DIR/"; }

echo "=== B3: deployer-bot RU → FIN ==="
echo "FIN SSH port: $FIN_PORT  (экспортируй FIN_SSH_PORT=<port> если не 22)"
echo ""

echo "--- [1/5] проверяем доступность FIN ---"
ssh_fin "echo 'FIN OK: $(hostname)'"

echo "--- [2/5] docker + git на FIN ---"
ssh_fin "
  if ! command -v docker &>/dev/null; then
    apt-get update -qq
    apt-get install -y --no-install-recommends docker.io docker-compose-v2 git
    systemctl enable --now docker
  else
    echo 'docker already installed'
  fi
"

echo "--- [3/5] клонируем репо на FIN ---"
ssh_fin "
  if [ ! -d '$DEPLOY_DIR/.git' ]; then
    git clone '$REPO_URL' '$DEPLOY_DIR'
  else
    echo 'repo already cloned'
  fi
"

echo "--- [4/5] .env + postgres dump ---"
echo ""
echo "  !! ВАЖНО: создай $DEPLOY_DIR/.env на FIN вручную:"
echo "     ssh -p $FIN_PORT root@$FIN_HOST"
echo "     cp /opt/vpndeployer/.env.example /opt/vpndeployer/.env"
echo "     nano /opt/vpndeployer/.env   # вставь BOT_TOKEN, POSTGRES_PASSWORD и т.д."
echo ""

# восстанавливаем postgres если дамп не пустой
if [ -s "$PG_DUMP_LOCAL" ] && [ "$(wc -c < "$PG_DUMP_LOCAL")" -gt 100 ]; then
  echo "--- [4b] копируем и восстанавливаем postgres dump ---"
  scp_to_fin "$PG_DUMP_LOCAL" deployer-postgres.sql
  ssh_fin "
    cd '$DEPLOY_DIR'
    docker compose up -d postgres
    sleep 5
    docker compose exec -T postgres psql -U deployer deployer < deployer-postgres.sql
    rm deployer-postgres.sql
  "
else
  echo "--- [4b] postgres dump пустой или маленький — пропускаем, БД создастся с нуля ---"
fi

echo ""
echo "--- [5/5] запускаем deployer-bot на FIN ---"
echo ""
echo "  !! После создания .env выполни:"
echo "     ssh -p $FIN_PORT root@$FIN_HOST"
echo "     cd $DEPLOY_DIR && docker compose up -d --build"
echo "     docker compose ps"
echo ""
echo "--- [6/6] останавливаем deployer-bot на RU ---"
echo ""
echo "  !! После проверки что FIN работает:"
echo "     ssh -p $RU_PORT root@$RU_HOST"
echo "     cd /opt/vpndeployer && docker compose down"
echo ""
echo "  !! Обнови GitHub Actions secrets в репо vpndeployer:"
echo "     SSH_HOST  → $FIN_HOST"
echo "     SSH_PORT  → $FIN_PORT"
echo ""
echo "=== ГОТОВО ==="
