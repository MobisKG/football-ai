"""Вырезки для чтения номеров, вторая версия: больше кадров и со спины.

Первая версия брала три самых крупных кадра трека. На проверке синего «№2»
(треки 868, 1616, 1677) все кадры оказались лицом к камере: номера не видно,
а модель всё равно назвала цифру с уверенностью 0,9. Номер на спине, поэтому
нужны кадры, где игрок уходит от камеры (в кадре он движется вверх), и их
нужно несколько, чтобы голосовать.

Берём до PER_TRACK кадров на трек: только крупные (оценка роста ≥ MIN_H),
не ближе 20 кадров друг к другу, с приоритетом «спиной к камере»
(смещение вверх за следующие 0,5 с) и по росту. Рамок в позициях нет, рост
оценивается по высоте точки в кадре, как и раньше.

    python extract_crops2.py pos.json video.mp4 crops/
"""
import json, os, sys
import cv2

pos_path, video, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
PER_TRACK, MIN_H, GAP = 8, 170, 20

d = json.load(open(pos_path)); frames = d["frames"]; fps = d["fps"]
team_of = {int(k): v for k, v in d["team_of"].items()}
skip = set(d.get("sideline") or [])
H = 1080.0
height = lambda y: 140 + 380 * (y / H) ** 2

seen = {}
for i, f in enumerate(frames):
    for tid, x, y in f["players"]:
        seen.setdefault(int(tid), []).append((i, x, y))

picks = {}
for tid, lst in seen.items():
    if tid in skip or team_of.get(tid, -1) not in (0, 1): continue
    byi = {i: (x, y) for i, x, y in lst}
    cand = []
    for i, x, y in lst:
        h = height(y)
        if h < MIN_H: continue
        j = i + int(0.5 * fps)
        back = byi[j][1] < y - 8 if j in byi else False   # уходит вверх по кадру = спиной
        cand.append((h * (1.6 if back else 1.0), i, x, y, back))
    cand.sort(reverse=True)
    chosen = []
    for s, i, x, y, back in cand:
        if all(abs(i - j) > GAP for _, j, _, _, _ in chosen):
            chosen.append((s, i, x, y, back))
        if len(chosen) == PER_TRACK: break
    if chosen: picks[tid] = chosen

want = {}
for tid, ch in picks.items():
    for s, i, x, y, back in ch:
        want.setdefault(i, []).append((tid, x, y))

os.makedirs(out_dir, exist_ok=True)
cap = cv2.VideoCapture(video)
W = int(cap.get(3)); stride = int(round(cap.get(5) / fps)) or 1
made = idx = 0
while True:
    ok, frame = cap.read()
    if not ok: break
    i = idx // stride
    if idx % stride == 0 and i in want:
        for tid, x, y in want[i]:
            bh = int(height(y)); bw = int(bh * 0.62)
            x1 = max(0, min(W - bw, int(x) - bw // 2)); y1 = max(0, int(y) - bh)
            crop = frame[y1:int(y), x1:x1 + bw]
            if crop.size:
                crop = cv2.resize(crop, (max(1, int(crop.shape[1] * 300 / crop.shape[0])), 300), interpolation=cv2.INTER_LANCZOS4)
                cv2.imwrite(os.path.join(out_dir, "t%d_%d.jpg" % (tid, i)), crop, [cv2.IMWRITE_JPEG_QUALITY, 90]); made += 1
    idx += 1
cap.release()
meta = {str(t): {"team": team_of.get(t, -1), "frames_seen": len(seen[t]),
                 "shots": ["t%d_%d.jpg" % (t, i) for _, i, _, _, _ in ch],
                 "back": [bool(b) for _, _, _, _, b in ch]} for t, ch in picks.items()}
json.dump(meta, open(os.path.join(out_dir, "tracks.json"), "w"), ensure_ascii=False)
nb = sum(sum(m["back"]) for m in meta.values())
print("треков %d, снимков %d, из них спиной %d" % (len(meta), made, nb))
