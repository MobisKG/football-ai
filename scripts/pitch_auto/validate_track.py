"""Ошибка привязки между опорами: на отложенных опорах (--holdout) сравнивает матрицу, которую
дало протягивание, с матрицей, построенной по самому кадру. Мера — расстояние на плане, м,
между проекциями одних и тех же точек нижней части кадра (там поле).

    python validate_track.py <slug>   → results/pitch/<slug>/validation.json
"""
import os, sys, json, glob
import numpy as np, cv2
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
slug = sys.argv[1]; D = os.path.join(ROOT, "results/pitch", slug)
T = json.load(open(os.path.join(D, "track_auto.json"))); held = json.load(open(os.path.join(D, "heldout.json")))
byi = {f["i"]: f for f in T["frames"]}; FPS = T["source_fps"]; STEP = T["step"]
W, L = T["plan"]["width"], T["plan"]["length"]
grid = np.array([[x, y, 1.0] for x in np.linspace(150, 1770, 7) for y in np.linspace(560, 1040, 5)])
def proj(H, P):
    q = (H @ P.T).T; return q[:, :2] / q[:, 2:3]
errs = []
for sec in held:
    a = json.load(open(os.path.join(D, "auto", "frame_t%04d.json" % sec)))
    i = int(round(sec * FPS)) // STEP * STEP
    f = byi.get(i) or byi.get(min(byi, key=lambda k: abs(k - i)))
    Ha = np.array(a["H_img2plan"], float); Ht = np.array(f["H_img2plan"], float).reshape(3, 3)
    pa = proj(Ha, grid); pt = proj(Ht, grid)
    ok = (pa[:, 0] > -2) & (pa[:, 0] < W + 2) & (pa[:, 1] > -2) & (pa[:, 1] < L + 2)
    if ok.sum() < 5: continue
    errs.append((sec, float(np.mean(np.hypot(*(pa[ok] - pt[ok]).T)))))
e = np.array([x[1] for x in errs]) if errs else np.array([np.nan])
out = {"heldout": len(held), "measured": len(errs), "median_m": round(float(np.median(e)), 2), "p90_m": round(float(np.percentile(e, 90)), 2),
       "mean_m": round(float(np.mean(e)), 2), "share_under_1m": round(float((e < 1.0).mean()), 2), "rows": errs}
json.dump(out, open(os.path.join(D, "validation.json"), "w"), indent=1)
print(slug, "ошибка между опорами: медиана %.2f м, 90%% — %.2f м, доля < 1 м: %.0f%% (%d кадров)" % (out["median_m"], out["p90_m"], 100 * out["share_under_1m"], len(errs)))
