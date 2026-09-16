"""Сборщик: как только с машины пришла привязка (track_auto.json), считает пробег и
скорость, проверяет правдоподобие и грузит на сайт.

    python finish_queue.py [--upload] [--workers 2] [--limit-min 600]
"""
import os, sys, json, glob, time, subprocess, threading
HERE = os.path.dirname(os.path.abspath(__file__)); SCR = os.path.dirname(HERE); ROOT = os.path.dirname(SCR)
def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
UPLOAD = "--upload" in sys.argv; NW = int(opt("--workers", 2)); LIMIT = float(opt("--limit-min", 600)) * 60
PY = sys.executable; lock = threading.Lock(); busy = set()
def log(*a):
    with lock: print(time.strftime("%H:%M:%S"), *a, flush=True)
jobs = [j["slug"] for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json"))) if not j.get("merged_into")]
def ready(s): return os.path.exists(os.path.join(ROOT, "results/pitch", s, "track_auto.json"))
def done(s): return os.path.exists(os.path.join(ROOT, "results/pitch", s, "speeds_auto", "plausibility.json"))
def one(s):
    args = [PY, os.path.join(HERE, "finish_game.py"), s] + (["--upload"] if UPLOAD else [])
    r = subprocess.run(["nice", "-n", "15"] + args, capture_output=True, text=True)
    open(os.path.join(ROOT, "results/pitch", s, "finish.log"), "w").write(r.stdout + r.stderr)
    tail = [l for l in r.stdout.strip().splitlines() if "правдоподобие" in l or "загрузка" in l or "НЕ ГРУЖУ" in l]
    log(s, "готово" if r.returncode == 0 else "ОШИБКА", (tail[-1] if tail else r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-150:])[:240])
def worker():
    t0 = time.time()
    while time.time() - t0 < LIMIT:
        with lock:
            todo = [s for s in jobs if ready(s) and not done(s) and s not in busy]
            if todo: s = todo[0]; busy.add(s)
        if not todo:
            if all(done(x) or not ready(x) for x in jobs) and all(ready(x) for x in jobs): return
            time.sleep(60); continue
        try: one(s)
        except Exception as e: log(s, "СБОЙ", str(e)[:150])
        finally:
            with lock: busy.discard(s)
ths = [threading.Thread(target=worker, daemon=True) for _ in range(NW)]
[t.start() for t in ths]
while any(t.is_alive() for t in ths): time.sleep(10)
log("сборщик закончил")
