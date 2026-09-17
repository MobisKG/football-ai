"""Внешность треков помимо формы: цвет обуви/гетр (низ рамки) и волос (верх рамки).
    python appearance.py <pos_final.json> <video.mp4> <out.json> [--per-track 4] [--min-dur 1.5]
Кадры читаются подряд, нужные забираются — без поиска по видео."""
import json, sys, cv2, numpy as np
from collections import defaultdict
def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
pos, video, out = sys.argv[1:4]; PER = int(opt("--per-track", 4)); MIN = float(opt("--min-dur", 1.5))
d = json.load(open(pos)); fps = d["fps"]; F = d["frames"]
seen = defaultdict(list)
for i, f in enumerate(F):
    for t, b in (f.get("boxes") or {}).items():
        if b[3] - b[1] >= 60: seen[int(t)].append(i)
want = defaultdict(list)   # индекс кадра разбора -> треки
for t, idx in seen.items():
    if (idx[-1] - idx[0]) / fps < MIN: continue
    for k in np.linspace(0, len(idx) - 1, PER + 2)[1:-1].astype(int): want[idx[k]].append(t)
cap = cv2.VideoCapture(video); vfps = cap.get(5)
need = sorted(want); vidx = {int(round(i / fps * vfps)): i for i in need}
def hist(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256]).flatten()
    return (h / max(1, h.sum())).tolist()
feats = defaultdict(lambda: {"shoe": [], "head": [], "h_px": []}); j = 0; last = max(vidx) if vidx else 0
while j <= last:
    if j not in vidx:
        if not cap.grab(): break
        j += 1; continue
    ok, im = cap.read(); j += 1
    if not ok: break
    i = vidx[j - 1]
    for t in want[i]:
        x0, y0, x1, y1 = map(int, F[i]["boxes"][str(t)]); h = y1 - y0
        if x1 - x0 < 8: continue
        feats[t]["shoe"].append(hist(im[max(0, y1 - int(.18 * h)):y1, x0:x1]))
        feats[t]["head"].append(hist(im[y0:y0 + int(.14 * h), x0:x1]))
        feats[t]["h_px"].append(h)
    if len(feats) and j % 20000 < 2: print("кадр", j, "треков", len(feats), flush=True)
json.dump({str(t): {k: np.mean(v, axis=0).tolist() if k != "h_px" else v for k, v in f.items()} for t, f in feats.items() if f["shoe"]}, open(out, "w"))
print("готово, треков", len(feats))
