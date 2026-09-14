"""Скорость и пробег игроков по позициям в метрах.

Позиции шумят на 0,5–1 м (точность привязки), поэтому скорость считается не
между соседними кадрами, а по сглаженной траектории: скользящее среднее за 0,5 с,
смещение за 0,5 с. Скорости выше 10 м/с физически недостижимы для любителя — это
скачки трекера или привязки, такие отрезки отбрасываются.

    python speeds.py pos_metres.json out.json
"""
import json, sys
import numpy as np
from collections import defaultdict

d = json.load(open(sys.argv[1])); fps = d["fps"]
team = {int(k): v for k, v in d["team_of"].items()}
num = {int(k): (v or {}).get("number") for k, v in (d.get("numbers") or {}).items()}
W = int(0.5 * fps); VMAX = 10.0

tr = defaultdict(list)
for k, f in enumerate(d["frames"]):
    for tid, X, Y, inside in f["players"]:
        if inside: tr[tid].append((k, X, Y))

per_track = {}
for tid, pts in tr.items():
    if len(pts) < 2 * W: continue
    a = np.array(pts, float)
    # разрывы трека больше 0,2 с режут его на куски
    cuts = np.where(np.diff(a[:, 0]) > 0.2 * fps)[0] + 1
    dist = 0.0; vmax = 0.0; t_moving = 0.0; t_total = 0.0
    for seg in np.split(a, cuts):
        if len(seg) < 2 * W: continue
        ker = np.ones(W) / W
        sx = np.convolve(seg[:, 1], ker, "valid"); sy = np.convolve(seg[:, 2], ker, "valid")
        dx = sx[W:] - sx[:-W]; dy = sy[W:] - sy[:-W]
        v = np.hypot(dx, dy) / (W / fps)
        ok = v < VMAX
        if not ok.any(): continue
        dist += float(np.hypot(np.diff(sx), np.diff(sy))[:len(ok)][ok[:len(sx) - 1]].sum())
        vmax = max(vmax, float(np.percentile(v[ok], 95)))   # 95-й процентиль, а не максимум: выбросы
        t_total += len(sx) / fps; t_moving += float((v[ok] > 1.0).sum()) / fps
    if t_total > 0:
        per_track[tid] = {"team": team.get(tid, -1), "number": num.get(tid), "dist_m": round(dist, 1),
                          "vmax_ms": round(vmax, 2), "time_s": round(t_total, 1)}

# собираем по игроку: команда + номер (треки одного игрока склеиваются по номеру)
players = defaultdict(lambda: {"dist_m": 0.0, "vmax_ms": 0.0, "time_s": 0.0, "tracks": 0})
for tid, r in per_track.items():
    if r["team"] not in (0, 1) or not r["number"]: continue
    p = players[(r["team"], r["number"])]
    p["dist_m"] += r["dist_m"]; p["time_s"] += r["time_s"]; p["tracks"] += 1
    p["vmax_ms"] = max(p["vmax_ms"], r["vmax_ms"])
names = {int(k): v for k, v in (d.get("team_names") or {}).items()}
rows = sorted(players.items(), key=lambda kv: -kv[1]["dist_m"])
print("%-9s %-5s %8s %9s %11s %9s" % ("команда", "номер", "пробег", "в кадре", "ср. скор.", "макс."))
for (t, n), p in rows:
    avg = p["dist_m"] / p["time_s"] if p["time_s"] else 0
    print("%-9s %-5s %6.0f м %7.0f с %7.2f м/с %5.1f км/ч" % (names.get(t, t), n, p["dist_m"], p["time_s"], avg, p["vmax_ms"] * 3.6))
json.dump({"per_track": per_track, "players": [{"team": names.get(t, t), "number": n, **p} for (t, n), p in rows]},
          open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
