"""Опора привязки по одному кадру: подписанные линии разметки -> однородная матрица кадр->план.

Не требует видео и протянутой привязки (в отличие от pitch_anchor.py): на вход кадр
в полном разрешении и JSON с примерными отрезками линий и подписями.

    python pitch_anchor_img.py кадр.jpg линии.json out_dir [--width 19.2]

линии.json:
  {"lines": {"end_near": {"segs": [[[x1,y1],[x2,y2]], ...], "label": "y=0"},
             "side_r":   {"segs": [...], "label": "x=19.2"}, ...},
   "points": [{"img": [x, y], "plan": [X, Y]}]      — необязательно, точки напрямую
  }
Подпись "y=..." — линия поперёк поля (линия ворот, средняя, передний край штрафной),
"x=..." — вдоль поля (боковая, края штрафной). Координаты плана: x вдоль линии ворот
0..ширина, y от ближних ворот 0..43,08. Пересекаются все пары x × y.

Выход: out_dir/anchor.json (матрица, точки, ошибка на плане), out_dir/anchor_check.jpg
(разметка плана поверх кадра), out_dir/anchor_small.jpg (кадр 960×540 для ORB-сверки).
"""
import json, sys, os
import numpy as np, cv2

img_path, lines_path, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(out_dir, exist_ok=True)
PP = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "pitch.json")))
W_P = float(sys.argv[sys.argv.index("--width") + 1]) if "--width" in sys.argv else PP["pitches"]["lower"]["width"]
L_P, HALF = PP["length"], PP["half_length"]; BW, BD, GW = PP["penalty_area"]["width"], PP["penalty_area"]["depth"], PP["goal"]["width"]
spec = json.load(open(lines_path))
img = cv2.imread(img_path); h, w = img.shape[:2]
g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
th = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
white = (th > 18) & (hsv[:, :, 1] < 80) & (hsv[:, :, 2] > 100)
ys, xs = np.nonzero(white); PT = np.stack([xs, ys], 1).astype(float)

def fit(segs, band=12):
    sel = []
    for (x1, y1), (x2, y2) in segs:
        d = np.array([x2 - x1, y2 - y1], float); L = np.linalg.norm(d); d /= L; n = np.array([-d[1], d[0]])
        rel = PT - [x1, y1]; t = rel @ d; o = rel @ n
        sel.append(PT[(t > -5) & (t < L + 5) & (np.abs(o) < band)])
    pts = np.concatenate(sel) if sel else np.zeros((0, 2))
    if len(pts) < 25:          # белых пикселей мало — берём сам отрезок как линию
        a = np.array(segs[0][0], float); b = np.array(segs[-1][1], float)
        return (a, (b - a) / np.linalg.norm(b - a)), len(pts), False
    vx, vy, x0, y0 = cv2.fitLine(pts.astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
    return (np.array([x0, y0]), np.array([vx, vy])), len(pts), True

def inter(a, b):
    (p, d), (q, e) = a, b
    A = np.array([d, -e]).T
    if abs(np.linalg.det(A)) < 1e-6: return None
    t = np.linalg.solve(A, q - p); return p + t[0] * d

def val(label):
    ax, v = label.split("="); v = v.strip()
    table = {"W": W_P, "L": L_P, "HALF": HALF, "BOXL": W_P / 2 - BW / 2, "BOXR": W_P / 2 + BW / 2, "BD": BD, "LBD": L_P - BD}
    return ax.strip(), float(table.get(v, v))

lines = {}
for k, L in spec.get("lines", {}).items():
    lines[k], n, fitted = fit(L["segs"])
    print("линия %-10s %-10s белых пикселей %5d %s" % (k, L["label"], n, "" if fitted else "(по отрезку)"))
corr = []
for a, A in spec.get("lines", {}).items():
    for b, B in spec.get("lines", {}).items():
        axa, va = val(A["label"]); axb, vb = val(B["label"])
        if axa != "x" or axb != "y": continue
        ip = inter(lines[a], lines[b])
        if ip is None or not (-0.3 * w < ip[0] < 1.3 * w and -0.3 * h < ip[1] < 1.3 * h): continue
        corr.append(((va, vb), ip, "%s∩%s" % (a, b)))
for pt in spec.get("points", []):
    corr.append((tuple(pt["plan"]), np.array(pt["img"], float), "точка"))
if len(corr) < 4: sys.exit("мало соответствий: %d" % len(corr))
src = np.array([c[1] for c in corr], float); dst = np.array([c[0] for c in corr], float)
H, _ = cv2.findHomography(src, dst, 0)
err = [float(np.hypot(*(cv2.perspectiveTransform(s.reshape(1, 1, 2), H).ravel() - d))) for s, d in zip(src, dst)]
for (pp, ip, name), e in zip(corr, err): print("  %-22s кадр (%6.0f,%6.0f) -> план %-14s ошибка %.2f м" % (name, ip[0], ip[1], pp, e))
print("соответствий %d, средняя ошибка %.2f м, максимум %.2f м" % (len(corr), np.mean(err), max(err)))

ov = img.copy(); Hi = np.linalg.inv(H)
segs = [[(0, 0), (W_P, 0), (W_P, L_P), (0, L_P), (0, 0)], [(0, HALF), (W_P, HALF)]]
for yb, sg in ((0, 1), (L_P, -1)):
    a_, b_ = W_P / 2 - BW / 2, W_P / 2 + BW / 2
    segs.append([(a_, yb), (a_, yb + sg * BD), (b_, yb + sg * BD), (b_, yb)])
    segs.append([(W_P / 2 - GW / 2, yb), (W_P / 2 + GW / 2, yb)])
sign = np.sign((Hi @ np.array([corr[0][0][0], corr[0][0][1], 1.0]))[2]) or 1.0
for pts in segs:
    dense = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t) for a, b in zip(pts, pts[1:]) for t in np.linspace(0, 1, 40)]
    q = (Hi @ np.c_[np.array(dense), np.ones(len(dense))].T).T; run = []
    for x, y, z in q:
        if z * sign > 1e-9 and -w < x / z < 2 * w and -h < y / z < 2 * h: run.append((int(x / z), int(y / z)))
        else:
            if len(run) > 1: cv2.polylines(ov, [np.array(run, np.int32).reshape(-1, 1, 2)], False, (0, 255, 255), 3, cv2.LINE_AA)
            run = []
    if len(run) > 1: cv2.polylines(ov, [np.array(run, np.int32).reshape(-1, 1, 2)], False, (0, 255, 255), 3, cv2.LINE_AA)
for _, ip, _ in corr: cv2.circle(ov, (int(ip[0]), int(ip[1])), 10, (255, 0, 255), 3)
cv2.imwrite(os.path.join(out_dir, "anchor_check.jpg"), cv2.resize(ov, (1280, 720)), [cv2.IMWRITE_JPEG_QUALITY, 88])
cv2.imwrite(os.path.join(out_dir, "anchor_small.jpg"), cv2.resize(g, (960, 540), interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 95])
json.dump({"image": os.path.abspath(img_path), "small": "anchor_small.jpg", "H_img2plan": (H / H[2, 2]).tolist(),
           "plan": {"width": W_P, "length": L_P}, "points": [{"name": n, "plan": list(p), "img": [float(i[0]), float(i[1])]} for p, i, n in corr],
           "reproj_err_m": err}, open(os.path.join(out_dir, "anchor.json"), "w"), ensure_ascii=False, indent=1)
print("готово:", out_dir)
