"""Кадры полного размера для ручной разметки (pitch_marker) по всем видео прогона.

    python pitch_frames_all.py [--every 15] [--only slug,slug]

Поднимает одну машину RunPod (лёгкую, без моделей), по очереди качает видео, режет
кадры каждые N секунд в полном размере и складывает в results/pitch/<slug>/frames_<slug>/.
Машина удаляется в конце (и при ошибке). Готовые игры пропускаются.
Ориентир: ~4 мин на игру, ~1,5 ч и ~1 $ на все 24.
"""
import json, os, sys, subprocess, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
opt = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
EVERY = opt("--every", "15"); ONLY = set((opt("--only", "") or "").split(",")) - {""}
jobs = json.load(open(os.path.join(ROOT, "results/pitch/jobs.json")))
todo = [j for j in jobs if (not ONLY or j["slug"] in ONLY) and not os.path.isdir(os.path.join(ROOT, "results/pitch", j["slug"], "frames_" + j["slug"] + "_full"))]
print("к нарезке:", [j["slug"] for j in todo], flush=True)
if not todo: sys.exit(0)
PP = [sys.executable, os.path.join(HERE, "pitch_pod.py")]
def run(*a, timeout=3600):
    r = subprocess.run(PP + list(a), capture_output=True, text=True, timeout=timeout); print(r.stdout[-300:].strip(), flush=True); return r.returncode == 0
tag = "pf1"
if not run("up", tag, timeout=1800): sys.exit("машина не поднялась")
try:
    for j in todo:
        s = j["slug"]; print(time.strftime("%H:%M"), s, flush=True)
        if not run("video", tag, s, j["yt"], timeout=2400): print("  !!! видео не скачалось, пропускаю", flush=True); continue
        # frames <tag> <slug> <every> --full  → results/pitch/<slug>/frames_<slug>/ ; переименовываем в *_full
        if run("frames", tag, s, EVERY, "--full", timeout=2400):
            d = os.path.join(ROOT, "results/pitch", s)
            os.rename(os.path.join(d, "frames_" + s), os.path.join(d, "frames_" + s + "_full"))
        run("ssh", tag, "rm -f /root/fb/out/%s.mp4; rm -rf /root/fb/frames_%s /root/fb/frames_%s.tar" % (s, s, s), "120")
finally:
    run("down", tag, timeout=300)
    print("машина удалена", flush=True)
