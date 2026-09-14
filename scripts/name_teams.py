"""Название команды по цвету формы, без повторного прогона.

Усреднять цвет целиком бесполезно: и тёмно-синяя, и бело-красная форма в
среднем дают серый, потому что в вырезке много газона, кожи и тени. Смотрим на
цветной тон только тех пикселей, которые действительно окрашены, а если таких
мало — решаем по светлоте: это белая или чёрная форма.
"""
import json, os, sys, glob
import cv2, numpy as np

pos_path, crops_dir = sys.argv[1], sys.argv[2]
d = json.load(open(pos_path))
team_of = {int(k): v for k, v in d["team_of"].items()}

def jersey_pixels(path):
    im = cv2.imread(path)
    if im is None:
        return None
    h, w = im.shape[:2]
    patch = im[int(h * 0.18):int(h * 0.45), int(w * 0.28):int(w * 0.72)]
    return patch.reshape(-1, 3) if patch.size else None

def describe(pix):
    hsv = cv2.cvtColor(pix.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    hue, sat, val = hsv[:, 0].astype(float), hsv[:, 1].astype(float), hsv[:, 2].astype(float)
    coloured = sat > 70
    share = coloured.mean()
    if share < 0.12:                       # почти нет цвета: белое или чёрное
        return ("белые" if np.median(val) > 140 else "чёрные"), share, None
    hh = np.median(hue[coloured]) * 2      # OpenCV хранит тон в диапазоне 0..179
    for lo, hi, name in [(0, 20, "красные"), (20, 45, "оранжевые"), (45, 70, "жёлтые"),
                         (70, 160, "зелёные"), (160, 200, "голубые"),
                         (200, 260, "синие"), (260, 330, "фиолетовые"), (330, 361, "красные")]:
        if lo <= hh < hi:
            return name, share, hh
    return "серые", share, hh

acc = {0: [], 1: []}
for f in glob.glob(os.path.join(crops_dir, "t*_*.jpg")):
    tid = int(os.path.basename(f).split("_")[0][1:])
    t = team_of.get(tid, -1)
    if t in (0, 1):
        p = jersey_pixels(f)
        if p is not None:
            acc[t].append(p)

names = {}
for t, lst in acc.items():
    if not lst:
        names[t] = "команда %d" % t
        continue
    pix = np.concatenate(lst)
    name, share, hue = describe(pix)
    names[t] = name
    print("команда %d: вырезок %d, окрашенных пикселей %.0f %%, тон %s -> %s"
          % (t, len(lst), 100 * share, "%.0f" % hue if hue is not None else "нет", name))

if names.get(0) == names.get(1):
    names[1] += " (вторые)"
d["team_names"] = {str(k): v for k, v in names.items()}
json.dump(d, open(pos_path, "w"), ensure_ascii=False)
print("названия:", names)
