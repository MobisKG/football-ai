"""Управление прогоном на сервере с ноутбука: залить код, попросить прогон, смотреть.

    python -m full_run.remote deploy      # залить код на сервер и пересобрать контейнер
    python -m full_run.remote start [--per-pod 2 --max-usd 30]
    python -m full_run.remote status      # этап, машины, деньги
    python -m full_run.remote log [-n 50] # хвост журнала сторожа
    python -m full_run.remote stop        # остановить прогон и удалить все машины

Сервер и пользователь — из переменных FB_SERVER (по умолчанию 46.101.9.74) и
FB_SERVER_USER (root). Нужен доступ по ключу: ssh-copy-id один раз.
Прогон идёт на сервере, поэтому ноутбук можно закрыть в любой момент.
"""
import os, subprocess, sys

HOST = os.environ.get("FB_SERVER", "46.101.9.74")
USER = os.environ.get("FB_SERVER_USER", "root")
BASE = os.environ.get("FB_SERVER_BASE", "/opt/football-runner")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SSH = ["ssh", "-o", "ConnectTimeout=20", "-o", "BatchMode=yes", "%s@%s" % (USER, HOST)]

def sh(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=kw.pop("timeout", 600), **kw)
    return p.returncode, (p.stdout + p.stderr).strip()

def need_access():
    rc, out = sh(SSH + ["echo ok"])
    if rc != 0:
        sys.exit("нет доступа к серверу %s@%s по ключу.\nОдин раз выполните:  ssh-copy-id %s@%s\nОтвет сервера: %s"
                 % (USER, HOST, USER, HOST, out[-200:]))

def deploy():
    need_access()
    sh(SSH + ["mkdir -p %s/repo %s/results %s/ssh %s/cache" % (BASE, BASE, BASE, BASE)])
    # только код и веса: результаты прошлых прогонов на сервер не нужны
    rc, out = sh(["rsync", "-az", "--delete", "--exclude", "results", "--exclude", ".git",
                  "--exclude", "__pycache__", "--exclude", "*.mp4",
                  ROOT + "/", "%s@%s:%s/repo/" % (USER, HOST, BASE)], timeout=1800)
    if rc != 0: sys.exit("rsync: " + out[-300:])
    rc, out = sh(SSH + ["cd %s && cp repo/server/docker-compose.yml . && docker compose up -d --build 2>&1 | tail -3" % BASE], timeout=1800)
    print(out[-800:])
    rc, out = sh(SSH + ["docker ps --filter name=football-runner --format '{{.Names}} {{.Status}}'"])
    print(out or "контейнер не запустился — проверьте %s/env" % BASE)

def start(args):
    need_access()
    rc, out = sh(SSH + ["cd %s && echo '%s' > results/RUN_REQUEST && ls -la results/RUN_REQUEST" % (BASE, " ".join(args))])
    print(out); print("прогон начнётся в течение минуты; смотреть:  python -m full_run.remote log")

def status():
    need_access()
    rc, out = sh(SSH + [
        "cd %s/results 2>/dev/null && for d in run_*/; do "
        "[ -f \"$d/state.json\" ] && python3 -c \"import json;s=json.load(open('$d/state.json'));"
        "v=s['videos'];print('$d', 'этап', s.get('stage'), '| игр', len(v), '| готово', "
        "sum(1 for x in v.values() if x.get('collected')), '| потрачено %.2f $' % s.get('spent_usd',0))\"; done; "
        "echo '--- сторож:'; tail -n 3 watch.log 2>/dev/null" % BASE])
    print(out or "прогонов ещё не было")

def logs(n):
    need_access()
    rc, out = sh(SSH + ["cd %s/results && tail -n %d $(ls -t run_*/run.log 2>/dev/null | head -1) 2>/dev/null; "
                        "echo '--- сторож:'; tail -n 5 watch.log" % (BASE, n)])
    print(out)

def stop():
    need_access()
    rc, out = sh(SSH + ["pkill -f '[f]ull_run.run'; sleep 2; "
                        "docker exec football-runner python -c \"import sys;sys.path.insert(0,'/app/scripts');"
                        "from full_run.core import delete_all_pods;print(delete_all_pods())\" 2>&1 | tail -3"])
    print(out); print("прогон остановлен, машины удалены")

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "deploy": deploy()
    elif cmd == "start": start(sys.argv[2:])
    elif cmd == "status": status()
    elif cmd == "log": logs(int(sys.argv[sys.argv.index("-n") + 1]) if "-n" in sys.argv else 40)
    elif cmd == "stop": stop()
    else: sys.exit(__doc__)

if __name__ == "__main__":
    main()
