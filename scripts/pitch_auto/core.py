"""Автоматическая привязка кадра к плану поля (Душанбинка, три площадки).

Кадр → матрица кадр→план строится в два шага:
  1) грубая матрица: сверка кадра с ручными опорами той же площадки (SIFT + RANSAC, фон
     у обеих съёмок общий: оператор стоит на одной трибуне) — или любая другая подсказка;
  2) уточнение по белым линиям: линии плана проецируются в кадр и матрица подгоняется так,
     чтобы они легли на светлые тонкие штрихи (top-hat) кадра; расстояние берётся из
     карты расстояний, потеря — робастная, поэтому игроки, тени и сетка мешают мало.
Качество — доля точек линий плана, попавших на белое (coverage), и число опорных линий.
"""
import json, os, glob
import numpy as np, cv2
from scipy.optimize import least_squares
from scipy.ndimage import map_coordinates

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
P = json.load(open(os.path.join(ROOT, "docs", "pitch.json")))
FIELD_PITCH = {"Полей №3": "field3", "Полей №1": "lower", "Полей №2": "lower"}

def plan_of(key):
    pl = P["pitches"][key]
    return {"width": pl["width"], "length": pl["length"], "half": P["half_length"], "goal": P["goal"]["width"],
            "box_w": P["penalty_area"]["width"], "box_d": P["penalty_area"]["depth"], "key": key}

def landmarks(plan):
    W, L, HL, bw, bd, g = plan["width"], plan["length"], plan["half"], plan["box_w"], plan["box_d"], plan["goal"]
    x0, x1, g0, g1 = W / 2 - bw / 2, W / 2 + bw / 2, W / 2 - g / 2, W / 2 + g / 2
    return {"c00": (0, 0), "c10": (W, 0), "b00": (x0, 0), "b10": (x1, 0), "b01": (x0, bd), "b11": (x1, bd), "g00": (g0, 0), "g10": (g1, 0),
            "h0": (0, HL), "h1": (W, HL), "hc": (W / 2, HL), "c01": (0, L), "c11": (W, L), "b02": (x0, L), "b12": (x1, L),
            "b03": (x0, L - bd), "b13": (x1, L - bd), "g01": (g0, L), "g11": (g1, L)}

def plan_lines(plan, circle=True):
    """отрезки плана (name, (x0,y0), (x1,y1)); круг — ломаная"""
    W, L, HL, bw, bd = plan["width"], plan["length"], plan["half"], plan["box_w"], plan["box_d"]
    x0, x1 = W / 2 - bw / 2, W / 2 + bw / 2
    S = [("far_touch", (0, 0), (0, L)), ("near_touch", (W, 0), (W, L)), ("goal0", (0, 0), (W, 0)), ("goal1", (0, L), (W, L)), ("half", (0, HL), (W, HL)),
         ("box0_l", (x0, 0), (x0, bd)), ("box0_f", (x0, bd), (x1, bd)), ("box0_r", (x1, 0), (x1, bd)),
         ("box1_l", (x0, L), (x0, L - bd)), ("box1_f", (x0, L - bd), (x1, L - bd)), ("box1_r", (x1, L), (x1, L - bd))]
    if circle:
        R = plan.get("circle_r", 3.0); a = np.linspace(0, 2 * np.pi, 33)
        pts = np.c_[W / 2 + R * np.cos(a), HL + R * np.sin(a)]
        S += [("circle", tuple(pts[i]), tuple(pts[i + 1])) for i in range(len(pts) - 1)]
    return S

def sample_lines(plan, step=0.25, circle=True):
    """точки на линиях плана: массив (N,2) и индекс линии (N,)"""
    pts, idx, names = [], [], []
    for k, (n, a, b) in enumerate(plan_lines(plan, circle)):
        a, b = np.array(a, float), np.array(b, float); m = max(2, int(np.hypot(*(b - a)) / step) + 1)
        t = np.linspace(0, 1, m)[:, None]; pts.append(a + t * (b - a)); idx.append(np.full(m, k)); names.append(n)
    return np.vstack(pts), np.concatenate(idx), names

# ---------- белые линии
def line_mask(img, k=25, thr=28, sat_max=90):
    """тонкие светлые штрихи: top-hat по яркости, без насыщенных цветов (форма игроков)"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV); gray = hsv[:, :, 2]
    th = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    m = (th > thr) & (hsv[:, :, 1] < sat_max)
    return m.astype(np.uint8)

def dist_map(mask, cap=40.0):
    d = cv2.distanceTransform((mask == 0).astype(np.uint8), cv2.DIST_L2, 5)
    return np.minimum(d, cap).astype(np.float32)

# ---------- геометрия
def project(H_plan2img, pts):
    p = np.c_[pts, np.ones(len(pts))]; q = (H_plan2img @ p.T).T
    return q[:, :2] / q[:, 2:3], q[:, 2]

def front_sign(H_plan2img, plan, shape=(1080, 1920)):
    """знак однородной координаты «перед камерой»: берём точку кадра в нижней части (она точно
    на земле перед камерой), переводим на план и смотрим знак w при обратной проекции.
    По центру поля нельзя: на кадрах у ворот центр бывает за горизонтом"""
    h, w = shape[:2]; Hi = np.linalg.inv(H_plan2img)
    for uv in ((w / 2, h * 0.85), (w / 2, h * 0.6), (w * 0.3, h * 0.8), (w * 0.7, h * 0.8)):
        q = Hi @ np.array([uv[0], uv[1], 1.0])
        if abs(q[2]) < 1e-9: continue
        _, ww = project(H_plan2img, np.array([[q[0] / q[2], q[1] / q[2]]]))
        if abs(ww[0]) > 1e-9: return float(np.sign(ww[0]))
    return 1.0

def visible(H_plan2img, pts, shape, sign, margin=0):
    uv, w = project(H_plan2img, pts); h, W = shape[:2]
    ok = (w * sign > 1e-6) & (uv[:, 0] >= -margin) & (uv[:, 0] < W + margin) & (uv[:, 1] >= -margin) & (uv[:, 1] < h + margin)
    return uv, ok

def refine(H_img2plan, dist, plan, shape, scale=1.0, rounds=((40.0, 3), (12.0, 3), (5.0, 2)), circle=True, sign=None, prior_m=3.0):
    """подгонка матрицы к карте расстояний. dist — карта расстояний в масштабе scale (кадр * scale).
    Возвращает H_img2plan, coverage (доля видимых точек ближе 3 px), число линий с опорой."""
    pts, idx, names = sample_lines(plan, 0.25, circle)
    Hp2i0 = np.linalg.inv(H_img2plan); Hp2i0 /= Hp2i0[2, 2]
    sg = front_sign(Hp2i0, plan) if sign is None else sign
    S = np.diag([scale, scale, 1.0]); dh, dw = dist.shape
    def res_fn(x, cap):
        Hm = np.r_[x, 1.0].reshape(3, 3)
        uv, w = project(S @ Hm, pts)
        ok = (w * sg > 1e-6) & (uv[:, 0] >= 1) & (uv[:, 0] < dw - 2) & (uv[:, 1] >= 1) & (uv[:, 1] < dh - 2)
        d = np.full(len(pts), cap, np.float32)
        if ok.sum() >= 8:
            d[ok] = map_coordinates(dist, [uv[ok, 1], uv[ok, 0]], order=1, mode="nearest")
        return np.minimum(d, cap) / cap
    # якорь: четыре точки кадра, спроецированные на план начальной матрицей, не должны уходить дальше prior_m
    h, w = shape[:2]; anc_img = np.array([[w * .25, h * .35], [w * .75, h * .35], [w * .75, h * .9], [w * .25, h * .9], [w * .5, h * .6]])
    anc0 = (H_img2plan @ np.c_[anc_img, np.ones(5)].T).T; anc0 = anc0[:, :2] / anc0[:, 2:3]
    wgt = PRIOR_W
    def prior(x):
        Hm = np.r_[x, 1.0].reshape(3, 3); Hi = np.linalg.inv(Hm)
        q = (Hi @ np.c_[anc_img, np.ones(5)].T).T; q = q[:, :2] / q[:, 2:3]
        return (np.hypot(*(q - anc0).T) / prior_m * wgt)
    res0 = res_fn
    res_fn = lambda x, cap: np.r_[res0(x, cap), prior(x)]
    x = Hp2i0.ravel()[:8].copy()
    for cap, n in rounds:
        for _ in range(n):
            r = least_squares(res_fn, x, args=(cap,), loss="soft_l1", f_scale=0.3, max_nfev=60, method="trf", x_scale="jac")
            x = r.x
    Hp2i = np.r_[x, 1.0].reshape(3, 3)
    q = quality(Hp2i, dist, plan, scale, sg, circle)
    Hi2p = np.linalg.inv(Hp2i); Hi2p /= Hi2p[2, 2]
    return Hi2p, q

def quality(Hp2i, dist, plan, scale, sign, circle=True, tol=3.0):
    pts, idx, names = sample_lines(plan, 0.25, circle)
    S = np.diag([scale, scale, 1.0]); dh, dw = dist.shape
    uv, w = project(S @ Hp2i, pts)
    ok = (w * sign > 1e-6) & (uv[:, 0] >= 1) & (uv[:, 0] < dw - 2) & (uv[:, 1] >= 1) & (uv[:, 1] < dh - 2)
    if ok.sum() < 8: return {"coverage": 0.0, "lines": 0, "visible": int(ok.sum()), "per_line": {}}
    d = map_coordinates(dist, [uv[ok, 1], uv[ok, 0]], order=1, mode="nearest")
    hit = d < tol * scale
    per = {}
    for k, n in enumerate(names):
        m = idx[ok] == k
        if m.sum() >= 6: per[n] = float(hit[m].mean())
    # опорные линии: ≥ 60 % точек на белом; круг считается одной линией
    good = [n for n, v in per.items() if v >= 0.6]
    # число независимых направлений: продольные (touch, box_l/r) и поперечные (goal, half, box_f)
    longi = [n for n in good if "touch" in n or n.endswith("_l") or n.endswith("_r")]
    trans = [n for n in good if n.startswith("goal") or n == "half" or n.endswith("_f")]
    return {"coverage": float(hit.mean()), "lines": len(good), "visible": int(ok.sum()), "per_line": per,
            "longi": len(longi), "trans": len(trans), "good": good}

# ---------- опоры пользователя
def load_manual(pitch_root=os.path.join(ROOT, "results", "pitch"), jobs=None):
    """все ручные опоры: [{game, sec, image, points{key:(u,v)}, H (или None), plan, field}]"""
    jobs = jobs or {j["slug"]: j for j in json.load(open(os.path.join(pitch_root, "jobs.json")))}
    out = []
    for f in sorted(glob.glob(os.path.join(pitch_root, "*", "manual", "frame_t*.json"))):
        j = json.load(open(f)); game = j.get("game") or f.split("/")[-3]
        if j.get("skip") or not j.get("points"): continue
        img = j.get("image")
        if not img or not os.path.exists(img):
            cands = glob.glob(os.path.join(pitch_root, game, "frames_%s_full" % game, "t%04d.jpg" % j["sec"])) + glob.glob(os.path.join(pitch_root, game, "full_*", "t%04d.jpg" % j["sec"]))
            img = cands[0] if cands else None
        if not img: continue
        field = jobs.get(game, {}).get("field"); key = FIELD_PITCH.get(field, "lower")
        plan = plan_of(key); plan["width"], plan["length"] = j["plan"]["width"], j["plan"]["length"]
        out.append({"game": game, "sec": j["sec"], "image": img, "points": {p["key"]: tuple(p["img"]) for p in j["points"]},
                    "H": np.array(j["H_img2plan"], float) if j.get("H_img2plan") else None, "plan": plan, "field": field, "pitch": key})
    return out

def landmark_error(H_img2plan, points, plan):
    """ошибка проекции ручных ориентиров на план, м: список по точкам"""
    LM = landmarks(plan); errs = {}
    for k, (u, v) in points.items():
        if k not in LM: continue
        q = H_img2plan @ np.array([u, v, 1.0]); q = q[:2] / q[2]
        errs[k] = float(np.hypot(*(q - np.array(LM[k]))))
    return errs

# ---------- чистка маски: сетка/забор/тени дают густую кашу штрихов, линии поля — редкие длинные штрихи
def clean_mask(mask, scale=0.5, dens_k=31, dens_thr=0.2, min_len=20, poly=None):
    """маска в масштабе scale: убраны области с высокой плотностью штрихов и мелкие компоненты;
    poly — многоугольник поля в координатах полного кадра: всё вне него обнуляется"""
    m = (cv2.resize(mask.astype(np.float32), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) > 0.15).astype(np.uint8)
    if dens_thr < 1:
        dens = cv2.blur(m.astype(np.float32), (dens_k, dens_k))
        clutter = cv2.dilate((dens > dens_thr).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dens_k, dens_k)))
        m[clutter > 0] = 0
    if poly is not None:
        pm = np.zeros_like(m); cv2.fillPoly(pm, [np.round(np.asarray(poly) * scale).astype(np.int32)], 1); m &= pm
    if min_len > 0:
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        keep = np.zeros(n, bool); keep[1:] = np.maximum(st[1:, cv2.CC_STAT_WIDTH], st[1:, cv2.CC_STAT_HEIGHT]) >= min_len
        m = keep[lab].astype(np.uint8)
    return m

def field_poly(H_img2plan, plan, shape, margin=1.5, n=40):
    """многоугольник поля с запасом в кадре (точки за горизонтом отброшены); None, если мало точек"""
    W, L = plan["width"], plan["length"]; M = margin
    corners = np.array([[-M, -M], [W + M, -M], [W + M, L + M], [-M, L + M]], float)
    dense = np.vstack([np.linspace(corners[k], corners[(k + 1) % 4], n) for k in range(4)])
    Hp2i = np.linalg.inv(H_img2plan); sg = front_sign(Hp2i, plan)
    uv, w = project(Hp2i, dense); ok = w * sg > 1e-6
    if ok.sum() < 8: return None
    h, wd = shape[:2]; pts = np.clip(uv[ok], [-3 * wd, -3 * h], [4 * wd, 4 * h])
    return cv2.convexHull(pts.astype(np.float32)).reshape(-1, 2)

PRIOR_W = 2.0
MASK = {"k": 15, "thr": 30, "dens_thr": 0.2, "min_len": 20}
def prepare(img, H_init=None, plan=None, scale=0.5, margin=1.5, raw=None, prm=None):
    """маска линий → чистая маска и карта расстояний в масштабе scale"""
    prm = prm or MASK
    if raw is None: raw = line_mask(img, k=prm["k"], thr=prm["thr"])
    poly = field_poly(H_init, plan, img.shape, margin) if H_init is not None else None
    clean = clean_mask(raw, scale, dens_thr=prm["dens_thr"], min_len=prm["min_len"], poly=poly)
    return dist_map(clean), clean, raw
