#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE=/etc/private-blog/private-blog.env
SERVICE=private-blog.service
fail(){ echo "启动失败：$*" >&2; exit 1; }

[[ "$(uname -s)" == Linux ]] || fail "启动脚本仅支持 Linux 服务器。"
[[ $EUID -eq 0 ]] || fail "请使用 sudo bash start.sh。"
[[ -f "$ENV_FILE" ]] || fail "未找到部署配置 $ENV_FILE，请先运行 deploy.sh 完成首次部署。"
systemctl list-unit-files "$SERVICE" --no-legend 2>/dev/null | grep -q "$SERVICE" || fail "未找到 $SERVICE，请先运行 deploy.sh 完成首次部署。"

APP_PORT="$(sed -n 's/^APP_PORT=\([0-9][0-9]*\)$/\1/p' "$ENV_FILE" | tail -n 1)"
[[ "$APP_PORT" =~ ^[0-9]{1,5}$ ]] && (( APP_PORT >= 1 && APP_PORT <= 65535 )) || fail "部署配置中的 APP_PORT 无效。"

systemctl start "$SERVICE" || { systemctl status "$SERVICE" --no-pager || true; fail "systemd 启动服务失败。"; }
for _ in {1..30}; do
  if curl --fail --silent --max-time 3 "http://127.0.0.1:$APP_PORT/health" >/dev/null; then
    echo "私有博客已启动并通过健康检查。"
    echo "服务：$SERVICE；应用端口：$APP_PORT；Nginx 代理端口：80"
    exit 0
  fi
  sleep 1
done
systemctl status "$SERVICE" --no-pager || true
fail "服务已启动，但 $APP_PORT 端口健康检查未通过。"
