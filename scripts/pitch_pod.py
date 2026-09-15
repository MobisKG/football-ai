"""Машина RunPod для разметки поля: без Qwen и моделей, только cv2, yt-dlp, ffmpeg.

    python pitch_pod.py up <tag>                         — создать машину, лёгкая установка
    python pitch_pod.py video <tag> <slug> <youtube_id>  — скачать видео (с проверкой целостности)
    python pitch_pod.py frames <tag> <slug> [шаг_с]      — кадры каждые N с (640 px) → results/pitch/<slug>/frames/
    python pitch_pod.py ssh <tag> '<команда>'
    python pitch_pod.py down <tag>

Состояние машин — results/pitch/pods.json. Использует Pod и rp из batch_run.py.
"""
import json, os, sys, subprocess, importlib.util, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
PITCH = os.path.join(ROOT, "results", "pitch"); os.makedirs(PITCH, exist_ok=True)
sys_argv = sys.argv[:]
sys.argv = ["batch_run.py", "--matches", os.path.join(ROOT, "results/batch_2026-09-14/matches.json")]
spec = importlib.util.spec_from_file_location("br", os.path.join(HERE, "batch_run.py")); br = importlib.util.module_from_spec(spec); spec.loader.exec_module(br)
sys.argv = sys_argv
PODS = os.path.join(PITCH, "pods.json")
pods = json.load(open(PODS)) if os.path.exists(PODS) else {}
def save(): json.dump(pods, open(PODS, "w"), indent=1)
def get(tag):
    rec = pods[tag]; p = br.Pod(tag); p.id, p.gpu, p.cost, p.t0 = rec["id"], rec["gpu"], rec["cost"], rec["t0"]
    r = br.rp("GET", "/pods/" + p.id); pm = r.get("portMappings") or {}
    p.ip, p.port = r.get("publicIp"), int(pm["22"]) if pm.get("22") else None
    return p
cmd, tag = sys.argv[1], sys.argv[2]
if cmd == "up":
    p = br.Pod(tag)
    if not p.create(): sys.exit("машину получить не удалось")
    pods[tag] = {"id": p.id, "gpu": p.gpu, "cost": p.cost, "t0": p.t0}; save()
    setup = ("mkdir -p /root/fb/out && cd /root/fb && pip install -q --root-user-action=ignore opencv-python-headless yt-dlp numpy 2>&1 | grep -v -i notice | tail -2; "
             "command -v ffmpeg >/dev/null || (curl -sL -o /tmp/ff.tar.xz https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz && mkdir -p /tmp/ff && tar xJf /tmp/ff.tar.xz -C /tmp/ff --strip-components=1 && install -m755 /tmp/ff/bin/ffmpeg /tmp/ff/bin/ffprobe /usr/local/bin/); "
             "grep -q OPENBLAS_NUM_THREADS /root/.bashrc || sed -i '1i export OPENBLAS_NUM_THREADS=16 OMP_NUM_THREADS=16' /root/.bashrc; ffprobe -version | head -1; python -c 'import cv2;print(\"cv2\", cv2.__version__)'")
    rc, out = p.ssh(setup, timeout=900); print(out[-400:])
    p.scp([os.path.join(HERE, f) for f in ("run_video.sh", "pitch_track.py", "log_saver.sh", "pod_watchdog.sh")], p.remote("/root/fb/"))
    print("UP", tag, p.id, p.gpu, p.ip, p.port)
elif cmd == "video":
    slug, yt = sys.argv[3], sys.argv[4]; p = get(tag)
    # только скачивание и проверка целостности из run_video.sh: функция video_ok и цикл клиентов
    script = open(os.path.join(HERE, "run_video.sh")).read()
    dl = script[script.index("video_ok() {"):script.index('log "частота видео')]
    body = 'cd /root/fb; YT=%s; NAME=%s; V=out/$NAME.mp4; STEP=4; mkdir -p out; log() { echo "[$(date +%%H:%%M:%%S)] $*"; }\n%s\necho VIDEO_DONE' % (yt, slug, dl)
    open("/tmp/pitch_dl.sh", "w").write(body)
    p.scp("/tmp/pitch_dl.sh", p.remote("/root/fb/pitch_dl_%s.sh" % slug))
    rc, out = p.ssh("bash /root/fb/pitch_dl_%s.sh" % slug, timeout=2400); print(out[-600:])
    if "VIDEO_DONE" not in out or "FAILED" in out or "не скачалось" in out: sys.exit(1)
elif cmd == "frames":
    slug = sys.argv[3]; every = float(sys.argv[4]) if len(sys.argv) > 4 else 5.0; p = get(tag)
    py = ("import cv2,os;v='out/%s.mp4';o='frames_%s';os.makedirs(o,exist_ok=True);c=cv2.VideoCapture(v);f=c.get(5);n=c.get(7);t=0.0\n"
          "while t<n/f:\n c.set(0,t*1000);ok,fr=c.read()\n if ok: cv2.imwrite('%%s/f_%%05d.jpg'%%(o,int(t)),cv2.resize(fr,(640,360)),[cv2.IMWRITE_JPEG_QUALITY,80])\n t+=%s\n"
          "print('кадров',len(os.listdir(o)))") % (slug, slug, every)
    rc, out = p.ssh("cd /root/fb && nice -n 10 python -c \"%s\" && tar cf frames_%s.tar frames_%s" % (py.replace('"', '\\"'), slug, slug), timeout=2400); print(out[-300:])
    d = os.path.join(PITCH, slug); os.makedirs(d, exist_ok=True)
    p.scp(p.remote("/root/fb/frames_%s.tar" % slug), os.path.join(d, "frames.tar"))
    subprocess.run(["tar", "xf", os.path.join(d, "frames.tar"), "-C", d], check=True); os.remove(os.path.join(d, "frames.tar"))
    print("FRAMES", len(os.listdir(os.path.join(d, "frames_" + slug))))
elif cmd == "ssh":
    p = get(tag); rc, out = p.ssh(sys.argv[3], timeout=int(sys.argv[4]) if len(sys.argv) > 4 else 3600); print(out)
elif cmd == "down":
    p = get(tag); p.delete(); pods.pop(tag, None); save(); print("DOWN", tag)
