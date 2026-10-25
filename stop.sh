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
python3 - /opt/private-blog/app <<'PY'
import os, signal, sys, time
app_dir = os.path.realpath(sys.argv[1])
def matches():
    found = []
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        try:
            pid = int(entry)
            if pid == os.getpid() or os.path.realpath(f'/proc/{pid}/cwd') != app_dir:
                continue
            args = open(f'/proc/{pid}/cmdline', 'rb').read().replace(b'\0', b' ')
            if b'uvicorn' in args and b'app.main:app' in args:
                found.append(pid)
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return found
pids = matches()
for pid in pids:
    try: os.kill(pid, signal.SIGTERM)
    except ProcessLookupError: pass
deadline = time.monotonic() + 10
while pids and time.monotonic() < deadline:
    time.sleep(.25)
    pids = matches()
for pid in pids:
    try: os.kill(pid, signal.SIGKILL)
    except ProcessLookupError: pass
if pids:
    raise SystemExit(f'无法停止私有博客残留进程：{pids}')
PY
echo "数据、附件、配置、备份和共享 Nginx 均未改动。"
