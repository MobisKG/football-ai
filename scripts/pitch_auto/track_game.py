"""Протягивание привязки по видео игры на принятых автоопорах (для машины и ноутбука).
    python track_game.py <slug> [--video путь] [--step N]
Пишет results/pitch/<slug>/track_auto.json рядом с опорами."""
import os, sys, json, glob, subprocess, time
import numpy as np, cv2
HERE = os.path.dirname(os.path.abspath(__file__)); SCR = os.path.dirname(HERE); ROOT = os.path.dirname(SCR)
def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
slug = sys.argv[1]; D = os.path.join(ROOT, "results/pitch", slug)
video = opt("--video") or os.path.join(ROOT, "results/pitch/videos", slug + ".mp4")
if not os.path.exists(video): video = os.path.join(ROOT, "videos", slug + ".mp4")
job = next(j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json"))) if j["slug"] == slug)
good = [f for f in sorted(glob.glob(os.path.join(D, "auto", "frame_t*.json"))) if (json.load(open(f)).get("ok") and json.load(open(f)).get("H_img2plan"))]
if len(good) < 6: sys.exit("мало принятых опор: %d" % len(good))
# --holdout k: каждая k-я принятая опора не участвует в привязке, по ним потом меряется ошибка
HOLD = int(opt("--holdout", 0)); held = []
if HOLD > 1:
    held = good[1::HOLD]; good = [f for f in good if f not in held]
    json.dump([json.load(open(f))["frame_sec"] for f in held], open(os.path.join(D, "heldout.json"), "w"))
cap = cv2.VideoCapture(video); fps = cap.get(5); n = cap.get(7); w = cap.get(3); cap.release()
step = int(opt("--step", max(1, round(fps / job["pos_fps"])))); ascale = w / 1920.0
secs = sorted(json.load(open(f))["frame_sec"] for f in good); ref = good[len(good) // 2]; ref_sec = json.load(open(ref))["frame_sec"]
out = os.path.join(D, "track_auto.json")
cmd = [sys.executable, os.path.join(SCR, "pitch_track.py"), video, ref, out, "--step", str(step), "--ref-sec", str(ref_sec),
       "--no-ground-mask", "--anchor-scale", str(ascale), "--near-anchors", "40", "--blend"]
for f in good: cmd += ["--anchor", f]
t0 = time.time(); r = subprocess.run(cmd, capture_output=True, text=True)
open(os.path.join(D, "track_auto.log"), "w").write(r.stdout + r.stderr)
print(slug, "опор", len(good), "шаг", step, "->", r.stdout.strip().splitlines()[-1][:160] if r.stdout.strip() else (r.stderr[-200:]), "%.0f мин" % ((time.time() - t0) / 60), flush=True)
sys.exit(0 if "готово" in r.stdout else 1)
