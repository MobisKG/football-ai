"""Переводит позиции игроков и мяча из пикселей кадра в метры плана поля.

Для каждого кадра берётся своя матрица из pitch_track.py: камера двигается, и
одна привязка на весь клип не годится. Игрок — точка у ног (низ рамки), мяч —
центр мяча, он почти всегда у земли.

Заодно считаем, кто стоит вне поля: это прямой признак зрителей и запасных у
бровки, которых отсев по скорости отделить не смог.

    python to_metres.py pos.json track.json out.json [кадры.jpg ...]
"""
import json, sys
import numpy as np

pos_path, track_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
pos = json.load(open(pos_path)); T = json.load(open(track_path))
W_P, L_P = T["plan"]["width"], T["plan"]["length"]
tr = T["frames"]
assert abs(pos["fps"] - T["fps"]) < 1e-6, "частоты позиций и привязки не совпадают"

def apply(H, pts):
    p = np.c_[np.asarray(pts, float), np.ones(len(pts))]
    q = (H @ p.T).T
    return q[:, :2] / q[:, 2:3]

MARGIN = 1.0      # на линии и чуть за ней игрок ещё на поле
out_frames = []; on = off = 0; held = 0
for k, f in enumerate(pos["frames"]):
    if k >= len(tr): break
    H = np.array(tr[k]["H_img2plan"], float).reshape(3, 3)
    src = tr[k]["src"]
    if src.startswith("hel"): held += 1
    rec = {"players": [], "ball": None, "src": src}
    if f["players"]:
        xy = apply(H, [[x, y] for _, x, y in f["players"]])
        for (tid, _, _), (X, Y) in zip(f["players"], xy):
            inside = -MARGIN <= X <= W_P + MARGIN and -MARGIN <= Y <= L_P + MARGIN
            on += inside; off += (not inside)
            rec["players"].append([int(tid), round(float(X), 2), round(float(Y), 2), bool(inside)])
    if f["ball"]:
        X, Y = apply(H, [f["ball"]])[0]
        rec["ball"] = [round(float(X), 2), round(float(Y), 2)]
    out_frames.append(rec)

out = {k: v for k, v in pos.items() if k != "frames"}
out.update({"units": "метры плана: x вдоль линии ворот 0..%.2f, y вдоль поля 0..%.2f" % (W_P, L_P),
            "plan": T["plan"], "frames": out_frames})
json.dump(out, open(out_path, "w"), ensure_ascii=False)
print("кадров %d, привязка удержана без сверки в %d" % (len(out_frames), held))
print("точек игроков: на поле %d, вне поля %d (%.0f %%)" % (on, off, 100 * off / max(on + off, 1)))
