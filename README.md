# 纸间 · 私有博客

基于 FastAPI、SQLAlchemy、Alembic、SQLite、Jinja2 和原生 CSS 的轻量私有博客。默认需要登录；不提供公开注册。

## 本地开发（macOS）

需要 Python 3.11 或更新版本。Python 3.12 推荐：

```bash
uv python install 3.12
uv sync --python 3.12 --extra test
cp .env.example .env
set -a; source .env; set +a
uv run uvicorn app.main:app --reload
```

首次启动会初始化 `admin`，默认开发密码为 `1234`，首次登录必須修改密码。可通过 `INITIAL_ADMIN_PASSWORD` 覆盖初始密码。SQLite 默认位于项目目录的 `private_blog.db`，附件位于 `data/uploads`，备份位于 `data/backups`。生产环境请用随机 `APP_SECRET`。

## Ubuntu 部署

`deploy.sh` 支持 Ubuntu 26.04（脚本也兼容 24.04），安装 Ubuntu 仓库的 `python3`、`python3-venv` 和 Nginx，不替换系统 Python。依赖版本通过 `uv.lock` 锁定，部署使用锁定的 `requirements.txt`。脚本需在项目根目录执行并使用 root：

```bash
sudo bash deploy.sh
```

应用以单个 Uvicorn worker 运行于 `0.0.0.0:8000` 起始端口，每次部署都从 8000 重新扫描，选择 8999 内第一个可用端口；探测会忽略已关闭连接残留的 TCP `TIME_WAIT` 状态。Nginx 仍监听 HTTP 80 并代理到所选端口。脚本只添加自己的 Nginx site，不关闭或覆盖已有站点；若已有默认站点占用公网 IP 默认路由，应检查服务器 Nginx 虚拟主机配置。部署成功时自动探测公网 IP 并输出直连地址 `http://IP:端口/` 和 Nginx 地址 `http://IP/`。云安全组需开放 TCP 80 和输出的应用端口；脚本不擅自改 UFW。

部署位置：

- 应用：`/opt/private-blog/app`
- 虚拟环境：`/opt/private-blog/venv`
- 配置：`/etc/private-blog/private-blog.env`（权限 0640）
- SQLite 数据：`/var/lib/private-blog/blog.sqlite3`
- 附件：`/var/lib/private-blog/uploads/`
- 备份：`/var/backups/private-blog/`
- 服务：`private-blog.service`；日志：`journalctl -u private-blog -f`

服务占用主要来自一个 Python/Uvicorn 进程、Nginx 和 SQLite。部署为单 worker；实际内存占用随内容和请求变化，应在目标机用 `systemctl status`、`ps`、`free -h` 实测。磁盘清理前先下载并验证备份；不要手工删除数据库的 WAL/SHM 文件或正在使用的附件。

HTTP 不加密登录流量。默认密码必须立即修改；公网使用时请优先通过 VPN、SSH 隧道或其他可信访问控制保护通信。可自行接入 TLS 终止代理。

## 核心功能

- Argon2 密码哈希、首次修改密码、服务端密码策略、会话版本失效、登录频率限制、CSRF 表单令牌、安全响应头。
- 管理员创建普通用户、编辑资料、禁用/启用、重置密码并管理用户组；不允许禁用最后一个管理员。
- 富文本文章编辑器（兼容原有 Markdown 文章），支持复制粘贴格式、工具栏排版、图片/视频粘贴或拖放、PDF 等附件链接；提供“保存草稿暂不发表”“实时发表”“定时发布”操作，默认所有登录用户可读，也可选择特定用户或用户组。
- 定时发布每分钟扫描一次，启动时补扫到期内容；时间以 UTC 存储，编辑器按 `APP_TIMEZONE`（默认 Asia/Shanghai）解释本地时间。DST 跳时导致不存在的时间会拒绝；秋季回拨的重复时间选第一次（fold=0）。
- 图片/视频/附件落在私有目录，编辑器上传后可直接插入正文，文件接口每次验证文章权限；单文件上限 500 MiB、默认总配额 20 GiB，使用随机存储名、类型白名单与文件头校验。
- 独立图集支持 JPG、PNG、GIF、WebP、HEIC、HEIF、DNG、RAW；文件夹上传自动跳过其他类型，浏览器无法直接预览的格式可下载原文件。
- 文章评论默认私密，可单独公开；至少支持一级回复。
- 关键文章、评论、用户及备份/恢复操作写入数据库审计日志，后台显示最近 200 条，不记录密码和会话令牌。
- 管理员可生成带 SQLite 一致性快照、附件和 SHA-256 清单的备份包。

## 备份、校验和迁移恢复

网页后台 `/admin/backups` 可创建、校验和下载备份。命令行操作：

```bash
cd /opt/private-blog/app
sudo -u private-blog /opt/private-blog/venv/bin/python -m app.backup backup
sudo -u private-blog /opt/private-blog/venv/bin/python -m app.backup validate /path/to/private-blog-backup-*.tar.gz
```

迁移到新服务器：先部署相同版本，再将备份包传至新服务器。恢复必须先停服务，校验包并自动生成当前数据的回滚备份：

```bash
sudo systemctl stop private-blog
sudo -u private-blog /opt/private-blog/venv/bin/python -m app.backup validate /path/to/backup.tar.gz
sudo -u private-blog /opt/private-blog/venv/bin/python -m app.backup restore /path/to/backup.tar.gz --confirm RESTORE
sudo systemctl start private-blog
curl --fail http://127.0.0.1:8000/health
```

恢复脚本在替换前检查归档路径、清单校验和和 SQLite 完整性；恢复后应用启动会重新扫描到期定时文章。恢复期间若操作失败，会尽力回滚原数据库和附件。备份包不包含应用密钥或明文密码，迁移时沿用目标服务器配置的 `APP_SECRET`。管理员也可在网页备份页上传备份并输入 `RESTORE` 执行恢复；恢复期间系统进入维护状态、清除当前登录会话并返回登录页。大文件恢复仍建议使用 CLI，避免浏览器或反向代理上传限制。

## 服务管理

```bash
sudo systemctl status private-blog
sudo systemctl restart private-blog
sudo bash /opt/private-blog/app/stop.sh
sudo bash /opt/private-blog/app/start.sh
```

`stop.sh` 和 `start.sh` 分别停止、启动本项目的 `private-blog.service`；启动脚本读取已配置端口并检查健康状态。两者都不扫描或终止其他进程，不停止共享 Nginx，也不删除数据。更新时在项目新版本目录执行 `sudo bash deploy.sh`；部署脚本只停止该 systemd 服务，端口扫描遇到其他程序占用时会跳过并顺延，不会停止端口占用者；下次部署仍从 8000 开始寻找第一个可用端口。脚本保留服务器数据和环境配置。

## 测试

```bash
uv run pytest -q
```

若端口发生顺延，恢复检查命令中的 `8000` 应替换为 `/etc/private-blog/private-blog.env` 中的 `APP_PORT`。部署脚本的 APT、systemd、Nginx、公网访问和重启恢复行为需在 Ubuntu 26.04 实机验证；macOS 上不能替代该环境测试。用户界面使用英文系统字体栈，不依赖 CDN 或前端构建系统。
