"""Управление прогоном на сервере с ноутбука: залить код, попросить прогон, смотреть.

    python -m full_run.remote deploy      # залить код на сервер и пересобрать контейнер
    python -m full_run.remote start [--per-pod 2 --max-usd 30]
    python -m full_run.remote status      # этап, машины, деньги
    python -m full_run.remote log [-n 50] # хвост журнала сторожа
    python -m full_run.remote stop        # остановить прогон и удалить все машины

Сервер, пользователь и ключ — из переменных FB_SERVER (по умолчанию 46.101.9.74),
FB_SERVER_USER (root) и FB_SERVER_KEY (~/.ssh/id_ed25519_personal). Нужен доступ по
ключу: ssh-copy-id один раз.
Прогон идёт на сервере, поэтому ноутбук можно закрыть в любой момент.
"""
import os, subprocess, sys

HOST = os.environ.get("FB_SERVER", "46.101.9.74")
USER = os.environ.get("FB_SERVER_USER", "root")
BASE = os.environ.get("FB_SERVER_BASE", "/opt/football-runner")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY = os.path.expanduser(os.environ.get("FB_SERVER_KEY", "~/.ssh/id_ed25519_personal"))
SSH = ["ssh", "-o", "ConnectTimeout=20", "-o", "BatchMode=yes", "-i", KEY, "%s@%s" % (USER, HOST)]

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
    # какие ролики уже разобраны: папки результатов на сервер не едут (14 ГБ),
    # а без этого списка сервер посчитал бы старые игры новыми
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from full_run import channel
    known = os.path.join(ROOT, "results", "known.json"); channel.write_known(known)
    rc, out = sh(["scp", "-o", "BatchMode=yes", "-i", KEY, known, "%s@%s:%s/results/known.json" % (USER, HOST, BASE)])
    print("разобранных роликов передано:", len(__import__("json").load(open(known))))
    # только код и веса: результаты прошлых прогонов на сервер не нужны
    rc, out = sh(["rsync", "-az", "--delete", "-e", "ssh -o BatchMode=yes -i " + KEY,
                  "--exclude", "results", "--exclude", ".git",
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
    # проценты в питоновском однострочнике конфликтуют с %-подстановкой, поэтому путь подставляем replace
    script = ("cd BASE/results 2>/dev/null || exit 0; for d in run_*/; do [ -f \"$d/state.json\" ] && "
              "python3 -c \"import json,sys;s=json.load(open(sys.argv[1]));v=s['videos'];"
              "print(sys.argv[2],'этап',s.get('stage'),'| игр',len(v),'| собрано',"
              "sum(1 for x in v.values() if x.get('collected')),'| загружено',"
              "sum(1 for x in v.values() if str(x.get('upload','')).startswith('загружено')),"
              "'| потрачено',round(s.get('spent_usd',0),2),'$')\" \"$d/state.json\" \"$d\"; done; "
              "echo '--- сторож:'; tail -n 3 watch.log 2>/dev/null").replace("BASE", BASE)
    rc, out = sh(SSH + [script])
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
                        "from full_run.core import list_pods;print(\"машины на счету:\", [(p.get(\"id\"),p.get(\"name\")) for p in list_pods()])\" 2>&1 | tail -3"])
    print(out); print("прогон остановлен. Машины этого прогона удалит сам прогон при следующем запуске;\n"
                        "если нужно снести их немедленно — скажите, какие именно: чужие задачи на этом счету тоже бывают")

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
