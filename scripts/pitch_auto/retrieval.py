"""Грубая матрица сверкой кадра с опорами той же площадки (SIFT + RANSAC).

Оператор снимает все игры площадки с одной трибуны: фон (забор, постройки, деревья) и линии
совпадают, поэтому однородное преобразование кадр→опора, умноженное на матрицу опоры,
даёт привязку с ошибкой ~0,3–0,6 м (проверено на 29 ручных опорах между играми).
Галерея — ручные опоры (results/pitch/*/manual) и, при желании, принятые автоматические.
"""
import os, sys, json, glob
import numpy as np, cv2
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from core import load_manual, ROOT

def load_auto_for_field(field, exclude_games=(), min_cov=0.85, min_lines=5, per_game=12):
    """уверенные автоматические опоры других игр той же площадки — опоры для галереи (самонаращивание)"""
    jobs = {j["slug"]: j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json")))}
    out = []
    for slug, j in jobs.items():
        if j.get("field") != field or slug in exclude_games: continue
        rows = []
        for f in sorted(glob.glob(os.path.join(ROOT, "results/pitch", slug, "auto", "frame_t*.json"))):
            try: r = json.load(open(f))
            except Exception: continue          # файл ещё пишется другим процессом
            q = r.get("quality") or {}
            if r.get("ok") and r.get("H_img2plan") and q.get("coverage", 0) >= min_cov and q.get("lines", 0) >= min_lines and os.path.exists(r["image"]):
                rows.append((q["coverage"], r))
        rows.sort(key=lambda x: -x[0])
        # равномерно по времени игры среди лучших
        pick = [r for _, r in rows[:per_game * 3]]; pick.sort(key=lambda r: r["sec"]); step = max(1, len(pick) // per_game)
        out += [(r["image"], np.array(r["H_img2plan"]), slug, r["sec"]) for r in pick[::step][:per_game]]
    return out

class Gallery:
    def __init__(self, field, exclude_games=(), extra=(), nfeat=4000, auto=True):
        self.sift = cv2.SIFT_create(nfeat); self.bf = cv2.BFMatcher(cv2.NORM_L2); self.items = []
        for m in load_manual():
            if m["H"] is None or m["field"] != field or m["game"] in exclude_games: continue
            self.add(m["image"], m["H"], m["game"], m["sec"])
        if auto:
            for path, H, game, sec in load_auto_for_field(field, exclude_games): self.add(path, H, game, sec)
        for path, H, game, sec in extra: self.add(path, H, game, sec)
    def feats(self, img):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY); g = cv2.resize(g, (960, 540), interpolation=cv2.INTER_AREA)
        kp, d = self.sift.detectAndCompute(g, None)
        return (np.float32([k.pt for k in kp]) * (img.shape[1] / 960.0), d) if kp else (np.zeros((0, 2), np.float32), None)
    def add(self, path, H, game, sec):
        img = cv2.imread(path)
        if img is None: return
        pts, d = self.feats(img)
        if d is not None: self.items.append({"pts": pts, "d": d, "H": np.asarray(H, float), "game": game, "sec": sec})
    def query(self, img, top=3, min_inl=25):
        """[(H_img2plan, inliers, game, sec)] по убыванию совпадений"""
        pq, dq = self.feats(img)
        if dq is None: return []
        out = []
        for it in self.items:
            ms = self.bf.knnMatch(dq, it["d"], k=2); good = [m for m, n in ms if len((m, n)) == 2 and m.distance < 0.75 * n.distance]
            if len(good) < 12: continue
            src = pq[[m.queryIdx for m in good]]; dst = it["pts"][[m.trainIdx for m in good]]
            Hq, inl = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
            if Hq is None or inl is None or inl.sum() < min_inl: continue
            out.append((it["H"] @ Hq, int(inl.sum()), it["game"], it["sec"]))
        out.sort(key=lambda x: -x[1]); return out[:top]
