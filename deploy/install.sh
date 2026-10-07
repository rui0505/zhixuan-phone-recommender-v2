#!/usr/bin/env bash
# Run only from a verified bundle on the explicitly approved demonstration ECS.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
task_release=$(pwd -P)
case "$task_release" in /opt/zhixuan/releases/*) ;; *) echo 'Refusing unexpected release path' >&2; exit 1;; esac
test "$(id -u)" -eq 0
test -x /opt/zhixuan/.venv/bin/python
test ! -e /etc/systemd/system/zhixuan.service
test ! -e /etc/nginx/sites-available/zhixuan
test ! -e /opt/zhixuan/current
test ! -e /opt/zhixuan/nginx-default-link.backup
id zhixuan >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin zhixuan
/opt/zhixuan/.venv/bin/python -m pip install --index-url https://pypi.org/simple --only-binary=:all: -r requirements-api.txt
chmod -R a+rX "$task_release"
ln -s "$task_release" /opt/zhixuan/current
install -m 0644 deploy/zhixuan.service /etc/systemd/system/zhixuan.service
systemctl daemon-reload
systemctl enable --now zhixuan
for attempt in $(seq 1 30); do curl -fsS http://127.0.0.1:8600/readyz >/dev/null && break; sleep 1; done
curl -fsS http://127.0.0.1:8600/readyz >/dev/null
/opt/zhixuan/.venv/bin/python scripts/api_smoke.py --base http://127.0.0.1:8600
install -m 0644 deploy/zhixuan-nginx.conf /etc/nginx/sites-available/zhixuan
ln -s /etc/nginx/sites-available/zhixuan /etc/nginx/sites-enabled/zhixuan
if [ -L /etc/nginx/sites-enabled/default ]; then mv /etc/nginx/sites-enabled/default /opt/zhixuan/nginx-default-link.backup; fi
if nginx -t; then systemctl reload nginx; else
    unlink /etc/nginx/sites-enabled/zhixuan
    if [ -L /opt/zhixuan/nginx-default-link.backup ]; then mv /opt/zhixuan/nginx-default-link.backup /etc/nginx/sites-enabled/default; fi
    exit 1
fi
curl -fsS http://127.0.0.1/readyz
echo 'LOCAL_DEPLOYMENT_VERIFIED; external browser acceptance still required'
