"""Разметка поля вручную: быстрый редактор опор для кадров одной игры.

    python server.py <папка_с_кадрами> <папка_вывода> [--pitch lower|upper] [--port 8770]

Кадры — JPEG полного размера с именами t0480.jpg (секунда видео) или f_00480.jpg.
Вывод — по файлу на кадр: frame_t0480.json {frame_sec, H_img2plan, plan, points,
reproj_err_m}; их читает pitch_track.py (--anchor/--ext-anchor) и pitch_anchor_img.py.

Логика страницы: выбрать точку на схеме поля → кликнуть её на кадре. Матрица
считается здесь (cv2), страница сразу рисует всю разметку поверх кадра.
"""
import json, os, sys, glob, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import numpy as np, cv2

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
def opt(n, d=None): return sys.argv[sys.argv.index(n) + 1] if n in sys.argv else d
FRAMES = os.path.abspath(os.path.expanduser(sys.argv[1])); OUT = os.path.abspath(os.path.expanduser(sys.argv[2])); os.makedirs(OUT, exist_ok=True)
PORT = int(opt("--port", 8770))
P = json.load(open(os.path.join(ROOT, "docs", "pitch.json")))
pl = P["pitches"][opt("--pitch", "lower")]
PLAN = {"width": pl["width"], "length": pl["length"], "half": P["half_length"], "goal": P["goal"]["width"],
        "box_w": P["penalty_area"]["width"], "box_d": P["penalty_area"]["depth"]}

def sec_of(path):
    m = re.search(r"(\d+)", os.path.basename(path)); return int(m.group(1)) if m else 0
FILES = sorted(glob.glob(os.path.join(FRAMES, "*.jpg")) + glob.glob(os.path.join(FRAMES, "*.png")), key=sec_of)
if not FILES: sys.exit("в папке нет кадров: " + FRAMES)
SECS = [sec_of(f) for f in FILES]
def out_path(sec): return os.path.join(OUT, "frame_t%04d.json" % sec)

def degenerate(points):
    """точки почти на одной линии плана: матрица по ним не определяется"""
    if len(points) < 4: return False
    d = np.array([p["plan"] for p in points], float)
    hull = cv2.convexHull(d.astype(np.float32).reshape(-1, 1, 2))
    return cv2.contourArea(hull) < 4.0          # м²: 4 угла штрафной дают ~58

def fit(points):
    """однородная матрица кадр->план по точкам; ошибка каждой точки на плане, м"""
    if len(points) < 4 or degenerate(points): return None, []
    src = np.array([p["img"] for p in points], float); dst = np.array([p["plan"] for p in points], float)
    H, _ = cv2.findHomography(src, dst, 0)
    if H is None: return None, []
    back = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
    err = [float(np.hypot(*(b - d))) for b, d in zip(back, dst)]
    return (H / H[2, 2]).tolist(), err

class Hd(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)): body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path in ("/", "/index.html"): return self.send(200, open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")
        if u.path == "/api/meta":
            done = {}
            for s in SECS:
                p = out_path(s)
                if os.path.exists(p):
                    d = json.load(open(p)); done[s] = {"n": len(d.get("points", [])), "err": max(d.get("reproj_err_m") or [0]), "skip": d.get("skip", False)}
            return self.send(200, {"plan": PLAN, "secs": SECS, "done": done, "game": os.path.basename(os.path.dirname(FRAMES.rstrip("/"))) or FRAMES, "out": OUT})
        if u.path == "/api/frame":
            s = int(q["sec"][0]); p = out_path(s)
            d = json.load(open(p)) if os.path.exists(p) else {"points": []}
            im = cv2.imread(FILES[SECS.index(s)]); h, w = im.shape[:2]
            return self.send(200, {"sec": s, "points": d.get("points", []), "H_img2plan": d.get("H_img2plan"), "err": d.get("reproj_err_m", []), "skip": d.get("skip", False), "w": w, "h": h})
        if u.path == "/api/image":
            s = int(q["sec"][0]); return self.send(200, open(FILES[SECS.index(s)], "rb").read(), "image/jpeg")
        self.send(404, {"error": "not found"})
    def do_POST(self):
        u = urlparse(self.path); n = int(self.headers.get("Content-Length", 0)); d = json.loads(self.rfile.read(n) or b"{}")
        if u.path == "/api/fit":
            H, err = fit(d.get("points", [])); return self.send(200, {"H_img2plan": H, "err": err, "degenerate": degenerate(d.get("points", []))})
        if u.path == "/api/save":
            s = int(d["sec"]); pts = d.get("points", []); H, err = fit(pts)
            out = {"frame_sec": s, "sec": s, "source": "manual", "k1": 0.0, "plan": {"width": PLAN["width"], "length": PLAN["length"]},
                   "points": pts, "H_img2plan": H, "reproj_err_m": err, "skip": bool(d.get("skip", False)), "image": FILES[SECS.index(s)]}
            if not pts and not out["skip"]:
                if os.path.exists(out_path(s)): os.remove(out_path(s))
            else:
                json.dump(out, open(out_path(s), "w"), ensure_ascii=False, indent=1)
            return self.send(200, {"ok": True, "H_img2plan": H, "err": err, "degenerate": degenerate(pts)})
        self.send(404, {"error": "not found"})

print("разметка: http://localhost:%d  кадров %d  вывод %s" % (PORT, len(FILES), OUT), flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), Hd).serve_forever()
