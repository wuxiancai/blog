#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR=/opt/private-blog/app
VENV=/opt/private-blog/venv
SERVICE_USER=private-blog
ENV_DIR=/etc/private-blog
DATA_DIR=/var/lib/private-blog
BACKUP_DIR=/var/backups/private-blog
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SITE=/etc/nginx/sites-available/private-blog
SITE_LINK=/etc/nginx/sites-enabled/private-blog
UNIT=/etc/systemd/system/private-blog.service
TMP_UNIT="$(mktemp)"; TMP_SITE="$(mktemp)"; TMP_ENV="$(mktemp)"
cleanup(){ rm -f "$TMP_UNIT" "$TMP_SITE" "$TMP_ENV"; }
trap cleanup EXIT
fail(){ echo "部署失败：$*" >&2; exit 1; }

[[ "$(uname -s)" == Linux ]] || fail "部署仅支持 Ubuntu Linux。"
[[ $EUID -eq 0 ]] || fail "请使用 sudo bash deploy.sh。"
command -v apt-get >/dev/null || fail "未找到 APT，不支持此系统。"
source /etc/os-release
[[ "${ID:-}" == ubuntu ]] || fail "仅支持 Ubuntu。"
[[ "${VERSION_ID:-}" == 26.04 || "${VERSION_ID:-}" == 24.04 ]] || echo "提示：当前 Ubuntu ${VERSION_ID:-unknown}，脚本优先针对 26.04 编写。"
ARCH="$(dpkg --print-architecture)"
[[ "$ARCH" == amd64 || "$ARCH" == arm64 ]] || fail "不支持的架构：$ARCH"
AVAIL_KB="$(df -Pk / | awk 'NR==2 {print $4}')"
(( AVAIL_KB >= 2097152 )) || fail "根分区至少需要 2 GiB 可用空间。"
MEM_KB="$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)"
(( MEM_KB >= 524288 )) || echo "警告：可用内存低于 512 MiB，安装过程可能受限。"
if ! getent hosts archive.ubuntu.com >/dev/null 2>&1; then echo "警告：DNS 网络检查失败，继续尝试 APT。"; fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip nginx ca-certificates curl rsync
python3 -c 'import sys; assert sys.version_info >= (3, 11), sys.version'
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
install -d -o root -g "$SERVICE_USER" -m 0750 /opt/private-blog "$APP_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_DIR" "$DATA_DIR/uploads"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$BACKUP_DIR"
install -d -o root -g "$SERVICE_USER" -m 0750 "$ENV_DIR"
if [[ ! -e "$APP_DIR/.private-blog-deploy-managed" && -n "$(find "$APP_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  fail "$APP_DIR 已包含未由本脚本管理的文件，请先检查。"
fi
if [[ -e "$SITE" ]] && ! grep -q '^# Managed by private-blog deploy.sh$' "$SITE"; then fail "$SITE 已存在且不属于本项目，拒绝覆盖。"; fi
if [[ -L "$SITE_LINK" ]] && [[ "$(readlink -f "$SITE_LINK" || true)" != "$SITE" ]]; then fail "$SITE_LINK 指向其他站点，拒绝覆盖。"; fi
if [[ -e "$UNIT" ]] && ! grep -q '^Description=Private Blog FastAPI application$' "$UNIT"; then fail "$UNIT 已存在且不属于本项目，拒绝覆盖。"; fi
rsync -a --delete --exclude='.private-blog-deploy-managed' --exclude='.git/' --exclude='.venv/' --exclude='data/' --exclude='*.db*' --exclude='.env' "$SCRIPT_DIR/" "$APP_DIR/"
touch "$APP_DIR/.private-blog-deploy-managed"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
cd "$APP_DIR"
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$ENV_DIR/private-blog.env" ]]; then
  APP_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  INITIAL_ADMIN_PASSWORD="${INITIAL_ADMIN_PASSWORD:-1234}"
  umask 077
  cat > "$TMP_ENV" <<EOF
APP_SECRET=$APP_SECRET
INITIAL_ADMIN_PASSWORD=$INITIAL_ADMIN_PASSWORD
DATABASE_URL=sqlite:////var/lib/private-blog/blog.sqlite3
DATA_DIR=/var/lib/private-blog
BACKUP_DIR=/var/backups/private-blog
APP_TIMEZONE=Asia/Shanghai
COOKIE_SECURE=false
MAX_UPLOAD_BYTES=524288000
MAX_UPLOAD_TOTAL_BYTES=21474836480
AUTO_MIGRATE=true
EOF
  install -o root -g "$SERVICE_USER" -m 0640 "$TMP_ENV" "$ENV_DIR/private-blog.env"
fi
chown -R root:"$SERVICE_USER" "$APP_DIR"
chmod -R u=rwX,g=rX,o= "$APP_DIR"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$DATA_DIR" "$BACKUP_DIR"
cd "$APP_DIR"
runuser -u "$SERVICE_USER" -- env DATABASE_URL=sqlite:////var/lib/private-blog/blog.sqlite3 "$VENV/bin/alembic" upgrade head
install -o root -g root -m 0644 "$APP_DIR/deploy/private-blog.service" "$UNIT"
install -o root -g root -m 0644 "$APP_DIR/deploy/nginx-private-blog.conf" "$SITE"
if [[ -e "$SITE_LINK" && ! -L "$SITE_LINK" ]]; then fail "$SITE_LINK 是用户文件，请先检查冲突并自行处理。"; fi
ln -sfn "$SITE" "$SITE_LINK"
systemd-analyze verify "$UNIT" || fail "systemd 服务文件校验失败。"
nginx -t || fail "Nginx 配置校验失败。"
systemctl daemon-reload
systemctl enable --now private-blog.service
nginx -t && systemctl reload nginx
systemctl enable nginx
for _ in {1..30}; do curl --fail --silent http://127.0.0.1:8010/health >/dev/null && break; sleep 1; done
curl --fail --silent http://127.0.0.1:8010/health >/dev/null || { systemctl status private-blog --no-pager || true; fail "应用健康检查失败。"; }
PUBLIC_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "部署成功： http://${PUBLIC_IP:-服务器公网IP}/"
echo "首次登录：admin / ${INITIAL_ADMIN_PASSWORD:-首次安装时设置的密码}，首次进入必须修改密码。"
echo "数据目录：$DATA_DIR；附件：$DATA_DIR/uploads；备份：$BACKUP_DIR"
echo "备份：sudo -u $SERVICE_USER $VENV/bin/python -m app.backup backup"
echo "停止：sudo bash $APP_DIR/stop.sh；更新：sudo bash $APP_DIR/deploy.sh"
echo "公网仅需开放 TCP 80；云厂商安全组请确认，HTTP 登录流量未加密。"
