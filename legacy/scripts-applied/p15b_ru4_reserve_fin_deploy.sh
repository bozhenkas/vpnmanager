#!/usr/bin/env bash
# P.15b: ru-4 standalone xray — client TCP Reality vision :443 -> FIN egress.
# run on ru-4 as root. RU panel down OK; user UUIDs synced from FIN remnanode live config.
set -euo pipefail

BACKUP_DIR="/root/deploy-backups/$(date -u +%Y%m%dT%H%M%SZ)-p15b-ru4-reserve-fin"
XRAY_DIR="/usr/local/share/xray"
XRAY_BIN="/usr/local/bin/xray"
CFG="/etc/xray/reserve-fin.json"
ENV_FILE="/etc/xray/reserve-fin.env"

# FIN inter-node Reality (live 2026-06-07, port 443, sni www.microsoft.com)
FIN_HOST="${FIN_HOST:-127.0.0.1}" # ru4-fin-tunnel.service -> FIN:17905
FIN_PORT="${FIN_PORT:-17905}"
FIN_UUID="${FIN_UUID:-0ead0442-4bae-4ba9-b4bd-a146fe540e13}"

mkdir -p "$BACKUP_DIR"
cp -a /etc/nginx/nginx.conf "$BACKUP_DIR/nginx.conf.bak" 2>/dev/null || true
systemctl stop nginx 2>/dev/null || true
systemctl disable nginx 2>/dev/null || true

mkdir -p "$XRAY_DIR" /etc/xray /var/log/xray
if [[ ! -x "$XRAY_BIN" ]]; then
  unzip -o /root/xray.zip xray geoip.dat geosite.dat -d /tmp/xray-extract
  install -m 755 /tmp/xray-extract/xray "$XRAY_BIN"
  install -m 644 /tmp/xray-extract/geoip.dat /tmp/xray-extract/geosite.dat "$XRAY_DIR/"
fi

# reserve client inbound keys
if [[ ! -f "$ENV_FILE" ]]; then
  KEY_OUT=$("$XRAY_BIN" x25519)
  RESERVE_PRIV=$(echo "$KEY_OUT" | awk '/PrivateKey:/ {print $2}')
  RESERVE_PBK=$(echo "$KEY_OUT" | awk '/Password \(PublicKey\):/ {print $3}')
  [[ -z "$RESERVE_PRIV" ]] && RESERVE_PRIV=$(echo "$KEY_OUT" | awk '/Private key:/ {print $3}')
  [[ -z "$RESERVE_PBK" ]] && RESERVE_PBK=$(echo "$KEY_OUT" | awk '/Public key:/ {print $3}')
  RESERVE_SID=$(openssl rand -hex 8)
  RESERVE_SNI="${RESERVE_SNI:-web.max.ru}"
  cat >"$ENV_FILE" <<EOF
RESERVE_PRIV=$RESERVE_PRIV
RESERVE_PBK=$RESERVE_PBK
RESERVE_SID=$RESERVE_SID
RESERVE_SNI=$RESERVE_SNI
EOF
  chmod 600 "$ENV_FILE"
fi
# shellcheck disable=SC1090
source "$ENV_FILE"
if [[ -z "${RESERVE_PRIV:-}" || -z "${RESERVE_PBK:-}" ]]; then
  KEY_OUT=$("$XRAY_BIN" x25519)
  RESERVE_PRIV=$(echo "$KEY_OUT" | awk '/PrivateKey:/ {print $2}')
  RESERVE_PBK=$(echo "$KEY_OUT" | awk '/Password \(PublicKey\):/ {print $3}')
  RESERVE_SID=${RESERVE_SID:-$(openssl rand -hex 8)}
  RESERVE_SNI=${RESERVE_SNI:-web.max.ru}
  cat >"$ENV_FILE" <<EOF
RESERVE_PRIV=$RESERVE_PRIV
RESERVE_PBK=$RESERVE_PBK
RESERVE_SID=$RESERVE_SID
RESERVE_SNI=$RESERVE_SNI
EOF
  chmod 600 "$ENV_FILE"
fi
cp -a "$ENV_FILE" "$BACKUP_DIR/reserve-fin.env.bak"

# Сохранённый клиентский reserve-профиль: менять нельзя без обновления подписок.
RESERVE_PRIV="KJDUv7z4QoEPeESk0pn-7Ftscziso-HgGq6iM0Kf8mk"
RESERVE_PBK="UpJ1_AFXOqNJUKlIaqj_C4XUipOd7Eg489xQWmuAbiY"
RESERVE_SID="1df9284e42105047"
RESERVE_SNI="web.max.ru"

# user UUIDs: pass CLIENT_UUIDS env from deploy host, or fetch via FIN ssh
if [[ -z "${CLIENT_UUIDS:-}" ]]; then
  CLIENT_UUIDS=$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 -i /root/.ssh/id_rsa -p 17904 root@77.110.108.57 \
    'docker exec remnanode node -e "
const http=require(\"http\");
http.get({socketPath:\"/run/remnawave-internal-LrxBfSluxf.sock\",path:\"/internal/get-config?token=o60vffw7tDjz4XTsS58nJjNE7bKyAiqZ93PGMgSSZuGD3txHiYSkNe8f6M9eAncZ\",headers:{Host:\"internal\"}},res=>{let d=\"\";res.on(\"data\",c=>d+=c);res.on(\"end\",()=>{const ib=JSON.parse(d).inbounds.find(x=>x.tag===\"REMNA_VLESS_TCP_REALITY_7443\");console.log((ib.settings.clients||[]).map(c=>c.id).join(\",\"));});});
"' 2>/dev/null || true)
fi

if [[ -f /tmp/fin_uuids.txt && -z "${CLIENT_UUIDS:-}" ]]; then
  CLIENT_UUIDS=$(tr -d '\n' </tmp/fin_uuids.txt)
fi

if [[ -z "${CLIENT_UUIDS:-}" ]]; then
  echo "[ru-4] WARN: no CLIENT_UUIDS, using testremn only" >&2
  CLIENT_UUIDS="f2c2f7bf-1624-4fe5-83c1-9927fad9912a"
fi

IFS=',' read -r -a UUID_ARR <<<"$CLIENT_UUIDS"
CLIENTS_JSON='[]'
i=1
for u in "${UUID_ARR[@]}"; do
  u=$(echo "$u" | tr -d ' ')
  [[ -z "$u" ]] && continue
  email="u$i"
  CLIENTS_JSON=$(echo "$CLIENTS_JSON" | jq --arg id "$u" --arg email "$email" '. + [{"id":$id,"email":$email}]')
  i=$((i + 1))
done

jq -n \
  --arg priv "$RESERVE_PRIV" \
  --arg sid "$RESERVE_SID" \
  --arg sni "$RESERVE_SNI" \
  --argjson clients "$CLIENTS_JSON" \
  --arg fin_uuid "$FIN_UUID" \
  --arg fin_host "$FIN_HOST" \
  --argjson fin_port "$FIN_PORT" \
  '{
    log: {loglevel:"warning", access:"/var/log/xray/access.log", error:"/var/log/xray/error.log"},
    inbounds: [{
      tag: "GOIDA_RESERVE",
      listen: "0.0.0.0",
      port: 443,
      protocol: "vless",
      settings: {clients: $clients, decryption: "none"},
      sniffing: {enabled: true, destOverride: ["http","tls","quic"]},
      streamSettings: {
        network: "grpc",
        security: "reality",
        realitySettings: {
          show: false,
          target: "web.max.ru:443",
          xver: 0,
          serverNames: [$sni],
          privateKey: $priv,
          shortIds: [$sid]
        },
        # Happ сохраняет старую ссылку как mode=gun; серверу нужен multi-mode.
        grpcSettings: {serviceName: "grpc", multiMode: true}
      }
    }],
    outbounds: [{
      tag: "REMNA_FI",
      protocol: "vless",
      settings: {vnext: [{address: $fin_host, port: $fin_port, users: [{id: $fin_uuid, encryption: "none"}]}]},
      streamSettings: {network: "tcp", security: "none"}
    }],
    routing: {
      domainStrategy: "IPIfNonMatch",
      rules: [{type:"field", inboundTag:["GOIDA_RESERVE"], network:"tcp,udp", outboundTag:"REMNA_FI"}]
    }
  }' >"$CFG"

echo "clients ${#UUID_ARR[@]}"

cp -a "$CFG" "$BACKUP_DIR/reserve-fin.json.bak"

cat >/etc/systemd/system/xray-reserve-fin.service <<'UNIT'
[Unit]
Description=xray reserve ru-4 -> FIN (P.15b)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/xray/reserve-fin.env
ExecStart=/usr/local/bin/xray run -config /etc/xray/reserve-fin.json
Restart=on-failure
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable xray-reserve-fin
systemctl restart xray-reserve-fin
sleep 2
systemctl is-active xray-reserve-fin
ss -tlnp | grep ':443' || true

echo "BACKUP_DIR=$BACKUP_DIR"
echo "RESERVE_PBK=$RESERVE_PBK"
echo "RESERVE_SID=$RESERVE_SID"
echo "RESERVE_SNI=$RESERVE_SNI"
