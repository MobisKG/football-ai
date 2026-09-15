"""Очередь скоростей: для игр с видео и автоопорами считает speed_game.py (без загрузки).
    python speed_queue.py [--min-anchors 60] [--weights-tag pitch_lines_v2.pt] [--once]
Берёт игры, у которых есть results/pitch/videos/<slug>.mp4 (целое), auto/summary.json с нужными весами
и ещё нет speeds_auto/plausibility.json. Крутится, пока не переберёт всё (или --once)."""
import os, sys, json, glob, time, subprocess
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); from download_videos import video_ok
def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
MINA = int(opt("--min-anchors", 60)); TAG = opt("--weights-tag"); PY = sys.executable
def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)
jobs = json.load(open(os.path.join(ROOT, "results/pitch/jobs.json")))
while True:
    todo = []
    for j in jobs:
        s = j["slug"]; D = os.path.join(ROOT, "results/pitch", s)
        if os.path.exists(os.path.join(D, "speeds_auto", "plausibility.json")) or os.path.exists(os.path.join(D, "speed_auto.lock")): continue
        sm = os.path.join(D, "auto", "summary.json")
        if not os.path.exists(sm): continue
        summ = json.load(open(sm))
        if TAG and summ.get("weights") != TAG: continue
        if summ.get("accepted", 0) < MINA: continue
        v = os.path.join(ROOT, "results/pitch/videos", s + ".mp4")
        if not video_ok(v): continue
        todo.append(s)
    if not todo:
        if "--once" in sys.argv: break
        time.sleep(120); continue
    s = todo[0]; D = os.path.join(ROOT, "results/pitch", s); open(os.path.join(D, "speed_auto.lock"), "w").write(str(os.getpid()))
    log("старт", s)
    r = subprocess.run([PY, os.path.join(HERE, "speed_game.py"), s, "--anchors", "auto"], capture_output=True, text=True)
    open(os.path.join(D, "speed_auto.log"), "w").write(r.stdout + r.stderr); os.remove(os.path.join(D, "speed_auto.lock"))
    log(s, "код", r.returncode, (r.stdout.strip().splitlines() or [""])[-1][:300])
log("очередь пуста")
