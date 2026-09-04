#!/bin/sh
# RoadMan 服务器端部署脚本（由 cd-deploy.ps1 传输并执行）
# 用法: sh server-deploy.sh <tar文件路径>
set -e

TAR="$1"
DIR="/home/z2538/Desktop/RoadMan"
EXT_URL="https://roadman.pigz2538.top:55309"
FRPC_BIN="/home/z2538/chmlfrp/frpc"
FRPC_INI="/home/z2538/chmlfrp/frpc.ini"

echo "[server] 解压覆盖（保留 .env / docker-compose.yml / roadman-certs / roadman.db）"
cd "$DIR"
tar -xf "$TAR" --exclude=docker-compose.yml --exclude=.env --exclude=roadman-certs --exclude=roadman.db
echo "[server] EXTRACT_OK"

echo "[server] 确保 HTTPS 构建参数存在（内网穿透依赖 443 监听）"
grep -q '^ROADMAN_HTTPS=' .env || echo 'ROADMAN_HTTPS=true' >> .env
grep -q '^ROADMAN_SERVER_NAME=' .env || echo 'ROADMAN_SERVER_NAME=roadman.pigz2538.top' >> .env
grep -E '^ROADMAN_HTTPS=|^ROADMAN_SERVER_NAME=' .env

echo "[server] 重建容器"
docker compose up -d --build 2>&1 | tail -8
sleep 10

echo "[server] 容器状态"
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' | grep roadman || true

echo "[server] 内网验证"
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7777 || true)
HTTPS_CODE=$(curl -sk -o /dev/null -w '%{http_code}' --connect-timeout 5 https://127.0.0.1:7443 || true)
echo "http 7777: $HTTP_CODE"
echo "https 7443: $HTTPS_CODE"

echo "[server] 容器内 nginx 监听检查"
docker exec roadman-frontend-1 sh -c 'netstat -tlnp 2>/dev/null | grep -E ":(80|443) " || ss -tlnp | grep -E ":(80|443) "' || true

echo "[server] 外网隧道验证"
check_ext() {
  curl -sk -o /dev/null -w '%{http_code}' --connect-timeout 10 "$EXT_URL" || echo "000"
}
EXT_CODE=$(check_ext)
echo "外网 $EXT_URL: $EXT_CODE"

if [ "$EXT_CODE" != "200" ]; then
  echo "[server] 外网不通，重启 frpc 隧道"
  pkill -f "$FRPC_BIN" 2>/dev/null || true
  sleep 2
  nohup "$FRPC_BIN" -c "$FRPC_INI" > /tmp/frpc.log 2>&1 &
  sleep 8
  echo "[server] frpc 进程:"
  ps aux | grep "$FRPC_BIN" | grep -v grep || echo "frpc 未运行!"
  echo "[server] frpc 日志:"
  tail -5 /tmp/frpc.log 2>/dev/null || true
  echo "[server] 等待 30s 让旧会话过期后重试"
  sleep 30
  EXT_CODE=$(check_ext)
  echo "外网重试 $EXT_URL: $EXT_CODE"
fi

if [ "$EXT_CODE" = "200" ]; then
  echo "[server] RESULT: 外网隧道 OK"
else
  echo "[server] RESULT: 外网隧道仍不可达（内网已恢复，隧道可能需要更长时间或手动处理）"
fi
