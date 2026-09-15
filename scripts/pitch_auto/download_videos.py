"""Скачивает видео игр на ноутбук (720p60, только видео, avc1) в results/pitch/videos/<slug>.mp4.

    python download_videos.py [slug,slug,...]     # по умолчанию все игры из jobs.json

Порядок: сначала игры с ручными опорами. Целостность — по последнему пакету (как run_video.sh).
Повторы при пропаже сети: до 6 попыток с паузой 60 с. Лог — stdout.
"""
import os, sys, json, subprocess, time, glob
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "results/pitch/videos"); os.makedirs(OUT, exist_ok=True)
PY = sys.executable
def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)
def video_ok(f):
    if not os.path.exists(f) or os.path.getsize(f) < 1e6: return False
    try:
        d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f], capture_output=True, text=True, timeout=60).stdout.strip())
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-read_intervals", "%d%%" % max(0, d - 60), "-show_entries", "packet=pts_time", "-of", "csv=p=0", f], capture_output=True, text=True, timeout=120)
        last = [float(x) for x in r.stdout.split() if x.replace(".", "").isdigit()]
        return bool(last) and last[-1] >= d - 10
    except Exception as e:
        log("ffprobe:", e); return False
jobs = json.load(open(os.path.join(ROOT, "results/pitch/jobs.json")))
only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
manual = {os.path.basename(os.path.dirname(os.path.dirname(f))) for f in glob.glob(os.path.join(ROOT, "results/pitch/*/manual/frame_t*.json"))}
todo = [j for j in jobs if not only or j["slug"] in only]
todo.sort(key=lambda j: (j["slug"] not in manual, j["slug"]))
for j in todo:
    f = os.path.join(OUT, j["slug"] + ".mp4")
    if video_ok(f): log(j["slug"], "уже есть"); continue
    for attempt in range(6):
        for fmt in ("298", "bv[height<=720][ext=mp4][vcodec^=avc1]", "bv[height<=720][ext=mp4]"):
            if os.path.exists(f): os.remove(f)
            r = subprocess.run([PY, "-m", "yt_dlp", "-q", "--no-warnings", "--socket-timeout", "30", "-f", fmt, "--no-part", "-o", f, "https://www.youtube.com/watch?v=" + j["yt"]], capture_output=True, text=True)
            if r.returncode == 0 and video_ok(f): break
            log(j["slug"], "формат", fmt, "не вышло:", (r.stderr or r.stdout).strip()[-160:])
        else:
            log(j["slug"], "попытка", attempt + 1, "не удалась, пауза 60 с"); time.sleep(60); continue
        log(j["slug"], "скачано %.0f МБ" % (os.path.getsize(f) / 1e6)); break
    else:
        log(j["slug"], "!!! не скачалось")
log("готово")
