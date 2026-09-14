"""Вырезает по нескольку фотографий на трек, чтобы игрока можно было опознать.

Зачем: номера треков в отчёте — это не номера игроков. Чтобы понять, кто есть
кто, нужен снимок. Берём кадры, где игрок ниже всего в кадре, то есть ближе
к камере: там он крупнее и номер на спине читается. На проверке трека #13918
номер 6 был виден невооружённым глазом.

Рамок в файле позиций нет, есть только точка под ногами, поэтому размер фигуры
оцениваем по высоте точки в кадре: у дальней кромки игрок около 140 пикселей,
у камеры примерно 520.

    python extract_crops.py pos.json video.mp4 crops/ [макс_треков]
"""
import json, os, sys
import cv2

pos_path, video, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
limit_tracks = int(sys.argv[4]) if len(sys.argv) > 4 else 0
PER_TRACK = 3

d = json.load(open(pos_path))
frames = d["frames"]
team_of = {int(k): v for k, v in d["team_of"].items()}

seen = {}
for i, f in enumerate(frames):
    for tid, x, y in f["players"]:
        seen.setdefault(int(tid), []).append((y, i, x))

# для каждого трека — кадры, где он крупнее всего, но не подряд идущие
picks = {}
for tid, lst in seen.items():
    lst.sort(key=lambda t: -t[0])
    chosen = []
    for y, i, x in lst:
        if all(abs(i - j) > 20 for _, j, _ in chosen):
            chosen.append((y, i, x))
        if len(chosen) == PER_TRACK:
            break
    picks[tid] = chosen

order = sorted(picks, key=lambda t: -len(seen[t]))
if limit_tracks:
    order = order[:limit_tracks]
want = {}
for tid in order:
    for y, i, x in picks[tid]:
        want.setdefault(i, []).append((tid, x, y))

os.makedirs(out_dir, exist_ok=True)
cap = cv2.VideoCapture(video)
W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
stride = int(round(cv2.VideoCapture(video).get(cv2.CAP_PROP_FPS) / d["fps"])) or 1

made = 0
idx = 0
while True:
    ok, frame = cap.read()
    if not ok:
        break
    i = idx // stride if stride else idx
    if idx % stride == 0 and i in want:
        for tid, x, y in want[i]:
            bh = int(140 + 380 * (y / H) ** 2)
            bw = int(bh * 0.62)
            x1 = max(0, min(W - bw, int(x) - bw // 2))
            y1 = max(0, int(y) - bh)
            crop = frame[y1:int(y), x1:x1 + bw]
            if crop.size:
                k = int(cv2.CAP_PROP_POS_FRAMES)  # не используется, оставлено читаемым
                h = 300
                w = max(1, int(crop.shape[1] * h / crop.shape[0]))
                crop = cv2.resize(crop, (w, h), interpolation=cv2.INTER_LANCZOS4)
                name = "t%d_%d.jpg" % (tid, i)
                cv2.imwrite(os.path.join(out_dir, name), crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
                made += 1
    idx += 1
cap.release()

meta = {str(t): {"team": team_of.get(t, -1),
                 "shots": ["t%d_%d.jpg" % (t, i) for _, i, _ in picks[t]],
                 "frames_seen": len(seen[t])}
        for t in order}
json.dump(meta, open(os.path.join(out_dir, "tracks.json"), "w"), ensure_ascii=False)
print("треков: %d, снимков: %d, папка: %s" % (len(order), made, out_dir))
