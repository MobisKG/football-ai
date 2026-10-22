"""Делит игроков на две команды по цвету футболок, измеренному по пикселям видео.

Запасной путь для team_from_kit.py, когда слова формы, названные моделью, команды
не делят. 22.10 так сломались две игры: «Карпинка vs Манас Талас» (тёмно-синие
против бело-голубых, модель обе формы называла «синий и белый», одна команда
вышла почти пустой) и «Реал-91 vs ЭлКат» (белую форму ЭлКата модель тысячи раз
называла «синий и белый», и команды перемешались). Пиксели такие формы различают
легко: синий против белого, тёмный против светлого.

Раз в секунду у каждого игрока берётся область груди внутри рамки (без газона),
по ней доли насыщенного синего, белого, тёмного и тёмно-синего. Треки делятся
k-средними на две группы. Команду получает только трек, у которого цвет замерен
хотя бы в MIN_N кадрах и ясно ближе к одной группе, остальные — вне команд:
лучше меньше данных, чем чужие игроки в команде.

    python kit_colour_split.py pos.json video.mp4 out_pos.json [--min-n 5] [--margin 0.2]

Код выхода 2 — цвета групп почти не различаются (деление по цвету не годится),
out_pos.json тогда не пишется.
"""
import json, subprocess, sys
import cv2, numpy as np

pos_path, video, out_path = sys.argv[1:4]
opt = lambda k, d: type(d)(sys.argv[sys.argv.index(k) + 1]) if k in sys.argv else d
MIN_N, MARGIN = opt("--min-n", 5), opt("--margin", 0.2)
MIN_GAP = 0.3            # расстояние центров групп: 22.10 было 0,45 и 0,77; меньше — цвета не различаются

pos = json.load(open(pos_path))
cap = cv2.VideoCapture(video)
vfps, W, H = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
cap.release()
ratio = max(1, round(vfps / pos["fps"]))            # кадр pos — каждый ratio-й кадр видео (шаг разбора)
every = max(1, round(pos["fps"]))                   # ≈ раз в секунду
cmd = ["ffmpeg", "-v", "error", "-i", video, "-vf", "select=not(mod(n\\,%d))" % (ratio * every),
       "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10 ** 8)
feats, k = {}, 0
while True:
    buf = p.stdout.read(W * H * 3)
    if len(buf) < W * H * 3: break
    i = k * every; k += 1
    if i >= len(pos["frames"]): break
    boxes = pos["frames"][i].get("boxes") or {}
    if not boxes: continue
    hsv = cv2.cvtColor(np.frombuffer(buf, np.uint8).reshape(H, W, 3), cv2.COLOR_BGR2HSV)
    for tid, (x1, y1, x2, y2) in boxes.items():
        w, h = x2 - x1, y2 - y1
        if h < 60: continue                          # слишком далеко: цвет не различить
        a, b = int(x1 + 0.3 * w), int(x2 - 0.3 * w); c, d = int(y1 + 0.18 * h), int(y1 + 0.45 * h)
        if b - a < 4 or d - c < 4: continue
        r = hsv[max(c, 0):d, max(a, 0):b].reshape(-1, 3).astype(int)
        r = r[~((r[:, 0] > 35) & (r[:, 0] < 90) & (r[:, 1] > 40))]      # газон
        if len(r) < 20: continue
        Hh, S, V = r[:, 0], r[:, 1], r[:, 2]
        feats.setdefault(tid, []).append([
            ((Hh >= 95) & (Hh <= 130) & (S > 90) & (V > 60)).mean(),                 # насыщенный синий
            ((S < 50) & (V > 150)).mean(),                                          # белый
            (V < 60).mean(),                                                        # тёмный
            ((Hh >= 95) & (Hh <= 130) & (S > 60) & (V >= 25) & (V < 110)).mean()])  # тёмно-синий
p.kill()

ids = [t for t, v in feats.items() if len(v) >= MIN_N]
if len(ids) < 10: sys.exit("цвет замерен у %d треков — мало" % len(ids))
X = np.array([np.mean(feats[t], 0) for t in ids]); w = np.array([len(feats[t]) for t in ids], float)
rng = np.random.default_rng(0); best = None
for _ in range(20):
    C = X[rng.choice(len(X), 2, replace=False)]
    for _ in range(50):
        lab = np.argmin(((X[:, None] - C[None]) ** 2).sum(-1), 1)
        C = np.array([np.average(X[lab == j], 0, w[lab == j]) if (lab == j).any() else C[j] for j in range(2)])
    inertia = (w * ((X - C[lab]) ** 2).sum(-1)).sum()
    if best is None or inertia < best[0]: best = (inertia, lab.copy(), C.copy())
_, lab, C = best
gap = float(np.sqrt(((C[0] - C[1]) ** 2).sum()))
print("центры групп [синий, белый, тёмный, тёмно-синий]:", C.round(3).tolist(), "| расстояние %.2f" % gap)
if gap < MIN_GAP:
    print("цвета групп почти не различаются (< %.2f): деление по цвету не годится" % MIN_GAP); sys.exit(2)

# названия: по преобладающему признаку; если совпали — «светлые» и «тёмные»
WORD = ["синие", "белые", "тёмные", "тёмно-синие"]
names = [WORD[int(np.argmax(c))] for c in C]
if names[0] == names[1]:
    names = ["светлые", "тёмные"] if C[0][1] > C[1][1] else ["тёмные", "светлые"]
dist = np.sqrt(((X[:, None] - C[None]) ** 2).sum(-1)); margin = np.abs(dist[:, 0] - dist[:, 1])
team = {t: (int(l) if m > MARGIN else -1) for t, l, m in zip(ids, lab, margin)}
pos["team_of"] = {k: team.get(k, -1) for k in pos["team_of"]}
pos["team_names"] = {"0": names[0], "1": names[1]}
pos["team_split"] = "по цвету футболок (kit_colour_split.py)"
json.dump(pos, open(out_path, "w"))
from collections import Counter
print("кадров:", k, "| треков с цветом:", len(ids), "| по командам:", dict(Counter(pos["team_of"].values())),
      "| названия:", names)
