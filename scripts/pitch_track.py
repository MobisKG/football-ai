"""Протягивает привязку к плану поля через весь клип.

Камера на этой съёмке двигается 78 % времени, поэтому одной привязки опорного
кадра мало. Оператор снимает с одной точки и в основном поворачивает камеру, а
при чистом повороте весь кадр — и поле, и фон — переходит в следующий одним
однородным преобразованием. Его и ищем от кадра к кадру: точки фона и газона
отслеживаются оптическим потоком, игроки отбрасываются как выбросы RANSAC.

Цепочка копит ошибку, поэтому каждые REANCHOR кадров пробуем сопоставить
текущий кадр напрямую с опорным (ORB + RANSAC) и, если совпадений достаточно,
заменяем накопленную матрицу прямой.

    python pitch_track.py видео.mp4 calib_ref.json out.json [--step 2] [--ref-sec 2.0]
"""
import json, sys, time
import numpy as np, cv2

video, calib_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
def opt(n, d): return type(d)(sys.argv[sys.argv.index(n) + 1]) if n in sys.argv else d
STEP = opt("--step", 2); REF_SEC = opt("--ref-sec", 2.0)
SCALE = 0.5; REANCHOR = 15; MIN_INL = 60; MIN_ORB = 80

cal = json.load(open(calib_path))
_PW, _PL = float(cal["plan"]["width"]), float(cal["plan"]["length"])
H_ref2plan = np.array(cal["H_img2plan"], float).reshape(3, 3)

cap = cv2.VideoCapture(video)
FPS = cap.get(cv2.CAP_PROP_FPS); N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
S_ = np.diag([SCALE, SCALE, 1.0]); Si = np.linalg.inv(S_)

frames_idx = list(range(0, N, STEP))
ref_i = int(round(REF_SEC * FPS)) // STEP * STEP

def gray_small(fr):
    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    return cv2.resize(g, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)

# читаем уменьшенные серые кадры в память: 9000 × 540×960 ≈ 4,7 ГБ — много,
# поэтому храним только нужные в два прохода (вперёд и назад от опорного)
def read_small(indices):
    out = {}
    want = set(indices)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    i = 0
    while True:
        ok = cap.grab()
        if not ok: break
        if i in want:
            ok, fr = cap.retrieve()
            if ok: out[i] = gray_small(fr)
        i += 1
        if i > max(want): break
    return out

orb = cv2.ORB_create(3000)
# Особые точки — только на газоне. Больше всего точек на сетке, заборе и стене постройки
# за воротами; это вертикальная плоскость, и при съёмке с рук (оператор чуть смещается)
# преобразование по ней неверно для поля: на пилоте разметка уезжала на крышу постройки.
GROUND_MASK = "--no-ground-mask" not in sys.argv
def ground_mask(H_img2plan, shape):
    """маска уменьшенного кадра: точки, чья проекция на план внутри поля с запасом 1 м"""
    if not GROUND_MASK or H_img2plan is None: return None
    h, w = shape
    Hi = np.linalg.inv(np.asarray(H_img2plan, float).reshape(3, 3))
    M = 1.0; poly = np.array([[-M, -M], [_PW + M, -M], [_PW + M, _PL + M], [-M, _PL + M]], float)
    # густая ломаная по краю поля, отбрасываем точки за горизонтом (другой знак однородной координаты)
    dense = np.vstack([np.linspace(poly[k], poly[(k + 1) % 4], 60) for k in range(4)])
    q = (Hi @ np.c_[dense, np.ones(len(dense))].T).T
    ref = Hi @ np.array([_PW / 2, _PL / 2, 1.0])
    ok = q[:, 2] * np.sign(ref[2] if ref[2] != 0 else 1) > 1e-9
    if ok.sum() < 20: return None
    pts = (q[ok, :2] / q[ok, 2:3]) * SCALE
    pts = np.clip(pts, -4 * w, 5 * w).astype(np.int32)
    m = np.zeros((h, w), np.uint8); cv2.fillPoly(m, [cv2.convexHull(pts)], 255)
    return m if (m > 0).mean() > 0.08 else None
bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

def pair_H(g1, g2, mask=None):
    """Преобразование g1 -> g2 (в координатах полного кадра)."""
    p1 = cv2.goodFeaturesToTrack(g1, 1500, 0.01, 8, mask=mask)
    if (p1 is None or len(p1) < 30) and mask is not None:
        p1 = cv2.goodFeaturesToTrack(g1, 1500, 0.01, 8)
    if p1 is None or len(p1) < 30: return None, 0
    p2, st, _ = cv2.calcOpticalFlowPyrLK(g1, g2, p1, None, winSize=(21, 21), maxLevel=4)
    m = st.ravel() == 1
    if m.sum() < 30: return None, 0
    H, inl = cv2.findHomography(p1[m], p2[m], cv2.RANSAC, 2.0)
    if H is None: return None, 0
    return Si @ H @ S_, int(inl.sum())

ref_small = None
def orb_to_ref(g):
    kp1, d1 = orb.detectAndCompute(g, None)
    if d1 is None or ref_feat[1] is None: return None, 0
    ms = bf.match(d1, ref_feat[1])
    if len(ms) < MIN_ORB: return None, 0
    a = np.float32([kp1[m.queryIdx].pt for m in ms]); b = np.float32([ref_feat[0][m.trainIdx].pt for m in ms])
    H, inl = cv2.findHomography(a, b, cv2.RANSAC, 3.0)
    if H is None: return None, 0
    return Si @ H @ S_, int(inl.sum())

t0 = time.time()
print("кадров %d, шаг %d, опорный кадр %d" % (N, STEP, ref_i), flush=True)
small = read_small(frames_idx)
# Заголовок файла обещает 18 079 кадров, а декодируется 18 001: берём только
# реально прочитанные, иначе последний шаг падает на отсутствующем кадре.
frames_idx = sorted(small)
print("кадры прочитаны за %.0f с, реально %d" % (time.time() - t0, len(frames_idx)), flush=True)

# Опоры: основная (ближние ворота) и дополнительные через --anchor путь.json.
# Прямая сверка с ближайшей по содержанию опорой не даёт ошибке копиться на
# дальнем конце поля, где до первой опоры 43 метра.
anchors = [] if "--no-ref" in sys.argv else [(ref_i, H_ref2plan)]
for k, a in enumerate(sys.argv):
    if a == "--anchor":
        c = json.load(open(sys.argv[k + 1]))
        ai = int(c["frame_index"]) // STEP * STEP
        if ai in small:
            anchors.append((ai, np.array(c["H_img2plan"], float).reshape(3, 3)))
key = []
for ai, Ha in anchors:
    kp, d = orb.detectAndCompute(small[ai], None)
    key.append((ai, Ha, kp, d))
# --ext-anchor anchor.json: опора из другой записи того же поля (оператор снимает с одной
# трибуны, фон тот же). Номер опоры — отрицательный, в кадры видео не попадает.
import os as _os
for k, a in enumerate(sys.argv):
    if a == "--ext-anchor":
        c = json.load(open(sys.argv[k + 1]))
        im = cv2.imread(_os.path.join(_os.path.dirname(_os.path.abspath(sys.argv[k + 1])), c["small"]), cv2.IMREAD_GRAYSCALE)
        _Ha = np.array(c["H_img2plan"], float).reshape(3, 3)
        _PW, _PL = float(c["plan"]["width"]), float(c["plan"]["length"])
        kp, d = orb.detectAndCompute(im, ground_mask(_Ha, im.shape))
        key.append((-(len(key) + 1), _Ha, kp, d))
print("опор: %d (%s)" % (len(key), ", ".join("кадр %d" % k[0] for k in key)), flush=True)

def orb_to_keys(g, H_guess=None):
    m1 = ground_mask(H_guess, g.shape)
    kp1, d1 = orb.detectAndCompute(g, m1)
    if d1 is None and m1 is not None: kp1, d1 = orb.detectAndCompute(g, None)
    if d1 is None: return None, 0, None
    best = (None, 0, None)
    for ai, Ha, kp2, d2 in key:
        if d2 is None: continue
        ms = bf.match(d1, d2)
        if len(ms) < MIN_ORB: continue
        a = np.float32([kp1[m.queryIdx].pt for m in ms]); b = np.float32([kp2[m.trainIdx].pt for m in ms])
        H, inl = cv2.findHomography(a, b, cv2.RANSAC, 3.0)
        if H is None: continue
        n = int(inl.sum())
        if n > best[1]: best = (Ha @ (Si @ H @ S_), n, ai)
    return best

H_plan = {}
quality = {}
for ai, Ha, _, _ in key:
    if ai >= 0:
        H_plan[ai] = Ha; quality[ai] = {"src": "anchor", "inl": 9999}
if "--no-ref" in sys.argv:
    # опора только внешняя: ищем кадр видео, лучше всего совпадающий с какой-то опорой
    best = (None, 0, None)
    for i in frames_idx[::REANCHOR]:
        Hd, n, ai = orb_to_keys(small[i])
        if Hd is not None and n > best[1]: best = (Hd, n, i)
        if n >= 4 * MIN_ORB: break
    if best[0] is None: sys.exit("ни один кадр не совпал с внешними опорами")
    ref_i = best[2]; H_plan[ref_i] = best[0]; quality[ref_i] = {"src": "extref", "inl": best[1]}
    print("стартовый кадр %d по внешней опоре, совпадений %d" % (ref_i, best[1]), flush=True)
lost = 0; reanch = 0

for direction in (+1, -1):
    seq = [i for i in frames_idx if (i > ref_i if direction > 0 else i < ref_i)]
    if direction < 0: seq = seq[::-1]
    prev_i = ref_i; H_prev = H_plan[ref_i]
    for k, i in enumerate(seq):
        if i in H_plan and quality[i]["src"] in ("anchor", "extref"):
            prev_i, H_prev = i, H_plan[i]; continue
        Hp, inl = pair_H(small[i], small[prev_i], ground_mask(H_prev, small[i].shape))      # кадр i -> кадр prev
        H_cur = H_prev @ Hp if (Hp is not None and inl >= MIN_INL) else None
        src = "flow" if H_cur is not None else "lost"
        if k % REANCHOR == 0 or H_cur is None:
            Hd, ninl, ai = orb_to_keys(small[i], H_cur if H_cur is not None else H_prev)
            if Hd is not None and ninl >= MIN_ORB:
                H_cur = Hd; src = "orb%d" % ai; inl = ninl; reanch += 1
        if H_cur is None:
            lost += 1; H_cur = H_prev; src = "held"; inl = 0
        H_plan[i] = H_cur; quality[i] = {"src": src, "inl": int(inl)}
        prev_i, H_prev = i, H_cur
        if (k + 1) % 1000 == 0:
            print("  %s: %d кадров, потеряно %d, пересверок %d, %.0f с" %
                  ("вперёд" if direction > 0 else "назад", k + 1, lost, reanch, time.time() - t0), flush=True)

out = {"fps": FPS / STEP, "step": STEP, "source_fps": FPS, "ref_frame": ref_i,
       "anchors": [k[0] for k in key], "plan": cal["plan"], "frames": []}
for i in frames_idx:
    H = H_plan[i]
    out["frames"].append({"i": i, "H_img2plan": (H / H[2, 2]).ravel().round(8).tolist(),
                          "src": quality[i]["src"], "inl": quality[i]["inl"]})
json.dump(out, open(out_path, "w"))
from collections import Counter
print("готово: %s, источники %s, %.0f с" % (out_path, dict(Counter(q["src"][:3] for q in quality.values())), time.time() - t0))
