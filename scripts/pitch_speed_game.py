"""Скорости и пробег одной игры по ручным опорам из pitch_marker.

    python pitch_speed_game.py <tag> <slug> [--no-upload]

Нужны: results/pitch/<slug>/manual/frame_t*.json (≥ 6 кадров с матрицей), машина <tag>
из results/pitch/pods.json (pitch_pod.py up <tag>). Делает: видео на машину (если нет),
опоры на машину, pitch_track.py по опорам, контрольные кадры между опорами, забирает
track.json, считает метры и статистику (окно 1,5 с), грузит на сайт как версию
«…-speeds» с теми же хозяевами, что в ночной загрузке (results/pitch/jobs.json).
"""
import json, os, sys, glob, subprocess, importlib.util, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
PY = "/private/tmp/claude-501/-Users-doni-Documents-football/7f0d34e7-ac71-4fb5-9950-2123175b3367/scratchpad/venv/bin/python"
tag, slug = sys.argv[1], sys.argv[2]; UPLOAD = "--no-upload" not in sys.argv
D = os.path.join(ROOT, "results/pitch", slug); man = sorted(glob.glob(os.path.join(D, "manual", "frame_t*.json")))
good = [f for f in man if json.load(open(f)).get("H_img2plan") and not json.load(open(f)).get("skip")]
if len(good) < 6: sys.exit("мало опор с матрицей: %d (нужно ≥ 6)" % len(good))
job = next(j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json"))) if j["slug"] == slug)
def log(*a): print(time.strftime("%H:%M:%S"), slug, *a, flush=True)
sys_argv = sys.argv[:]; sys.argv = ["batch_run.py", "--matches", os.path.join(ROOT, "results/batch_2026-09-14/matches.json")]
spec = importlib.util.spec_from_file_location("br", os.path.join(HERE, "batch_run.py")); br = importlib.util.module_from_spec(spec); spec.loader.exec_module(br)
sys.argv = sys_argv
rec = json.load(open(os.path.join(ROOT, "results/pitch/pods.json")))[tag]; p = br.Pod(tag); p.id = rec["id"]
r = br.rp("GET", "/pods/" + p.id); p.ip = r["publicIp"]; p.port = int(r["portMappings"]["22"])
rc, out = p.ssh("ls /root/fb/out/%s.mp4 2>/dev/null && echo HAVE" % slug, timeout=60)
if "HAVE" not in out:
    log("видео на машину"); r = subprocess.run([sys.executable, os.path.join(HERE, "pitch_pod.py"), "video", tag, slug, job["yt"]], capture_output=True, text=True)
    if r.returncode != 0: sys.exit("видео не скачалось: " + r.stdout[-200:])
p.ssh("mkdir -p /root/fb/manual_%s" % slug); p.scp(good, p.remote("/root/fb/manual_%s/" % slug))
p.scp([os.path.join(HERE, "pitch_track.py"), os.path.join(HERE, "pitch_check.py"), os.path.join(ROOT, "docs/pitch.json")], p.remote("/root/fb/"))
rc, info = p.ssh("cd /root/fb && python -c \"import cv2;c=cv2.VideoCapture('out/%s.mp4');print(c.get(5), c.get(7)/c.get(5))\"" % slug, timeout=120)
src_fps, dur = [float(x) for x in info.strip().splitlines()[-1].split()]; step = max(1, round(src_fps / job["pos_fps"]))
anch = " ".join("--anchor manual_%s/%s" % (slug, os.path.basename(f)) for f in good); ref = os.path.basename(good[0]); ref_sec = json.load(open(good[0]))["frame_sec"]
secs = sorted(json.load(open(f))["frame_sec"] for f in good); mids = [int((a + b) / 2) for a, b in zip(secs, secs[1:])][:8] or [int(dur / 2)]
log("опор %d, шаг %d, протягивание (~%d мин)" % (len(good), step, dur / 60 * 0.6))
cmd = ("cd /root/fb && nice -n 5 python pitch_track.py out/{s}.mp4 manual_{s}/{ref} out/track_{s}.json --step {st} --ref-sec {rs} --no-ground-mask {a} > out/track_{s}.log 2>&1; "
       "tail -n 1 out/track_{s}.log; rm -rf check_{s}; python pitch_check.py out/{s}.mp4 out/track_{s}.json check_{s} {m} >/dev/null 2>&1; tar cf check_{s}.tar check_{s}").format(
       s=slug, ref=ref, st=step, rs=ref_sec, a=anch, m=" ".join(map(str, mids)))
rc, out = p.ssh(cmd, timeout=5400)
if "готово" not in out: sys.exit("протягивание не удалось: " + out[-300:])
log(out.strip().splitlines()[0][:160])
p.scp(p.remote("/root/fb/out/track_%s.json" % slug), os.path.join(D, "track_manual.json"))
p.scp(p.remote("/root/fb/check_%s.tar" % slug), os.path.join(D, "check.tar")); subprocess.run(["tar", "xf", os.path.join(D, "check.tar"), "-C", D], check=True); os.remove(os.path.join(D, "check.tar"))
r = subprocess.run([sys.executable, os.path.join(HERE, "pitch_metres.py"), slug, os.path.join(D, "track_manual.json"), "--out-sub", "speeds", "--win", "1.5"], capture_output=True, text=True)
print(r.stdout[-900:]); 
if r.returncode != 0: sys.exit("метры/статистика: " + r.stderr[-300:])
if not UPLOAD or not job.get("home_colour") or not job.get("match_guid"): log("без загрузки (нет цвета хозяев или match_guid)"); sys.exit(0)
if job.get("merged_into"): log("часть сдвоенной игры — грузится объединённо, пропускаю загрузку"); sys.exit(0)
args = [PY, os.path.join(HERE, "upload_analytics.py"), "stats.json", "--match-guid", job["match_guid"], "--home-colour", job["home_colour"],
        "--video-url", "https://www.youtube.com/watch?v=" + job["yt"], "--version", "2026-09-15-speeds"]
if job.get("scoreboard"):
    args += ["--scoreboard", os.path.join(ROOT, "results/batch_2026-09-14", slug, "scoreboard.json")] + (["--home-is-top"] if job.get("home_is_top") else [])
r = subprocess.run(args, cwd=os.path.join(D, "speeds"), capture_output=True, text=True); log("загрузка:", (r.stdout or r.stderr)[:120].strip())
