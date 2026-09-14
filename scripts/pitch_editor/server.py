"""Редактор привязки к плану поля: локальный сервер для ручной правки на глаз.

Кадры и текущие матрицы берутся из results/pitch_calib/track_clip5.json,
ручные правки пишутся в results/pitch_calib/manual/ в том же формате, что и
опоры pitch_anchor.py, — их можно сразу отдать pitch_track.py через --anchor.

    python server.py [порт]
"""
import json, os, sys, glob
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import numpy as np, cv2

ROOT = "/Users/doni/Documents/football"
VIDEO = os.path.expanduser("~/Downloads/football/clip5_qE8fVwIRQxA.mp4")
TRACK = ROOT + "/results/pitch_calib/track_clip5.json"
MAN = ROOT + "/results/pitch_calib/manual"
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765

T = json.load(open(TRACK)); BYI = {f["i"]: f for f in T["frames"]}
PITCH = json.load(open(ROOT + "/docs/pitch.json"))
cap = cv2.VideoCapture(VIDEO); FPS = cap.get(cv2.CAP_PROP_FPS)
SUGGEST = [2, 30, 55, 70, 100, 135, 150, 170, 200, 229.5, 250, 285]

def frame_index(t): return int(round(t * FPS)) // T["step"] * T["step"]

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
                             "duration": len(T["frames"]) * T["step"] / FPS}).encode(), "application/json")
        if u.path == "/api/frame":
            i = frame_index(float(q["t"][0])); i = min(i, max(BYI))
            f = BYI[i]; man = manual_for(i)
            return self.send(200, json.dumps({"i": i, "t": i / FPS, "H_img2plan": (man or f)["H_img2plan"] if not man else man["H_img2plan"],
                             "auto_H": f["H_img2plan"], "src": "manual" if man else f["src"],
                             "points": (man or {}).get("points", [])}).encode(), "application/json")
        if u.path == "/api/image":
            i = int(q["i"][0]); cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, fr = cap.read()
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
