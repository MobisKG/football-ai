"""Привязка кадра к плану поля: однородное преобразование кадр <-> метры.

Опорный кадр — 2-я секунда клипа, видны ворота, штрафная и угол поля.
Линии не ставятся на глаз: по каждой примерной линии собираются белые пиксели в
полосе вокруг неё и подгоняется прямая. Объектив даёт заметную бочку — линия
ворот изгибается к краю кадра, — поэтому сначала подбирается радиальное
искажение k1, при котором точки каждой линии ложатся на прямую лучше всего.

Система координат плана: x вдоль линии ворот (0..19 м, ширина верхней
площадки), y от этой линии ворот вглубь поля (0..43,08 м). Ворота в центре x=9,5.

    python pitch_calib.py кадр.png out_dir
"""
import json, sys, itertools
import numpy as np, cv2

frame_path, out = sys.argv[1], sys.argv[2]
P = json.load(open("/Users/doni/Documents/football/docs/pitch.json"))
W_P, L_P = P["pitches"]["upper"]["width"], P["length"]
GW, BW, BD = P["goal"]["width"], P["penalty_area"]["width"], P["penalty_area"]["depth"]
HALF = P["half_length"]

img = cv2.imread(frame_path); H, W = img.shape[:2]
cx, cy, F = W / 2, H / 2, float(W)

# белые пиксели: светлые и малонасыщенные, выше фона вокруг (top-hat)
g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
th = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
white = (th > 22) & (hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 120)

ROUGH = {   # примерные отрезки по раскадровке, в пикселях кадра
    "end":   [[(150, 382), (715, 449)], [(995, 484), (1565, 597)]],
    "front": [[(170, 459), (955, 636)]],
    "boxr":  [[(990, 631), (1200, 530)]],
    "boxl":  [[(175, 452), (515, 416)]],
    "sider": [[(1582, 612), (1420, 1040)]],
}
BAND = 14

def band_points(segs, band=BAND):
    pts = []
    ys, xs = np.nonzero(white)
    P_ = np.stack([xs, ys], 1).astype(np.float64)
    for (x1, y1), (x2, y2) in segs:
        d = np.array([x2 - x1, y2 - y1], float); L = np.linalg.norm(d); d /= L
        n = np.array([-d[1], d[0]])
        rel = P_ - [x1, y1]
        t = rel @ d; o = rel @ n
        m = (t > 0) & (t < L) & (np.abs(o) < band)
        pts.append(P_[m])
    return np.concatenate(pts) if pts else np.zeros((0, 2))

# Линия ворот идёт вдоль яркого бордюра и нижней перекладины ворот: в широкой
# полосе они попадают в выборку и тянут прямую. Для неё полоса уже.
groups = {k: band_points(v, 7 if k == "end" else BAND) for k, v in ROUGH.items()}
print("белых пикселей по линиям:", {k: len(v) for k, v in groups.items()})

def undist(p, k1):
    x = (p[:, 0] - cx) / F; y = (p[:, 1] - cy) / F
    r2 = x * x + y * y; s = 1 + k1 * r2
    return np.stack([x * s * F + cx, y * s * F + cy], 1)

def redist(p, k1):
    # обратное к undist итерациями
    q = p.copy().astype(np.float64)
    for _ in range(12):
        x = (q[:, 0] - cx) / F; y = (q[:, 1] - cy) / F
        s = 1 + k1 * (x * x + y * y)
        q = np.stack([(p[:, 0] - cx) / s + cx, (p[:, 1] - cy) / s + cy], 1)
    return q

def line_resid(pts):
    if len(pts) < 20: return 0.0
    m = pts.mean(0); u, s, vt = np.linalg.svd(pts - m, full_matrices=False)
    n = vt[1]; r = np.abs((pts - m) @ n)
    return float(np.median(r))

best = None
# Искажение оцениваем только по чистым линиям. Прошлые попытки по всем линиям
# упирались в край диапазона (-0,39 при пределе -0,4, затем -0,895 при -0,9) —
# признак того, что минимизировался не изгиб объектива, а мусор в выборке.
CLEAN = ("front", "boxr", "boxl", "sider")
for k1 in np.linspace(-0.50, 0.20, 141):
    cost = sum(line_resid(undist(groups[k], k1)) for k in CLEAN) / len(CLEAN)
    if best is None or cost < best[0]: best = (cost, k1)
K1 = float(best[1])
if "--k1" in sys.argv:
    # Искажение по коротким линиям не определяется: стоимость почти плоская и
    # уходит к краю диапазона. Тогда фиксируем k1 явно и сравниваем ошибку.
    K1 = float(sys.argv[sys.argv.index("--k1") + 1])
    best = (sum(line_resid(undist(groups[k], K1)) for k in CLEAN) / len(CLEAN), K1)
print("искажение k1 = %.3f, медианный отступ от прямых %.2f px" % (K1, best[0]))
print("  отступ по линиям:", {k: round(line_resid(undist(v, K1)), 2) for k, v in groups.items()})
if abs(K1 - (-0.50)) < 1e-6 or abs(K1 - 0.20) < 1e-6:
    print("  ВНИМАНИЕ: k1 на краю диапазона, оценке искажения не доверять")

def fit(pts):
    u = undist(pts, K1)
    vx, vy, x0, y0 = cv2.fitLine(u.astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
    return np.array([x0, y0]), np.array([vx, vy])

def inter(l1, l2):
    (p, d), (q, e) = l1, l2
    A = np.array([d, -e]).T; t = np.linalg.solve(A, q - p)
    return p + t[0] * d

Lend, Lfront, Lboxr, Lboxl, Lsider = (fit(groups[k]) for k in ("end", "front", "boxr", "boxl", "sider"))
# Основания стоек ворот в опоры НЕ берём: по первой попытке они стоят примерно
# на полметра за нарисованной линией (ворота поставлены за линию или клик
# пришёлся на заднюю перекладину). Опоры — только пересечения линий разметки.
posts_img = undist(np.array([[722.0, 450.0], [966.0, 480.0]]), K1)

xg0 = W_P / 2 - GW / 2; xg1 = W_P / 2 + GW / 2
xbr = W_P / 2 + BW / 2
xbl = W_P / 2 - BW / 2
corr = [  # (план, кадр без искажения)
    ((W_P, 0.0), inter(Lend, Lsider)),
    ((xbr, BD), inter(Lfront, Lboxr)),
    ((W_P, BD), inter(Lfront, Lsider)),
    ((xbr, 0.0), inter(Lend, Lboxr)),
    ((xbl, BD), inter(Lfront, Lboxl)),
    ((xbl, 0.0), inter(Lend, Lboxl)),
]
src = np.array([c[1] for c in corr], np.float64)
dst = np.array([c[0] for c in corr], np.float64)
Himg2plan, _ = cv2.findHomography(src, dst, 0)
Hplan2img = np.linalg.inv(Himg2plan)

def to_img(pts_plan):
    p = np.asarray(pts_plan, float).reshape(-1, 1, 2)
    u = cv2.perspectiveTransform(p, Hplan2img).reshape(-1, 2)
    return redist(u, K1)

def to_img_safe(pts_plan):
    """Проекция с отсечением: точки за камерой и далеко за краем кадра
    выбрасываются, иначе обратное искажение разлетается и рисует мусор."""
    p = np.asarray(pts_plan, float)
    hom = (Hplan2img @ np.c_[p, np.ones(len(p))].T).T
    # Знак однородной координаты у видимых точек зависит от масштаба матрицы и
    # может быть отрицательным. Берём знак по опорной точке, заведомо видимой:
    # раньше проверка «w > 0» отбрасывала вообще всё, и линии не рисовались.
    ref = Hplan2img @ np.array([corr[0][0][0], corr[0][0][1], 1.0])
    ok = hom[:, 2] * np.sign(ref[2]) > 1e-9
    u = hom[:, :2] / hom[:, 2:3]
    xn = (u[:, 0] - cx) / F; yn = (u[:, 1] - cy) / F
    ok &= (np.abs(xn) < 0.75) & (np.abs(yn) < 0.5)
    q = np.full_like(u, np.nan)
    if ok.any(): q[ok] = redist(u[ok], K1)
    return q

err = []
for (pp, ip) in corr:
    back = cv2.perspectiveTransform(np.array([[ip]], float), Himg2plan).ravel()
    err.append(float(np.hypot(*(back - pp))))
print("опорные точки, ошибка на плане, м:", ["%.2f" % e for e in err])
pp = cv2.perspectiveTransform(posts_img.reshape(-1, 1, 2), Himg2plan).reshape(-1, 2)
print("проверка: стойки ворот на плане", [[round(a, 2), round(b, 2)] for a, b in pp],
      "(ожидается x=%.2f и %.2f, y около 0)" % (xg0, xg1))

# наложение всей разметки поля на кадр
ov = img.copy()
def poly(pts, col, w=3):
    dense = []
    for a, b in zip(pts, pts[1:]):
        for t in np.linspace(0, 1, 40): dense.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    ip = to_img_safe(dense)
    run = []
    for q in ip:
        if np.isnan(q[0]):
            if len(run) > 1: cv2.polylines(ov, [np.array(run, np.int32).reshape(-1, 1, 2)], False, col, w, cv2.LINE_AA)
            run = []
        else:
            run.append((int(q[0]), int(q[1])))
    if len(run) > 1: cv2.polylines(ov, [np.array(run, np.int32).reshape(-1, 1, 2)], False, col, w, cv2.LINE_AA)
Y = (0, 255, 255); M = (255, 0, 255)
poly([(0, 0), (W_P, 0), (W_P, L_P), (0, L_P), (0, 0)], Y)
poly([(0, HALF), (W_P, HALF)], Y)
for yb, sg in ((0, 1), (L_P, -1)):
    x0b, x1b = W_P / 2 - BW / 2, W_P / 2 + BW / 2
    poly([(x0b, yb), (x0b, yb + sg * BD), (x1b, yb + sg * BD), (x1b, yb)], M)
for (pp_, ip) in corr:
    q = to_img([pp_])[0].astype(int)
    cv2.circle(ov, tuple(q), 9, (0, 0, 255), 3)
# ворота по плану — голубым, для сравнения с настоящими стойками
poly([(xg0, 0), (xg0, -0.8), (xg1, -0.8), (xg1, 0)], (255, 200, 0), 2)
cv2.imwrite(out + "/overlay.jpg", ov, [cv2.IMWRITE_JPEG_QUALITY, 90])

# вид сверху
S_ = 25
top = np.zeros((int(L_P * S_) + 100, int(W_P * S_) + 100, 3), np.uint8)
Hplan2top = np.array([[S_, 0, 50], [0, S_, 50], [0, 0, 1]], float)
und = cv2.undistortPoints  # не нужен; делаем через карту
ys_, xs_ = np.mgrid[0:H, 0:W]
grid = np.stack([xs_.ravel(), ys_.ravel()], 1).astype(np.float64)
mapu = undist(grid, K1)
# warpPerspective требует неискажённого исходника: строим его обратной картой
inv = redist(grid, K1).astype(np.float32).reshape(H, W, 2)
undist_img = cv2.remap(img, inv[:, :, 0], inv[:, :, 1], cv2.INTER_LINEAR)
top = cv2.warpPerspective(undist_img, Hplan2top @ Himg2plan, (top.shape[1], top.shape[0]))
cv2.imwrite(out + "/topdown.jpg", top, [cv2.IMWRITE_JPEG_QUALITY, 90])

json.dump({"frame": frame_path, "k1": K1, "center": [cx, cy], "f_norm": F,
           "H_img2plan": Himg2plan.tolist(), "plan": {"width": W_P, "length": L_P},
           "points": [{"plan": list(c[0]), "img_undist": [float(c[1][0]), float(c[1][1])]} for c in corr],
           "reproj_err_m": err}, open(out + "/calib_ref.json", "w"), indent=1)
print("готово")
