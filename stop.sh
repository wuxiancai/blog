#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "请使用 sudo bash stop.sh。" >&2; exit 1; }
if systemctl list-unit-files private-blog.service --no-legend 2>/dev/null | grep -q private-blog.service; then
  systemctl stop private-blog.service || true
  if systemctl is-active --quiet private-blog.service; then echo "私有博客仍在运行。" >&2; exit 1; fi
  echo "私有博客应用服务已停止。"
else
  echo "未安装私有博客 systemd 服务，无需停止。"
fi
echo "数据、附件、配置、备份和共享 Nginx 均未改动。"
