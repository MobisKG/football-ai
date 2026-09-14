"""Склеивает обрывки треков одного игрока в один трек. Офлайн, после прохода.

Зачем: за пять минут на двенадцать человек трекер заводит 419 треков. Владение
считается по номеру трека, поэтому смена номера посреди эпизода выглядит как
передача, а настоящая передача теряется. Это же ломает и тип события: пас и
потеря различаются только по командам участников.

Онлайн-трекер склеить их не мог: в момент обрыва он не знает будущего. Здесь
известен весь клип, поэтому обрывок ищется не «вперёд по кадрам», а по всем
кандидатам сразу, и выбирается лучшая пара.

    python stitch.py pos.json out.json [--max-gap 1.5] [--max-dist 160]
"""
import json, sys, math
from collections import defaultdict

pos_path, out_path = sys.argv[1], sys.argv[2]
def opt(n, d):
    return float(sys.argv[sys.argv.index(n) + 1]) if n in sys.argv else d
MAX_GAP = opt("--max-gap", 1.5)     # секунд между концом одного обрывка и началом другого
MAX_DIST = opt("--max-dist", 160.)  # пикселей между предсказанием и началом

pos = json.load(open(pos_path))
fps = pos["fps"]; frames = pos["frames"]
team = {int(k): v for k, v in pos["team_of"].items()}

tr = defaultdict(list)
for i, f in enumerate(frames):
    for tid, x, y in f["players"]:
        tr[int(tid)].append((i, x, y))
for t in tr:
    tr[t].sort()

def vel(pts, tail=6):
    if len(pts) < 2:
        return (0.0, 0.0)
    a, b = pts[max(0, len(pts) - tail)], pts[-1]
    dt = max(1, b[0] - a[0])
    return ((b[1] - a[1]) / dt, (b[2] - a[2]) / dt)

def head_vel(pts, head=6):
    if len(pts) < 2:
        return (0.0, 0.0)
    a, b = pts[0], pts[min(len(pts) - 1, head)]
    dt = max(1, b[0] - a[0])
    return ((b[1] - a[1]) / dt, (b[2] - a[2]) / dt)

parent = {t: t for t in tr}
def find(t):
    while parent[t] != t:
        parent[t] = parent[parent[t]]; t = parent[t]
    return t

ends = sorted(tr, key=lambda t: tr[t][-1][0])
pairs = []
for a in tr:
    ea = tr[a][-1]
    va = vel(tr[a])
    for b in tr:
        if b == a:
            continue
        sb = tr[b][0]
        gap = sb[0] - ea[0]
        if gap <= 0 or gap > MAX_GAP * fps:
            continue
        ta, tb = team.get(a, -1), team.get(b, -1)
        if ta in (0, 1) and tb in (0, 1) and ta != tb:
            continue                     # разные команды — точно разные люди
        px = ea[1] + va[0] * gap
        py = ea[2] + va[1] * gap
        d = math.hypot(sb[1] - px, sb[2] - py)
        if d > MAX_DIST:
            continue
        vb = head_vel(tr[b])
        dv = math.hypot(vb[0] - va[0], vb[1] - va[1])
        pairs.append((d + 6.0 * dv + 2.0 * gap / fps, a, b))

pairs.sort()
used_end, used_start = set(), set()
merged = 0
for cost, a, b in pairs:
    ra, rb = find(a), find(b)
    if ra == rb or a in used_end or b in used_start:
        continue
    parent[rb] = ra
    used_end.add(a); used_start.add(b)
    merged += 1

newid = {}
for t in tr:
    newid[t] = find(t)
for f in frames:
    f["players"] = [[newid[int(t)], x, y] for t, x, y in f["players"]]
team2 = {}
for t, v in team.items():
    n = newid.get(t, t)
    if v in (0, 1):
        team2.setdefault(n, v)
for t in tr:
    team2.setdefault(newid[t], team.get(t, -1))
pos["team_of"] = {str(k): v for k, v in team2.items()}
pos["stitched"] = {"before": len(tr), "after": len(set(newid.values())), "merges": merged}
json.dump(pos, open(out_path, "w"), ensure_ascii=False)
print("треков было %d, стало %d (склеек %d)" % (len(tr), len(set(newid.values())), merged))
