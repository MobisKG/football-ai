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

class Gallery:
    def __init__(self, field, exclude_games=(), extra=(), nfeat=4000):
        self.sift = cv2.SIFT_create(nfeat); self.bf = cv2.BFMatcher(cv2.NORM_L2); self.items = []
        for m in load_manual():
            if m["H"] is None or m["field"] != field or m["game"] in exclude_games: continue
            self.add(m["image"], m["H"], m["game"], m["sec"])
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
