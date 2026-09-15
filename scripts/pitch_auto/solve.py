"""Из карт классов линий (сеть lines_net) → матрица кадр→план.

1. По каждому классу линии — прямые в кадре (RANSAC), 1–2 на класс.
2. Две гипотезы, какой конец поля виден (y=0 «у постройки» или y=L «у деревьев»): линии
   ворот/штрафной получают координаты плана; матрица — DLT по соответствиям прямых
   (l ∝ Hᵀ·L). Верная гипотеза — та, у которой якобиан кадр→план положителен (камера
   над полем сбоку x=W: у всех ручных опор det > 0), при равенстве — меньшая невязка.
3. Уточнение по картам расстояний каждого класса (робастные наименьшие квадраты) и
   оценка качества: доля точек каждой линии плана, легших на предсказанные пиксели.
"""
import os, sys, json
import numpy as np, cv2
from scipy.optimize import least_squares
from scipy.ndimage import map_coordinates
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from core import plan_lines, sample_lines, project, front_sign, landmarks, line_mask, dist_map, refine as refine_bin, quality as quality_bin
from lines_net import CLASSES, LINE_CLASS, WW, WH, SCALE

def class_pixels(prob, c, thr=0.5):
    pred = prob.argmax(0); m = (pred == c) & (prob[c] > thr)
    ys, xs = np.nonzero(m); return np.c_[xs, ys].astype(np.float64)   # координаты 960×540

def ransac_lines(pts, max_lines=2, min_pts=50, tol=2.0, iters=400, rng=None):
    rng = rng or np.random.default_rng(0); out = []
    pts = pts.copy()
    for _ in range(max_lines):
        if len(pts) < min_pts: break
        best = (0, None)
        for _ in range(iters):
            i, j = rng.integers(len(pts), size=2)
            if i == j: continue
            d = pts[j] - pts[i]; n = np.hypot(*d)
            if n < 15: continue
            nrm = np.array([-d[1], d[0]]) / n; c = -nrm @ pts[i]
            inl = np.abs(pts @ nrm + c) < tol
            if inl.sum() > best[0]: best = (int(inl.sum()), inl)
        if best[0] < min_pts: break
        P = pts[best[1]]; mu = P.mean(0); u, s, vt = np.linalg.svd(P - mu); d = vt[0]
        nrm = np.array([-d[1], d[0]]); c = -nrm @ mu
        inl = np.abs(pts @ nrm + c) < tol; P = pts[inl]
        t = (P - mu) @ d
        out.append({"n": int(inl.sum()), "line": np.array([nrm[0], nrm[1], c]), "ext": (float(t.min()), float(t.max())), "mu": mu, "dir": d, "spread": float(t.max() - t.min())})
        pts = pts[~inl]
    return out

def detect_lines(prob, thr=0.5):
    """{класс: [прямые]} в координатах 960×540"""
    out = {}
    for c in range(1, len(CLASSES)):
        pts = class_pixels(prob, c, thr)
        if len(pts) < 50: continue
        ls = ransac_lines(pts, max_lines=2 if CLASSES[c] in ("goal", "box_f") else 1)
        ls = [l for l in ls if l["spread"] >= 40]
        if ls: out[CLASSES[c]] = ls
    return out

def plan_line_eq(plan, name):
    for n, a, b in plan_lines(plan, circle=False):
        if n == name:
            a, b = np.array(a, float), np.array(b, float); d = b - a; nrm = np.array([-d[1], d[0]]) / np.hypot(*d)
            return np.array([nrm[0], nrm[1], -nrm @ a])
    raise KeyError(name)

def dlt_lines(corr):
    """corr: [(l_img (3,), L_plan (3,))], l ∝ Hᵀ L, H: кадр→план. Возвращает H или None"""
    if len(corr) < 4: return None
    # нормировка: сдвиг/масштаб прямых кадра и плана
    A = []
    for l, L in corr:
        l = l / np.linalg.norm(l[:2]); L = L / np.linalg.norm(L[:2])
        # l × (Hᵀ L) = 0; Hᵀ L = [h00 L0 + h10 L1 + h20 L2, h01 L0 + h11 L1 + h21 L2, h02 L0 + h12 L1 + h22 L2]
        # неизвестные h в порядке h00..h22 (построчно)
        def row(k):   # компонента k вектора Hᵀ L как линейная форма по h
            r = np.zeros(9); r[k] = L[0]; r[3 + k] = L[1]; r[6 + k] = L[2]; return r
        m0, m1, m2 = row(0), row(1), row(2)
        A.append(l[1] * m2 - l[2] * m1); A.append(l[2] * m0 - l[0] * m2)
    A = np.array(A); _, s, vt = np.linalg.svd(A); H = vt[-1].reshape(3, 3)
    if abs(H[2, 2]) < 1e-12: return None
    return H / H[2, 2]

def jac_sign(H, uv):
    def f(u, v): q = H @ np.array([u, v, 1.0]); return q[:2] / q[2]
    e = 1.0; J = np.c_[(f(uv[0] + e, uv[1]) - f(uv[0] - e, uv[1])) / 2, (f(uv[0], uv[1] + e) - f(uv[0], uv[1] - e)) / 2]
    return np.sign(np.linalg.det(J))

def hypotheses(det, plan, shape):
    """соответствия прямых для двух гипотез конца поля; линии в координатах полного кадра"""
    S = SCALE; L = plan["length"]
    def to_full(l): return np.array([l[0] * S, l[1] * S, l[2]])     # l·(x·S) = 0 для точек полного кадра
    hyps = []
    for end in (0, 1):
        corr = []
        for cls, ls in det.items():
            l = ls[0]["line"]
            if cls == "touch_far": corr.append((to_full(l), plan_line_eq(plan, "far_touch"), cls))
            elif cls == "touch_near": corr.append((to_full(l), plan_line_eq(plan, "near_touch"), cls))
            elif cls == "half": corr.append((to_full(l), plan_line_eq(plan, "half"), cls))
            elif cls == "goal": corr.append((to_full(l), plan_line_eq(plan, "goal%d" % end), cls))
            elif cls == "box_f": corr.append((to_full(l), plan_line_eq(plan, "box%d_f" % end), cls))
            elif cls == "box_l": corr.append((to_full(l), plan_line_eq(plan, "box%d_l" % end), cls))
            elif cls == "box_r": corr.append((to_full(l), plan_line_eq(plan, "box%d_r" % end), cls))
        hyps.append((end, corr))
    return hyps

def solve_dlt(det, plan, shape):
    """грубая матрица по прямым; None, если прямых мало. Возвращает (H, end, corr_names)"""
    h, w = shape[:2]; best = None
    for end, corr in hypotheses(det, plan, shape):
        dirs = {c for _, _, c in corr}
        longi = dirs & {"touch_far", "touch_near", "box_l", "box_r"}; trans = dirs & {"goal", "box_f", "half"}
        if len(corr) < 4 or not longi or not trans: continue
        # нормировка координат кадра для обусловленности: x' = (x - w/2)/w
        T = np.array([[1 / w, 0, -0.5], [0, 1 / w, -0.5 * h / w], [0, 0, 1]]); Ti = np.linalg.inv(T)
        cn = [(Ti.T @ l, Lp) for l, Lp, _ in corr]    # прямая в нормированных координатах: l' = T^{-T} l
        Hn = dlt_lines(cn)
        if Hn is None: continue
        H = Hn @ T
        # невязка: расстояние от концов найденных прямых до проекции линий плана (в метрах, по точкам на прямых кадра)
        res = []
        for l, Lp, c in corr:
            # две точки на прямой кадра внутри кадра
            n = l[:2] / np.linalg.norm(l[:2]); c0 = l[2] / np.linalg.norm(l[:2]); p0 = -c0 * n; d = np.array([-n[1], n[0]])
            for t in (-w * 0.3, w * 0.3):
                q = H @ np.r_[p0 + t * d, 1.0]; q = q[:2] / q[2]; res.append(abs(Lp[:2] @ q + Lp[2]))
        res = float(np.median(res)); sg = jac_sign(H, (w / 2, h * 0.7))
        cand = (sg < 0, res, end, H, [c for _, _, c in corr])
        if best is None or cand[:2] < best[:2]: best = cand
    if best is None: return None
    return best[3], best[2], best[4], best[1], best[0]

def class_dist(prob, thr=0.5, cap=30.0):
    """карта расстояний до пикселей каждого класса (в координатах 960×540)"""
    pred = prob.argmax(0); D = {}
    for c in range(1, len(CLASSES)):
        m = ((pred == c) & (prob[c] > thr)).astype(np.uint8)
        D[c] = np.minimum(cv2.distanceTransform(1 - m, cv2.DIST_L2, 5), cap).astype(np.float32) if m.any() else np.full(m.shape, cap, np.float32)
    return D

def refine_cls(H_img2plan, D, plan, shape, prior_m=3.0, prior_w=2.0, rounds=((30.0, 3), (10.0, 3), (4.0, 2))):
    """уточнение матрицы по картам расстояний классов; возвращает H, качество"""
    pts, idx, names = sample_lines(plan, 0.25, circle=False); cls = np.array([LINE_CLASS[n] for n in names])[idx]
    Hp2i0 = np.linalg.inv(H_img2plan); Hp2i0 /= Hp2i0[2, 2]; sg = front_sign(Hp2i0, plan)
    S = np.diag([SCALE, SCALE, 1.0]); h, w = shape[:2]
    anc_img = np.array([[w * .25, h * .35], [w * .75, h * .35], [w * .75, h * .9], [w * .25, h * .9], [w * .5, h * .6]])
    anc0 = (H_img2plan @ np.c_[anc_img, np.ones(5)].T).T; anc0 = anc0[:, :2] / anc0[:, 2:3]
    def res_fn(x, cap):
        Hm = np.r_[x, 1.0].reshape(3, 3); uv, wq = project(S @ Hm, pts)
        ok = (wq * sg > 1e-6) & (uv[:, 0] >= 1) & (uv[:, 0] < WW - 2) & (uv[:, 1] >= 1) & (uv[:, 1] < WH - 2)
        d = np.full(len(pts), cap, np.float32)
        for c in np.unique(cls[ok]):
            mm = ok & (cls == c); d[mm] = map_coordinates(D[c], [uv[mm, 1], uv[mm, 0]], order=1, mode="nearest")
        Hi = np.linalg.inv(Hm); q = (Hi @ np.c_[anc_img, np.ones(5)].T).T; q = q[:, :2] / q[:, 2:3]
        return np.r_[np.minimum(d, cap) / cap, np.hypot(*(q - anc0).T) / prior_m * prior_w]
    x = Hp2i0.ravel()[:8].copy()
    for cap, n in rounds:
        for _ in range(n): x = least_squares(res_fn, x, args=(cap,), loss="soft_l1", f_scale=0.3, max_nfev=60, method="trf", x_scale="jac").x
    Hp2i = np.r_[x, 1.0].reshape(3, 3); Hi2p = np.linalg.inv(Hp2i); Hi2p /= Hi2p[2, 2]
    return Hi2p, quality_cls(Hi2p, D, plan, shape)

def quality_cls(H_img2plan, D, plan, shape, tol=2.5):
    pts, idx, names = sample_lines(plan, 0.25, circle=False); cls = np.array([LINE_CLASS[n] for n in names])[idx]
    Hp2i = np.linalg.inv(H_img2plan); sg = front_sign(Hp2i, plan); S = np.diag([SCALE, SCALE, 1.0])
    uv, wq = project(S @ Hp2i, pts)
    ok = (wq * sg > 1e-6) & (uv[:, 0] >= 1) & (uv[:, 0] < WW - 2) & (uv[:, 1] >= 1) & (uv[:, 1] < WH - 2)
    if ok.sum() < 8: return {"coverage": 0.0, "lines": 0, "per_line": {}, "longi": 0, "trans": 0, "visible": int(ok.sum())}
    d = np.full(len(pts), 99.0, np.float32)
    for c in np.unique(cls[ok]):
        mm = ok & (cls == c); d[mm] = map_coordinates(D[c], [uv[mm, 1], uv[mm, 0]], order=1, mode="nearest")
    hit = d < tol; per = {}
    for k, n in enumerate(names):
        m = ok & (idx == k)
        if m.sum() >= 6: per[n] = float(hit[m].mean())
    good = [n for n, v in per.items() if v >= 0.6]
    longi = [n for n in good if "touch" in n or n.endswith("_l") or n.endswith("_r")]
    trans = [n for n in good if n.startswith("goal") or n == "half" or n.endswith("_f")]
    return {"coverage": float(hit[ok].mean()), "lines": len(good), "per_line": per, "longi": len(longi), "trans": len(trans), "visible": int(ok.sum()), "good": good}

def fine_dist(prob, img, thr=0.5, gate=9, cap=40.0):
    """карта расстояний до линий: пиксели любой линии по сети (960×540), внутри них — точные светлые
    штрихи top-hat полного кадра (сеть говорит «где», top-hat — «точно где»). Если штрихов внутри
    маски мало, остаётся маска сети"""
    pred = prob.argmax(0); conf = prob.max(0); m = ((pred > 0) & (conf > thr)).astype(np.uint8)
    if m.sum() < 30: return dist_map(m, cap), m
    g = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (gate, gate)))
    raw = line_mask(img, k=15, thr=20)
    raw = (cv2.resize(raw.astype(np.float32), (WW, WH), interpolation=cv2.INTER_AREA) > 0.1).astype(np.uint8) & g
    fine = raw if raw.sum() >= 0.3 * m.sum() else m
    return dist_map(fine, cap), fine

def sanity(H_img2plan, plan, shape):
    """геометрическая правдоподобность: якобиан > 0 (камера сбоку над полем), низ кадра лежит
    у поля, ширина низа кадра в метрах разумна, линии плана в кадре не слипаются"""
    h, w = shape[:2]; out = {"det_ok": False, "span_m": None, "pos_ok": False, "sep_px": None}
    try:
        out["det_ok"] = jac_sign(H_img2plan, (w / 2, h * 0.8)) > 0
        q = (H_img2plan @ np.array([[w * 0.15, h * 0.9, 1], [w * 0.85, h * 0.9, 1], [w / 2, h * 0.9, 1]]).T).T; q = q[:, :2] / q[:, 2:3]
        out["span_m"] = float(np.hypot(*(q[0] - q[1]))); W, L = plan["width"], plan["length"]
        out["pos_ok"] = bool(-12 <= q[2, 0] <= W + 12 and -12 <= q[2, 1] <= L + 12)
        # камера стоит у ближней боковой (x = W) на средней линии: точка ниже в кадре ближе к камере.
        # Это снимает неоднозначность поворота плана на 180° (поле симметрично)
        cam = np.array([W + 4.0, plan["half"]]); out["cam_ok"] = False
        for ym in (0.7, 0.8, 0.55):
            pm = H_img2plan @ np.array([w / 2, h * ym, 1.0]); pb = H_img2plan @ np.array([w / 2, h * 0.97, 1.0])
            if abs(pm[2]) < 1e-9 or abs(pb[2]) < 1e-9 or np.sign(pm[2]) != np.sign(pb[2]): continue
            pm = pm[:2] / pm[2]; pb = pb[:2] / pb[2]; v = pb - pm; c = cam - pb
            if np.linalg.norm(v) < 1e-6 or np.linalg.norm(c) < 1e-6: continue
            # движение вниз по кадру = к камере: угол между (низ − середина) и (камера − низ) < 70°
            out["cam_ok"] = bool(v @ c / np.linalg.norm(v) / np.linalg.norm(c) > np.cos(np.radians(70))); break
        pts, idx, names = sample_lines(plan, 0.5, circle=False); Hp2i = np.linalg.inv(H_img2plan); sg = front_sign(Hp2i, plan, shape)
        uv, wq = project(Hp2i, pts); ok = (wq * sg > 1e-6) & (uv[:, 0] >= 0) & (uv[:, 0] < w) & (uv[:, 1] >= 0) & (uv[:, 1] < h)
        seps = []
        for k in np.unique(idx[ok]):
            a = uv[ok & (idx == k)]; b = uv[ok & (idx != k)]
            if len(a) >= 4 and len(b) >= 4:
                d = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1)).min(1); seps.append(float(np.median(d)))
        out["sep_px"] = float(min(seps)) if seps else None
    except Exception: return out
    out["ok"] = bool(out["det_ok"] and out["pos_ok"] and out.get("cam_ok") and out["span_m"] is not None and 2.5 <= out["span_m"] <= 45 and (out["sep_px"] is None or out["sep_px"] >= 12))
    return out

def calibrate(prob, plan, shape, img=None, H_inits=(), thr=0.5, loo=True, classfree=True):
    """карты классов (+ кадр для точной маски) → матрица. H_inits: [(имя, H кадр→план)] — подсказки
    (сверка с опорами, соседний кадр). Кандидаты: DLT по классам линий (и варианты без одной линии),
    подсказки; каждый уточняется по общей карте линий, берётся лучший по покрытию.
    Возвращает dict: H_img2plan, src, quality, ok, det"""
    det = detect_lines(prob, thr)
    if img is not None: dist, fine = fine_dist(prob, img, thr)
    else:
        pred = prob.argmax(0); conf = prob.max(0); fine = ((pred > 0) & (conf > thr)).astype(np.uint8); dist = dist_map(fine)
    cands = []
    r = solve_dlt(det, plan, shape)
    if r is not None and not r[4] and r[3] < 3.0: cands.append(("dlt", r[0]))
    if loo and len(det) >= 5:
        for drop in list(det):
            d2 = {k: v for k, v in det.items() if k != drop}; r2 = solve_dlt(d2, plan, shape)
            if r2 is not None and not r2[4] and r2[3] < 3.0: cands.append(("dlt-%s" % drop, r2[0]))
    weights = {}
    for it in H_inits:
        cands.append((it[0], it[1]))
        if len(it) > 2: weights[it[0]] = it[2]
    if classfree and (not cands or classfree == "always"): cands += classfree_candidates(fine, plan, shape, dist)
    best = None
    for src, H0 in cands:
        try: H, q = refine_bin(H0, dist, plan, shape, SCALE, circle=False)
        except Exception: continue
        if not np.all(np.isfinite(H)): continue
        sn = sanity(H, plan, shape); q["sanity"] = sn
        okq = sn.get("ok", False) and q["lines"] >= 3 and q["longi"] >= 1 and q["trans"] >= 1 and q["coverage"] >= 0.6
        # слабый ярус: сверка с опорой площадки дала много совпадений (≥ 60 SIFT-инлаеров: по 29 ручным
        # опорам это ошибка ~0,3–0,6 м), геометрия правдоподобна, линии подтверждают хотя бы частично
        weak = (not okq) and sn.get("ok", False) and weights.get(src, 0) >= 60 and q["coverage"] >= 0.35 and q["lines"] >= 2
        q["tier"] = "strong" if okq else ("weak" if weak else "none")
        score = (okq or weak, okq, sn.get("ok", False), q["lines"], round(q["coverage"], 2))
        if best is None or score > best[0]: best = (score, src, H, q)
    if best is None: return None
    score, src, H, q = best
    return {"H_img2plan": H, "src": src, "quality": q, "ok": bool(score[0]), "tier": q["tier"], "det": {k: len(v) for k, v in det.items()}, "fine": fine}

def draw_plan(img, H_img2plan, plan, col=(0, 255, 255), thick=2):
    """разметка плана на кадре (любого размера — матрица для полного 1920×1080)"""
    h, w = img.shape[:2]; s = w / 1920.0
    Hp2i = np.diag([s, s, 1.0]) @ np.linalg.inv(H_img2plan); sg = front_sign(Hp2i, plan)
    for name, a, b in plan_lines(plan, circle=False):
        a, b = np.array(a, float), np.array(b, float); pts = a + np.linspace(0, 1, 60)[:, None] * (b - a)
        uv, wq = project(Hp2i, pts); ok = (wq * sg > 1e-6) & (np.abs(uv[:, 0]) < 4 * w) & (np.abs(uv[:, 1]) < 4 * h)
        run = []
        for (u, v), o in zip(uv, ok):
            if o: run.append((int(u), int(v)))
            else:
                if len(run) > 1: cv2.polylines(img, [np.array(run, np.int32)], False, col, thick, cv2.LINE_AA)
                run = []
        if len(run) > 1: cv2.polylines(img, [np.array(run, np.int32)], False, col, thick, cv2.LINE_AA)
    return img

# ---------- решатель без классов: прямые из общей маски линий, семейства по направлению,
# перебор соответствий с сохранением порядка (продольные: сверху вниз = x плана по возрастанию;
# поперечные: монотонно по y плана в одну из двух сторон), DLT + правдоподобность + покрытие
from itertools import combinations
def classfree_candidates(fine, plan, shape, dist, max_lines=7, top=6):
    """семейства прямых — по разрыву в углах (в кадре параллельные линии плана почти параллельны);
    какое семейство — линии x=const (боковые, стороны штрафной), а какое y=const (ворота, передняя
    штрафной, средняя) — перебираем; порядок внутри семейства (по положению вдоль общей нормали)
    соответствует порядку на плане в одну из двух сторон"""
    ys, xs = np.nonzero(fine); pts = np.c_[xs, ys].astype(np.float64)
    if len(pts) < 100: return []
    ls = [l for l in ransac_lines(pts, max_lines=max_lines, min_pts=60, tol=2.0) if l["spread"] >= 50]
    if len(ls) < 4: return []
    h, w = shape[:2]; S = SCALE
    # семейства по точке схода: параллельные линии плана в кадре сходятся в одну точку (или
    # параллельны). Перебираем пары прямых → точка схода → сколько прямых проходят через неё
    # (угол между прямой и направлением из её середины в точку схода < 2.5°)
    def vp_family(idx):
        best = []
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                la, lb = ls[idx[a]]["line"], ls[idx[b]]["line"]; vp = np.cross(la, lb)
                members = []
                for i in idx:
                    l = ls[i]; d = l["dir"]
                    if abs(vp[2]) < 1e-9 * max(abs(vp[0]), abs(vp[1]), 1e-12): v = vp[:2] / np.linalg.norm(vp[:2])      # точка схода на бесконечности
                    else:
                        P = vp[:2] / vp[2]; v = P - l["mu"]; n = np.linalg.norm(v)
                        if n < 1e-6: members.append(i); continue
                        v = v / n
                    if abs(np.degrees(np.arccos(np.clip(abs(v @ d), 0, 1)))) < 2.5: members.append(i)
                if len(members) > len(best): best = members
        return best
    idx = list(range(len(ls))); famA = vp_family(idx)
    rest = [i for i in idx if i not in famA]; famB = vp_family(rest) if len(rest) >= 2 else rest
    if len(famA) < 1 or len(famB) < 1: return []
    def to_full(l): return np.array([l[0] * S, l[1] * S, l[2]])
    def ordered(f):
        nrm = np.mean([ls[i]["line"][:2] * np.sign(ls[i]["line"][0] + 1e-9) for i in f], 0)     # общая нормаль
        return sorted(f, key=lambda i: -ls[i]["line"][2] * np.sign(ls[i]["line"][:2] @ nrm) / np.linalg.norm(ls[i]["line"][:2]))
    famA, famB = ordered(famA)[:4], ordered(famB)[:4]
    W, L, HL, bd = plan["width"], plan["length"], plan["half"], plan["box_d"]; x0, x1 = W / 2 - plan["box_w"] / 2, W / 2 + plan["box_w"] / 2
    LP = [0.0, x0, x1, W]; TP = [0.0, bd, HL, L - bd, L]
    T = np.array([[1 / w, 0, -0.5], [0, 1 / w, -0.5 * h / w], [0, 0, 1]]); Ti = np.linalg.inv(T)
    cands = []; seen = 0
    for Lf, Tf in ((famA, famB), (famB, famA)):
        for kl in range(1, len(Lf) + 1):
            for ldet in combinations(Lf, kl):
                for lsub in combinations(range(4), kl):
                    for revL in (False, True):
                        cl = [(Ti.T @ to_full(ls[li]["line"]), np.array([1.0, 0.0, -LP[pi]])) for li, pi in zip(ldet[::-1] if revL else ldet, lsub)]
                        for kt in range(1, len(Tf) + 1):
                            if kl + kt < 4: continue
                            for tdet in combinations(Tf, kt):
                                for tsub in combinations(range(5), kt):
                                    for revT in (False, True):
                                        corr = cl + [(Ti.T @ to_full(ls[ti]["line"]), np.array([0.0, 1.0, -TP[pi]])) for ti, pi in zip(tdet[::-1] if revT else tdet, tsub)]
                                        Hn = dlt_lines(corr); seen += 1
                                        if Hn is None: continue
                                        H = Hn @ T
                                        if not np.all(np.isfinite(H)): continue
                                        sn = sanity(H, plan, shape)
                                        if not sn.get("ok"): continue
                                        q = quality_bin(np.linalg.inv(H), dist, plan, SCALE, front_sign(np.linalg.inv(H), plan, shape), circle=False, tol=6.0)
                                        cands.append((q["coverage"], q["lines"], H, (kl, lsub, kt, tsub, revL, revT)))
    cands.sort(key=lambda c: (-c[1], -c[0]))
    return [("free%s" % str(c[3]).replace(" ", ""), c[2]) for c in cands[:top]]
