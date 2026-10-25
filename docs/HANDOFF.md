# 交接记录

日期：2026-10-25

## 当前状态

项目从空目录建立。核心后端、模板、SQLite/Alembic、运维文件已写入；pytest 集成测试 4 项通过，Python 编译检查、依赖锁检查、Alembic 升级和 shell 语法检查通过。服务启动时运行 Alembic 迁移，避免现有数据库缺少新字段。部署脚本从 8000 起自动选端口、探测公网 IPv4，并输出应用直连和 Nginx 两个地址。部署和停止仅对本项目 `private-blog.service` 执行停止操作，不扫描或终止系统内其他进程；被占用端口会直接跳过。

## 重要路径

- 入口：`app/main.py`
- 模型/数据库：`app/models.py`、`app/database.py`
- 运维：`deploy.sh`、`stop.sh`、`deploy/`
- 备份：`app/backup.py`

## 尚未完成/限制

- Ubuntu 26.04 专属安装、systemd、Nginx 和公网流量仍需目标环境验证。
- 自动化测试覆盖登录、权限、XSS、附件、定时发布、备份恢复、用户管理及夏令时边界；完整 PRD 场景仍未逐项覆盖。
- 尚未在 Ubuntu 26.04 实机运行部署脚本，systemd/Nginx 和公网访问须在目标机验收。
- 测试运行后更新本文件。
