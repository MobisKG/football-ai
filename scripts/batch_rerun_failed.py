"""Сторож пакетного прогона: ждёт завершения batch_run.py и, если остались
невыполненные игры (упавшие дважды, недосчитанные после сбоя), возвращает их
в очередь и запускает прогон заново. Не больше MAX_RERUNS раз, и повтор только
если прошлый запуск что-то доделал.

    python batch_rerun_failed.py <matches.json> [--pods 4] [--max 2]
"""
import json, os, subprocess, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
MATCHES = os.path.abspath(sys.argv[1]); OUT = os.path.dirname(MATCHES)
opt = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
PODS = opt("--pods", "4"); MAX_RERUNS = int(opt("--max", "2"))
STATE = os.path.join(OUT, "state.json"); LOG = os.path.join(OUT, "rerun.log")
def log(*a):
    line = "[%s] %s" % (time.strftime("%d.%m %H:%M:%S"), " ".join(map(str, a)))
    print(line, flush=True); open(LOG, "a").write(line + "\n")
def running():
    r = subprocess.run(["pgrep", "-f", "scripts/batch_run[.]py"], capture_output=True, text=True)
    return bool(r.stdout.strip())
def done_count():
    s = json.load(open(STATE)); return sum(1 for v in s["videos"].values() if v["status"] == "done")

log("сторож запущен, жду завершения batch_run.py")
last_done = None
for rerun in range(MAX_RERUNS + 1):
    quiet = 0
    while quiet < 3:                      # три проверки подряд без процесса — точно завершился
        quiet = 0 if running() else quiet + 1
        time.sleep(60)
    M = json.load(open(MATCHES)); s = json.load(open(STATE))
    slugs = [v["slug"] for v in M["videos"] if "parts" not in v]
    left = [k for k in slugs if s["videos"].get(k, {}).get("status") != "done"]
    d = done_count()
    log("batch_run.py завершён; готово %d, не готово %d: %s" % (d, len(left), left))
    if not left: log("всё готово, сторож выходит"); break
    if rerun == MAX_RERUNS: log("лимит перезапусков исчерпан, остаток — в отчёт"); break
    if last_done is not None and d <= last_done: log("прошлый перезапуск ничего не доделал — не повторяю"); break
    last_done = d
    for k in left:
        v = s["videos"].setdefault(k, {"status": "queued", "attempts": 0, "gpu_min": 0, "log": []})
        if v["status"] in ("failed", "running", "queued"):
            v["log"].append("возвращено в очередь сторожем после завершения прогона (было: %s)" % v["status"])
            v["status"] = "queued"; v["attempts"] = 0
        elif v["status"] == "mac_failed":
            v["status"] = "gpu_done"
    json.dump(s, open(STATE + ".tmp", "w"), ensure_ascii=False, indent=1); os.replace(STATE + ".tmp", STATE)
    log("перезапуск %d: %d игр в очереди" % (rerun + 1, len(left)))
    subprocess.Popen([sys.executable, os.path.join(HERE, "batch_run.py"), "--matches", MATCHES, "--pods", PODS],
                     stdout=open(os.path.join(OUT, "full_run.out"), "a"), stderr=subprocess.STDOUT, start_new_session=True)
    time.sleep(120)
