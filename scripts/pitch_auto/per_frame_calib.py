"""Разметка поля на каждом кадре (без протягивания между опорами) — проверка способа.

    python per_frame_calib.py <slug> --start 600 --dur 600 [--fps 5] [--out results/pitch/<slug>/perframe]

Для каждого кадра (fps раз в секунду): сеть линий → матрица кадр→план. Начальная матрица —
с соседнего удачного кадра; если его нет — DLT по линиям и перебор без классов; если модель
не справляется 2 с подряд — сверка с галереей площадки. Потом: пропуски ≤ 2 с заполняются
по соседям, ряд сглаживается медианой ±0,4 с. Проверка:
  • отложенные кадры (каждый 10-й удачный): расхождение восстановленной и прямой разметки, м;
  • ручные опоры в отрезке (если есть): ошибка ориентиров, м (как в задаче: < 0,7 ср., < 1,5 макс.).
"""
import os, sys, json, time
import numpy as np, cv2
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from core import plan_of, FIELD_PITCH, ROOT, load_manual, landmark_error
from lines_net import load_net, predict
from solve import calibrate
from retrieval import Gallery

def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
slug = sys.argv[1]; START = float(opt("--start", 0)); DUR = float(opt("--dur", 600)); FPS = float(opt("--fps", 5))
OUT = opt("--out") or os.path.join(ROOT, "results/pitch", slug, "perframe_%d_%d" % (START, START + DUR)); os.makedirs(OUT, exist_ok=True)
jobs = {j["slug"]: j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json")))}
plan = plan_of(FIELD_PITCH.get(jobs[slug]["field"], "lower"))
video = next(p for p in (os.path.join(ROOT, "results/pitch/videos", slug + ".mp4"), os.path.join(ROOT, "videos", slug + ".mp4")) if os.path.exists(p))
def log(*a): print(time.strftime("%H:%M:%S"), slug, *a, flush=True)

GRID = np.array([[x, y, 1.0] for x in np.linspace(150, 1770, 7) for y in np.linspace(560, 1040, 5)])
def to_plan(H):
    q = (H @ GRID.T).T; return q[:, :2] / q[:, 2:3]

# ---------- 1. разметка каждого кадра
FROM = opt("--from-frames")
if FROM:
    cap = cv2.VideoCapture(video); vfps = cap.get(5); Fd = json.load(open(FROM))
    frames = Fd["frames"]; FPS = Fd["fps"]; START = Fd["start"]; DUR = Fd["dur"]; elapsed = Fd["sec_per_frame"] * len(frames)
net, dev = (None, None) if FROM else load_net(opt("--weights", os.path.join(ROOT, "weights/pitch_lines_v2.pt"))); gal = None
if not FROM:
  cap = cv2.VideoCapture(video); vfps = cap.get(5)
  if DUR <= 0: DUR = cap.get(7) / vfps - START
if not FROM:
    frames = []; H_prev = None; miss = 0; t0 = time.time(); n = int(DUR * FPS)
    cap.set(cv2.CAP_PROP_POS_MSEC, START * 1000)
    next_t = START
    while next_t < START + DUR:
        ok, im = cap.read()
        if not ok: break
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if t + 1e-3 < next_t: continue
        next_t += 1.0 / FPS
        if im.shape[1] != 1920: im = cv2.resize(im, (1920, 1080))
        prob = predict(net, im, dev); r = None
        if H_prev is not None:
            r = calibrate(prob, plan, im.shape, img=im, H_inits=[("prev", H_prev)], loo=False, classfree=False)
            if r is not None and not r["ok"]: r = None
        if r is None:
            r = calibrate(prob, plan, im.shape, img=im, H_inits=[], loo=True, classfree=True)
            if (r is None or not r["ok"]) and miss >= int(2 * FPS):
                if gal is None: gal = Gallery(jobs[slug]["field"]); log("галерея площадки: %d кадров" % len(gal.items))
                inits = [("gal", Hg, inl) for Hg, inl, g, s in gal.query(im, top=2)]
                if inits: r = calibrate(prob, plan, im.shape, img=im, H_inits=inits, loo=False, classfree=False)
        good = r is not None and r["ok"]
        frames.append({"t": round(t, 3), "ok": bool(good), "H": r["H_img2plan"].tolist() if good else None,
                       "cov": round(float(r["quality"]["coverage"]), 3) if r is not None else None, "src": r["src"] if r is not None else None})
        if good: H_prev = r["H_img2plan"]; miss = 0
        else: miss += 1
        k = len(frames)
        if k % 250 == 0:
            el = time.time() - t0
            log("кадров %d/%d, удачных %.0f%%, %.2f с/кадр" % (k, n, 100 * np.mean([f["ok"] for f in frames]), el / k))
    elapsed = time.time() - t0
    json.dump({"slug": slug, "start": START, "dur": DUR, "fps": FPS, "frames": frames, "sec_per_frame": elapsed / max(1, len(frames))}, open(os.path.join(OUT, "frames.json"), "w"))

# ---------- 2. восстановление ряда: пропуски ≤ 2 с, сглаживание медианой ±0,4 с
ts = np.array([f["t"] for f in frames]); okm = np.array([f["ok"] for f in frames])
P = np.full((len(frames), len(GRID), 2), np.nan)
for i, f in enumerate(frames):
    if f["ok"]: P[i] = to_plan(np.array(f["H"]))
# выбросы: удачный кадр, чья разметка расходится с медианой соседей (±1 с) больше чем на 1 м, не используется
W1 = max(2, int(round(1.0 * FPS))); outl = 0
okP = ~np.isnan(P[:, 0, 0])
for i in np.flatnonzero(okP):
    nb = [j for j in range(max(0, i - W1), min(len(P), i + W1 + 1)) if j != i and okP[j]]
    if len(nb) >= 3:
        med = np.median(P[nb], axis=0)
        if np.mean(np.hypot(*(P[i] - med).T)) > 1.0: P[i] = np.nan; outl += 1
okm = ~np.isnan(P[:, 0, 0])
rng = np.random.default_rng(0); ok_idx = np.flatnonzero(okm); held = set(ok_idx[5::10].tolist())
def series(exclude):
    Q = P.copy()
    for i in exclude: Q[i] = np.nan
    have = np.flatnonzero(~np.isnan(Q[:, 0, 0]))
    R = np.full_like(Q, np.nan)
    for i in range(len(Q)):
        if not np.isnan(Q[i, 0, 0]): R[i] = Q[i]; continue
        prv = have[have < i]; nxt = have[have > i]
        if len(prv) and len(nxt) and ts[nxt[0]] - ts[prv[-1]] <= 2.0:
            a, b = prv[-1], nxt[0]; w = (ts[i] - ts[a]) / (ts[b] - ts[a]); R[i] = (1 - w) * Q[a] + w * Q[b]
    W = max(1, int(round(0.4 * FPS))); S = np.full_like(R, np.nan)
    for i in range(len(R)):
        win = R[max(0, i - W): i + W + 1]; win = win[~np.isnan(win[:, 0, 0])]
        if len(win): S[i] = np.median(win, axis=0)
    return S
S_hold = series(held)
errs = [float(np.mean(np.hypot(*(S_hold[i] - P[i]).T))) for i in held if not np.isnan(S_hold[i, 0, 0])]
S = series(set())
covered = float(np.mean(~np.isnan(S[:, 0, 0])))
res = {"frames": len(frames), "outliers_dropped": int(outl), "model_ok_share": round(float(okm.mean()), 3), "covered_share": round(covered, 3),
       "sec_per_frame": round(elapsed / max(1, len(frames)), 3),
       "holdout": {"n": len(errs), "median_m": round(float(np.median(errs)), 2) if errs else None, "p90_m": round(float(np.percentile(errs, 90)), 2) if errs else None,
                   "share_under_1m": round(float(np.mean(np.array(errs) < 1.0)), 2) if errs else None}}
# ---------- 3. ручные опоры в отрезке
man = [m for m in load_manual() if m["game"] == slug and START <= m["sec"] <= START + DUR]
rows = []
for m in man:
    i = int(np.argmin(np.abs(ts - m["sec"])))
    if abs(ts[i] - m["sec"]) > 0.25 or np.isnan(S[i, 0, 0]): rows.append({"sec": m["sec"], "error": "нет разметки рядом"}); continue
    H, _ = cv2.findHomography(GRID[:, :2], S[i], 0)
    e = landmark_error(H, m["points"], plan); rows.append({"sec": m["sec"], "mean_m": round(float(np.mean(list(e.values()))), 2), "max_m": round(float(max(e.values())), 2), "points": len(e), "model_ok_here": bool(okm[i])})
res["manual"] = rows
if "--save-track" in sys.argv:
    # привязка для to_metres.py: матрица на каждый кадр позиций (шаг под 15 к/с); где разметки нет — src="gap"
    pos_fps = float(jobs[slug]["pos_fps"]); step = max(1, round(vfps / pos_fps)); N = int(cap.get(7))
    have = ~np.isnan(S[:, 0, 0]); tr = []
    for i in range(0, N, step):
        tt = i / vfps; k = int(np.searchsorted(ts, tt))
        cand = [c for c in (k - 1, k) if 0 <= c < len(ts) and have[c] and abs(ts[c] - tt) <= 0.25]
        if cand:
            if len(cand) == 2:
                a, b = cand; w = (tt - ts[a]) / max(1e-6, ts[b] - ts[a]); Q = (1 - w) * S[a] + w * S[b]
            else: Q = S[cand[0]]
            H, _ = cv2.findHomography(GRID[:, :2], Q, 0); tr.append({"i": i, "H_img2plan": (H / H[2, 2]).ravel().round(8).tolist(), "src": "pf", "inl": 0})
        else:
            tr.append({"i": i, "H_img2plan": None, "src": "gap", "inl": 0})
    last = next((f["H_img2plan"] for f in tr if f["H_img2plan"]), None)
    for f in tr:
        if f["H_img2plan"] is None: f["H_img2plan"] = last
        else: last = f["H_img2plan"]
    json.dump({"fps": vfps / step, "step": step, "source_fps": vfps, "plan": {"width": plan["width"], "length": plan["length"]}, "frames": tr},
              open(os.path.join(ROOT, "results/pitch", slug, "track_perframe.json"), "w"))
    res["track_gap_share"] = round(float(np.mean([f["src"] == "gap" for f in tr])), 3)
json.dump(res, open(os.path.join(OUT, "result.json"), "w"), ensure_ascii=False, indent=1)
log("ИТОГ", json.dumps(res, ensure_ascii=False))
