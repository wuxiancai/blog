# 项目上下文

- 目标：2 vCPU / 2GB 服务器上的轻量私有知识库，最多 1～2 人使用。
- 技术：FastAPI、SQLAlchemy 2、SQLite WAL、Alembic、Jinja2、Markdown、Nginx、systemd。
- 存储：生产 SQLite 与附件分离于应用代码，归档包含一致性 SQLite 快照及本地附件。
- 权限：默认登录可读；文章支持指定读者；评论默认私密且可单独公开。
- 时间：DB 保存 UTC；界面时间区采用 `APP_TIMEZONE`，默认 `Asia/Shanghai`。
- 生产进程：单 Uvicorn worker，应用只监听回环地址，Nginx 共用系统服务。
