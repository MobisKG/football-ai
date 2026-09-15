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
ROOT_MODE = "--root" in sys.argv          # python server.py --root results/pitch : все игры, выбор на странице
PORT = int(opt("--port", 8770))
P = json.load(open(os.path.join(ROOT, "docs", "pitch.json")))
def plan_of(key):
    pl = P["pitches"].get(key) or P["pitches"]["lower"]
    return {"width": pl["width"], "length": pl["length"], "half": P["half_length"], "goal": P["goal"]["width"],
            "box_w": P["penalty_area"]["width"], "box_d": P["penalty_area"]["depth"], "key": key}
FIELD_PITCH = {"Полей №3": "field3", "Полей №1": "lower", "Полей №2": "lower"}
GAMES = {}
def scan_games():
    """папки с кадрами — заново при каждом запросе списка: кадры новых игр приходят по ходу работы"""
    if not ROOT_MODE: return
    jobs = {j["slug"]: j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json")))}
    for slug in sorted(os.listdir(PROOT)):
        if slug in GAMES: continue
        fr = os.path.join(PROOT, slug, "frames_%s_full" % slug)
        if not os.path.isdir(fr):
            alt = glob.glob(os.path.join(PROOT, slug, "full_*")); fr = alt[0] if alt and os.path.isdir(alt[0]) else None
        if not fr: continue
        j = jobs.get(slug, {}); GAMES[slug] = {"frames": fr, "out": os.path.join(PROOT, slug, "manual"), "pitch": opt("--pitch") or FIELD_PITCH.get(j.get("field"), "lower"), "field": j.get("field"), "title": slug}
if ROOT_MODE:
    PROOT = os.path.abspath(opt("--root")); scan_games()
    if not GAMES: sys.exit("в " + PROOT + " нет папок с кадрами")
else:
    FRAMES = os.path.abspath(os.path.expanduser(sys.argv[1])); OUT = os.path.abspath(os.path.expanduser(sys.argv[2]))
    slug = os.path.basename(os.path.dirname(FRAMES.rstrip("/"))) or FRAMES
    GAMES[slug] = {"frames": FRAMES, "out": OUT, "pitch": opt("--pitch", "lower"), "field": None, "title": slug}
def sec_of(path):
    m = re.search(r"(\d+)", os.path.basename(path)); return int(m.group(1)) if m else 0
class Game:
    def __init__(self, slug):
        g = GAMES[slug]; self.slug = slug; self.out = g["out"]; os.makedirs(self.out, exist_ok=True)
        self.files = sorted(glob.glob(os.path.join(g["frames"], "*.jpg")) + glob.glob(os.path.join(g["frames"], "*.png")), key=sec_of)
        self.secs = [sec_of(f) for f in self.files]; self.plan = plan_of(g["pitch"]); self.field = g["field"]
    def out_path(self, sec): return os.path.join(self.out, "frame_t%04d.json" % sec)
    def done(self):
        d = {}
        for s in self.secs:
            p = self.out_path(s)
            if os.path.exists(p):
                j = json.load(open(p)); d[s] = {"n": len(j.get("points", [])), "err": max(j.get("reproj_err_m") or [0]), "skip": j.get("skip", False)}
        return d
GAME_CACHE = {}
def game(q):
    slug = (q.get("game") or [next(iter(GAMES))])[0]
    if slug not in GAME_CACHE: GAME_CACHE[slug] = Game(slug)
    return GAME_CACHE[slug]

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
        if u.path == "/api/games":
            scan_games(); rows = []
            for slug in GAMES:
                g = game({"game": [slug]}); d = g.done(); marked = sum(1 for v in d.values() if v["n"] >= 4)
                rows.append({"slug": slug, "field": g.field, "frames": len(g.secs), "marked": marked, "pitch": g.plan["key"]})
            return self.send(200, rows)
        if u.path == "/api/meta":
            g = game(q)
            return self.send(200, {"plan": g.plan, "secs": g.secs, "done": g.done(), "game": g.slug, "field": g.field, "out": g.out})
        if u.path == "/api/frame":
            g = game(q); s = int(q["sec"][0]); p = g.out_path(s)
            d = json.load(open(p)) if os.path.exists(p) else {"points": []}
            im = cv2.imread(g.files[g.secs.index(s)]); h, w = im.shape[:2]
            return self.send(200, {"sec": s, "points": d.get("points", []), "H_img2plan": d.get("H_img2plan"), "err": d.get("reproj_err_m", []), "skip": d.get("skip", False), "w": w, "h": h})
        if u.path == "/api/image":
            g = game(q); s = int(q["sec"][0]); return self.send(200, open(g.files[g.secs.index(s)], "rb").read(), "image/jpeg")
        self.send(404, {"error": "not found"})
    def do_POST(self):
        u = urlparse(self.path); q = parse_qs(u.query); n = int(self.headers.get("Content-Length", 0)); d = json.loads(self.rfile.read(n) or b"{}")
        g = game({"game": [d["game"]]} if d.get("game") else q)
        if u.path == "/api/fit":
            H, err = fit(d.get("points", [])); return self.send(200, {"H_img2plan": H, "err": err, "degenerate": degenerate(d.get("points", []))})
        if u.path == "/api/save":
            s = int(d["sec"]); pts = d.get("points", []); H, err = fit(pts)
            out = {"frame_sec": s, "sec": s, "source": "manual", "k1": 0.0, "plan": {"width": g.plan["width"], "length": g.plan["length"]},
                   "points": pts, "H_img2plan": H, "reproj_err_m": err, "skip": bool(d.get("skip", False)), "image": g.files[g.secs.index(s)], "game": g.slug}
            if not pts and not out["skip"]:
                if os.path.exists(g.out_path(s)): os.remove(g.out_path(s))
            else:
                json.dump(out, open(g.out_path(s), "w"), ensure_ascii=False, indent=1)
            return self.send(200, {"ok": True, "H_img2plan": H, "err": err, "degenerate": degenerate(pts)})
        self.send(404, {"error": "not found"})

print("разметка: http://localhost:%d  игр %d" % (PORT, len(GAMES)), flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), Hd).serve_forever()
