"""Дополнительная опора привязки по кадру с дальними воротами.

Первая опора (2-я секунда) стоит у ближних ворот, и на дальнем конце поля ошибка
растёт до 1–2 м: 43 метра от опорных точек. Здесь то же самое для кадра, где
видны дальние ворота и штрафная.

Какой линии какая точка плана соответствует, не угадывается вручную: текущая
протянутая матрица уже примерно знает положение, поэтому каждое пересечение
линий привязывается к ближайшей известной точке плана.

    python pitch_anchor.py видео.mp4 track.json сек out_dir "линии.json"
"""
import json, sys, os
import numpy as np, cv2

video, track_path, sec, out_dir, rough_path = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4], sys.argv[5]
os.makedirs(out_dir, exist_ok=True)
T = json.load(open(track_path)); PP = json.load(open("/Users/doni/Documents/football/docs/pitch.json"))
W_P, L_P = T["plan"]["width"], T["plan"]["length"]
BW, BD = PP["penalty_area"]["width"], PP["penalty_area"]["depth"]
ROUGH_ALL = json.load(open(rough_path))
# Явные подписи линий: "y=43.08" — линия поперёк поля, "x=14" — вдоль.
# Подпись по ближайшей точке старой привязки на дальнем конце не годится: там
# протянутая привязка ошибалась на 9 м, и точки уходили к чужим линиям.
LABELS = ROUGH_ALL.pop("labels", {})
ROUGH = ROUGH_ALL

cap = cv2.VideoCapture(video); FPS = cap.get(cv2.CAP_PROP_FPS)
i = int(round(sec * FPS)) // T["step"] * T["step"]
cap.set(cv2.CAP_PROP_POS_FRAMES, i); ok, img = cap.read()
f = {x["i"]: x for x in T["frames"]}[i]
H0 = np.array(f["H_img2plan"], float).reshape(3, 3)

g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
th = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
white = (th > 20) & (hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 110)
ys, xs = np.nonzero(white); PT = np.stack([xs, ys], 1).astype(float)

def fit(segs, band=10):
    sel = []
    for (x1, y1), (x2, y2) in segs:
        d = np.array([x2 - x1, y2 - y1], float); L = np.linalg.norm(d); d /= L; n = np.array([-d[1], d[0]])
        rel = PT - [x1, y1]; t = rel @ d; o = rel @ n
        sel.append(PT[(t > 0) & (t < L) & (np.abs(o) < band)])
    pts = np.concatenate(sel)
    vx, vy, x0, y0 = cv2.fitLine(pts.astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
    return (np.array([x0, y0]), np.array([vx, vy])), len(pts)

lines = {}
for k, segs in ROUGH.items():
    lines[k], n = fit(segs)
    print("линия %-6s белых пикселей %d" % (k, n))

def inter(a, b):
    (p, d), (q, e) = a, b
    t = np.linalg.solve(np.array([d, -e]).T, q - p); return p + t[0] * d

corr = []
def lab(k):
    ax, val = LABELS[k].split("=")
    return ax, float(val)
names = [k for k in lines if k in LABELS]
for a_ in names:
    for b_ in names:
        axa, va = lab(a_); axb, vb = lab(b_)
        if not (axa == "x" and axb == "y"): continue
        ip = inter(lines[a_], lines[b_])
        pt = (va, vb)
        pp = cv2.perspectiveTransform(ip.reshape(1, 1, 2), H0).ravel()
        print("  %s ∩ %s -> кадр (%.0f, %.0f) = план %s; старая привязка давала (%.1f, %.1f)"
              % (a_, b_, ip[0], ip[1], pt, pp[0], pp[1]))
        corr.append((pt, ip))
src = np.array([c[1] for c in corr]); dst = np.array([c[0] for c in corr], float)
H1, _ = cv2.findHomography(src, dst, 0)
err = [float(np.hypot(*(cv2.perspectiveTransform(s.reshape(1, 1, 2), H1).ravel() - d))) for s, d in zip(src, dst)]
print("новая опора: точек %d, ошибка на плане, м: %s" % (len(corr), ["%.2f" % e for e in err]))
if "posts" in LABELS:
    for (px, py, xs_) in LABELS["posts"]:
        q = cv2.perspectiveTransform(np.array([[[px, py]]], float), H1).ravel()
        print("  проверка: стойка ворот кадр (%d, %d) -> план (%.2f, %.2f), ожидается x=%.2f у линии ворот" % (px, py, q[0], q[1], xs_))

json.dump({"frame_index": i, "sec": sec, "k1": 0.0, "H_img2plan": H1.tolist(), "plan": T["plan"],
           "points": [{"plan": list(c[0]), "img": [float(c[1][0]), float(c[1][1])]} for c in corr],
           "reproj_err_m": err}, open(os.path.join(out_dir, "calib_%05.1f.json" % sec), "w"), indent=1)

ov = img.copy(); h, w = ov.shape[:2]
def draw(H, col):
    Hi = np.linalg.inv(H)
    segs = [[(0, 0), (W_P, 0), (W_P, L_P), (0, L_P), (0, 0)], [(0, PP["half_length"]), (W_P, PP["half_length"])]]
    for yb, sg in ((0, 1), (L_P, -1)):
        a, b = W_P / 2 - BW / 2, W_P / 2 + BW / 2
        segs.append([(a, yb), (a, yb + sg * BD), (b, yb + sg * BD), (b, yb)])
    sign = np.sign((Hi @ np.array([W_P / 2, L_P - 3, 1.0]))[2]) or 1.0
    for pts in segs:
        dense = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t) for a, b in zip(pts, pts[1:]) for t in np.linspace(0, 1, 30)]
        q = (Hi @ np.c_[np.array(dense), np.ones(len(dense))].T).T
        run = []
        for x, y, z in q:
            if z * sign > 1e-9 and -w < x / z < 2 * w and -h < y / z < 2 * h: run.append((int(x / z), int(y / z)))
            else:
                if len(run) > 1: cv2.polylines(ov, [np.array(run, np.int32).reshape(-1, 1, 2)], False, col, 3, cv2.LINE_AA)
                run = []
        if len(run) > 1: cv2.polylines(ov, [np.array(run, np.int32).reshape(-1, 1, 2)], False, col, 3, cv2.LINE_AA)
draw(H0, (0, 0, 255))
draw(H1, (0, 255, 255))
for c in corr: cv2.circle(ov, (int(c[1][0]), int(c[1][1])), 9, (255, 0, 255), 3)
cv2.imwrite(os.path.join(out_dir, "anchor_%05.1f.jpg" % sec), cv2.resize(ov, (1280, 720)), [cv2.IMWRITE_JPEG_QUALITY, 88])
print("готово")
