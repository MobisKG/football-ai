"""Редактор привязки к плану поля: локальный сервер для ручной правки на глаз.

Две формы запуска:
  - клип 5, как раньше: кадры из видео, начальные матрицы из готового трека;
        python server.py [порт]
  - новая игра без трека: кадры из папки JPEG (имена t0065.jpg — секунда
    видео), начальная разметка — грубая заготовка, её надо поправить руками;
        python server.py 8766 --frames ~/Downloads/football/gz_real91/frames \
            --manual ../../results/gz_real91/pitch_manual --pitch upper --fps 59.076

Ручные правки пишутся в папку --manual в том же формате, что и опоры
pitch_anchor.py, — их можно сразу отдать pitch_track.py через --anchor.
"""
import json, os, sys, glob
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import numpy as np, cv2

def opt(n, d=None): return sys.argv[sys.argv.index(n) + 1] if n in sys.argv else d
ROOT = "/Users/doni/Documents/sport/football"
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8765
PITCH = json.load(open(ROOT + "/docs/pitch.json"))
FRAMES = opt("--frames")

if FRAMES:
    FRAMES = os.path.expanduser(FRAMES)
    MAN = os.path.abspath(os.path.expanduser(opt("--manual", FRAMES + "/../pitch_manual")))
    FPS = float(opt("--fps", 59.076)); STEP = 1
    pl = PITCH["pitches"][opt("--pitch", "upper")]
    PLAN = {"width": pl["width"], "length": pl["length"]}
    TIMES = sorted(int(os.path.basename(p)[1:5]) for p in glob.glob(FRAMES + "/t*.jpg"))
    SUGGEST = TIMES
    img0 = cv2.imread("%s/t%04d.jpg" % (FRAMES, TIMES[0])); IH, IW = img0.shape[:2]
    # Грубая заготовка: поле как трапеция в нижних двух третях кадра, дальняя
    # сторона уже ближней. Точной её никто не ждёт — это стартовая точка для рук.
    src = np.float32([[0, 0], [PLAN["width"], 0], [PLAN["width"], PLAN["length"]], [0, PLAN["length"]]])
    dst = np.float32([[0.30 * IW, 0.35 * IH], [0.02 * IW, 0.95 * IH], [0.98 * IW, 0.95 * IH], [0.70 * IW, 0.35 * IH]])
    GUESS = cv2.getPerspectiveTransform(dst, src).flatten().tolist()
    T = {"step": STEP, "plan": PLAN, "frames": []}
    BYI = {}
    cap = None
else:
    VIDEO = os.path.expanduser("~/Downloads/football/clip5_qE8fVwIRQxA.mp4")
    TRACK = ROOT + "/results/pitch_calib/track_clip5.json"
    MAN = ROOT + "/results/pitch_calib/manual"
    T = json.load(open(TRACK)); BYI = {f["i"]: f for f in T["frames"]}
    cap = cv2.VideoCapture(VIDEO); FPS = cap.get(cv2.CAP_PROP_FPS)
    SUGGEST = [2, 30, 55, 70, 100, 135, 150, 170, 200, 229.5, 250, 285]
os.makedirs(MAN, exist_ok=True)

def frame_index(t):
    if FRAMES:   # ближайшая вырезанная секунда
        sec = min(TIMES, key=lambda x: abs(x - t)); return int(round(sec * FPS))
    return int(round(t * FPS)) // T["step"] * T["step"]

def manual_for(i):
    p = "%s/frame_%05d.json" % (MAN, i)
    return json.load(open(p)) if os.path.exists(p) else None

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self.send(200, open(HERE + "/index.html", "rb").read(), "text/html; charset=utf-8")
        if u.path == "/api/meta":
            saved = sorted(int(os.path.basename(p)[6:11]) for p in glob.glob(MAN + "/frame_*.json"))
            settings = json.load(open(MAN + "/settings.json")) if os.path.exists(MAN + "/settings.json") else {}
            return self.send(200, json.dumps({"fps": FPS, "step": T["step"], "suggest": SUGGEST, "pitch": PITCH,
                             "plan": T["plan"], "saved": saved, "settings": settings,
                             "duration": (TIMES[-1] if FRAMES else len(T["frames"]) * T["step"] / FPS)}).encode(), "application/json")
        if u.path == "/api/frame":
            i = frame_index(float(q["t"][0]))
            if not FRAMES: i = min(i, max(BYI))
            f = {"H_img2plan": GUESS, "src": "guess"} if FRAMES else BYI[i]; man = manual_for(i)
            return self.send(200, json.dumps({"i": i, "t": i / FPS, "H_img2plan": (man or f)["H_img2plan"] if not man else man["H_img2plan"],
                             "auto_H": f["H_img2plan"], "src": "manual" if man else f["src"],
                             "points": (man or {}).get("points", [])}).encode(), "application/json")
        if u.path == "/api/image":
            i = int(q["i"][0])
            if FRAMES:
                sec = min(TIMES, key=lambda x: abs(x * FPS - i)); fr = cv2.imread("%s/t%04d.jpg" % (FRAMES, sec))
            else:
                cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, fr = cap.read()
            ok, buf = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 90])
            return self.send(200, buf.tobytes(), "image/jpeg")
        self.send(404, b"not found", "text/plain")
    def do_POST(self):
        u = urlparse(self.path); n = int(self.headers.get("Content-Length", 0)); d = json.loads(self.rfile.read(n))
        if u.path == "/api/save":
            i = int(d["i"])
            out = {"frame_index": i, "sec": i / FPS, "k1": 0.0, "source": "manual",
                   "H_img2plan": d["H_img2plan"], "plan": T["plan"], "points": d.get("points", [])}
            json.dump(out, open("%s/frame_%05d.json" % (MAN, i), "w"), ensure_ascii=False, indent=1)
            return self.send(200, b'{"ok":true}', "application/json")
        if u.path == "/api/delete":
            p = "%s/frame_%05d.json" % (MAN, int(d["i"]))
            if os.path.exists(p): os.remove(p)
            return self.send(200, b'{"ok":true}', "application/json")
        if u.path == "/api/settings":
            json.dump(d, open(MAN + "/settings.json", "w"), ensure_ascii=False, indent=1)
            return self.send(200, b'{"ok":true}', "application/json")
        self.send(404, b"not found", "text/plain")

print("редактор: http://localhost:%d" % PORT, flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
