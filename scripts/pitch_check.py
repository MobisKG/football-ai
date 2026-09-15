"""Проверка привязки: рисует разметку поля по плану на кадрах в заданные моменты.

    python pitch_check.py видео.mp4 track.json out_dir 30 60 135 229.5 ...
"""
import json, sys, os
import numpy as np, cv2

video, track_path, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
times = [float(t) for t in sys.argv[4:]]
os.makedirs(out_dir, exist_ok=True)
T = json.load(open(track_path))
_here = os.path.dirname(os.path.abspath(__file__))
# размеры полей: рядом со скриптом (на машине) или в docs/ проекта (на ноутбуке)
P = json.load(open(next(x for x in (os.path.join(_here, "pitch.json"), os.path.join(_here, "..", "docs", "pitch.json")) if os.path.exists(x))))
W_P, L_P = T["plan"]["width"], T["plan"]["length"]
BW, BD, GW, HALF = P["penalty_area"]["width"], P["penalty_area"]["depth"], P["goal"]["width"], P["half_length"]
byi = {f["i"]: f for f in T["frames"]}
cap = cv2.VideoCapture(video); FPS = cap.get(cv2.CAP_PROP_FPS)

LINES = [([(0, 0), (W_P, 0), (W_P, L_P), (0, L_P), (0, 0)], (0, 255, 255)),
         ([(0, HALF), (W_P, HALF)], (0, 255, 255))]
for yb, sg in ((0, 1), (L_P, -1)):
    a, b = W_P / 2 - BW / 2, W_P / 2 + BW / 2
    LINES.append(([(a, yb), (a, yb + sg * BD), (b, yb + sg * BD), (b, yb)], (255, 0, 255)))
# центральная точка и круг радиусом 3 м — ориентир в середине поля
circ = [(W_P / 2 + 3 * np.cos(t), HALF + 3 * np.sin(t)) for t in np.linspace(0, 2 * np.pi, 60)]
LINES.append((circ, (0, 255, 255)))

for t in times:
    i = int(round(t * FPS)) // T["step"] * T["step"]
    f = byi.get(i)
    if f is None: continue
    cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, fr = cap.read()
    if not ok: continue
    H = np.array(f["H_img2plan"], float).reshape(3, 3); Hi = np.linalg.inv(H)
    h, w = fr.shape[:2]
    # матрицы привязки — в координатах 1920×1080; видео может быть 720p
    if w != 1920: Hi = np.diag([w / 1920.0, w / 1920.0, 1.0]) @ Hi
    for pts, col in LINES:
        dense = []
        for a_, b_ in zip(pts, pts[1:]):
            for s in np.linspace(0, 1, 30): dense.append((a_[0] + (b_[0] - a_[0]) * s, a_[1] + (b_[1] - a_[1]) * s))
        p = np.c_[np.array(dense), np.ones(len(dense))]
        q = (Hi @ p.T).T
        ref_sign = np.sign((Hi @ np.array([W_P / 2, HALF, 1.0]))[2]) or 1.0
        run = []
        for (x, y, z) in q:
            good = z * ref_sign > 1e-9
            if good:
                u, v = x / z, y / z
                good = -w < u < 2 * w and -h < v < 2 * h
            if good: run.append((int(u), int(v)))
            else:
                if len(run) > 1: cv2.polylines(fr, [np.array(run, np.int32).reshape(-1, 1, 2)], False, col, 3, cv2.LINE_AA)
                run = []
        if len(run) > 1: cv2.polylines(fr, [np.array(run, np.int32).reshape(-1, 1, 2)], False, col, 3, cv2.LINE_AA)
    cv2.rectangle(fr, (0, 0), (620, 50), (0, 0, 0), -1)
    cv2.putText(fr, "t=%.1fs  src=%s  inl=%d" % (t, f["src"], f["inl"]), (12, 36), 0, 1.0, (255, 255, 255), 2)
    cv2.imwrite(os.path.join(out_dir, "check_%06.1f.jpg" % t), cv2.resize(fr, (1280, 720)), [cv2.IMWRITE_JPEG_QUALITY, 85])
print("ok", len(times))
