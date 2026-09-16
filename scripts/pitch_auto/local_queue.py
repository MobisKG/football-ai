"""Местная очередь: по каждой игре с готовым видео — кадры, автоопоры, привязка, скорости, загрузка.

    python local_queue.py [--workers 2] [--weights weights/pitch_lines_v2.pt] [--upload] [--once]

Берёт игры из results/pitch/jobs.json, пропускает сдвоенные и уже посчитанные
(speeds_auto/plausibility.json). Ждёт, пока видео докачается (download_videos.py работает
параллельно). Пишет лог по игре в results/pitch/<slug>/local.log.
"""
import os, sys, json, glob, time, subprocess, threading
HERE = os.path.dirname(os.path.abspath(__file__)); SCR = os.path.dirname(HERE); ROOT = os.path.dirname(SCR)
sys.path.insert(0, HERE); from download_videos import video_ok
def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
W = opt("--weights", os.path.join(ROOT, "weights/pitch_lines_v2.pt")); PY = sys.executable
UPLOAD = "--upload" in sys.argv; NW = int(opt("--workers", 2))
lock = threading.Lock()
def log(*a): 
    with lock: print(time.strftime("%H:%M:%S"), *a, flush=True)
jobs = [j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json"))) if not j.get("merged_into")]
def done(slug): return os.path.exists(os.path.join(ROOT, "results/pitch", slug, "speeds_auto", "plausibility.json"))
def step(slug, name, args, tail=1):
    r = subprocess.run(["nice", "-n", "15", PY] + args, capture_output=True, text=True)
    with open(os.path.join(ROOT, "results/pitch", slug, "local.log"), "a") as f: f.write("== %s\n%s\n%s\n" % (name, r.stdout[-3000:], r.stderr[-2000:]))
    out = (r.stdout.strip().splitlines() or [""])[-1][:150]
    log(slug, name, "ok" if r.returncode == 0 else "ОШИБКА", out)
    return r.returncode == 0
def one(j):
    slug = j["slug"]; D = os.path.join(ROOT, "results/pitch", slug); os.makedirs(D, exist_ok=True)
    v = os.path.join(ROOT, "results/pitch/videos", slug + ".mp4")
    if not (os.path.exists(v) and video_ok(v)): return False
    if not step(slug, "кадры", [os.path.join(HERE, "extract_frames.py"), slug, "--every", "15"]): return True
    if not step(slug, "опоры", [os.path.join(HERE, "run_games.py"), W, slug]): return True
    if not step(slug, "привязка", [os.path.join(HERE, "track_game.py"), slug]): return True
    step(slug, "скорости", [os.path.join(HERE, "finish_game.py"), slug] + (["--upload"] if UPLOAD else []))
    return True
def worker():
    while True:
        with lock:
            ready = lambda j: os.path.exists(os.path.join(ROOT, "results/pitch/videos", j["slug"] + ".mp4"))
            todo = [j for j in jobs if not done(j["slug"]) and j["slug"] not in busy and ready(j)]
            if not todo:
                if "--once" in sys.argv: return
                pending = [j for j in jobs if not done(j["slug"])]
                if not pending: return
            else:
                j = todo[0]; busy.add(j["slug"])
        if not todo: time.sleep(120); continue
        try:
            if not one(j): time.sleep(60)          # видео ещё качается — вернём в очередь
        except Exception as e: log(j["slug"], "СБОЙ", str(e)[:150])
        finally:
            with lock: busy.discard(j["slug"])
        if not done(j["slug"]): time.sleep(30)
busy = set()
ts = [threading.Thread(target=worker, daemon=True) for _ in range(NW)]
[t.start() for t in ts]
while any(t.is_alive() for t in ts): time.sleep(10)
log("очередь пуста")
