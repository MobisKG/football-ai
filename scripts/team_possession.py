"""Владение по командам, без опоры на номера треков.

Зачем: за пять минут система заводит 419 треков на двенадцать игроков, и когда
игрок посреди эпизода получает новый номер, это выглядит как смена владельца.
Передачи по именам игроков из-за этого не считаются. Но для владения и для
переходов мяча между командами личность игрока не нужна — достаточно цвета
формы, а он у трека почти не меняется.

Владельцем кадра считается команда ближайшего к мячу игрока. Дальше короткое
сглаживание по времени: смена команды засчитывается, только если новая команда
держится HOLD кадров подряд.

    python team_possession.py pos.json [truth.json]
"""
import json, sys
from collections import Counter

pos = json.load(open(sys.argv[1]))
fps, frames = pos["fps"], pos["frames"]
team_of = {int(k): v for k, v in pos["team_of"].items()}
NAMES = {int(k): v for k, v in (pos.get("team_names") or {}).items()}
name = lambda t: NAMES.get(t, "команда %s" % t)

import os
MAX_DIST = float(os.environ.get("MAXD", 220))
HOLD = int(os.environ.get("HOLD", 5))
MAX_CARRY = int(os.environ.get("CARRY", 30))
# Когда у мяча толкутся игроки обеих команд, ближайший меняется каждые
# несколько кадров, и в статистике появляются десятки несуществующих переходов.
# Требуем запас: своя команда должна быть ближе чужой хотя бы на MARGIN пикселей,
# иначе владение считается спорным и не засчитывается никому.
MARGIN = float(os.environ.get("MARGIN", 0))

per_frame = []
for f in frames:
    if f["ball"] is None or not f["players"]:
        per_frame.append(None); continue
    bx, by = f["ball"]
    near = {0: 1e9, 1: 1e9}
    for tid, px, py in f["players"]:
        t = team_of.get(tid, -1)
        if t not in (0, 1):
            continue
        d = ((px - bx) ** 2 + (py - by) ** 2) ** 0.5
        if d < near[t]:
            near[t] = d
    best = 0 if near[0] <= near[1] else 1
    other = 1 - best
    ok = near[best] <= MAX_DIST and (near[other] - near[best]) >= MARGIN
    per_frame.append(best if ok else None)

# Момент перехода помечаем не там, где он подтвердился, а там, где новая
# команда впервые стала ближе: подтверждение требует HOLD кадров и потому
# всегда опаздывает, а сверять надо с реальным моментом на видео.
cur, pend, pend_n, pend_i, since = None, None, 0, 0, 0
timeline, switches = [], []
for i, (t, f) in enumerate(zip(per_frame, frames)):
    since = 0 if f["ball"] is not None else since + 1
    if since > MAX_CARRY:
        cur, pend, pend_n = None, None, 0
        timeline.append(None); continue
    if t is not None and t != cur:
        if t == pend:
            pend_n += 1
        else:
            pend, pend_n, pend_i = t, 1, i
        if pend_n >= HOLD:
            if cur is not None:
                switches.append({"t": round(pend_i / fps, 2), "from": name(cur), "to": name(t)})
            cur, pend, pend_n = t, None, 0
    elif t == cur:
        pend, pend_n = None, 0
    timeline.append(cur)

known = [t for t in timeline if t is not None]
c = Counter(known)
tot = len(known) or 1
print("=" * 58)
print("Владение по командам, без опоры на номера треков")
print("=" * 58)
print("владение известно   %.1f %% кадров" % (100 * len(known) / len(frames)))
for t in (0, 1):
    print("%-14s      %5.1f %%   (%.1f с)" % (name(t), 100 * c.get(t, 0) / tot, c.get(t, 0) / fps))
print("переходов между командами %d" % len(switches))

if len(sys.argv) > 2:
    gt = json.load(open(sys.argv[2]))
    lo, hi = gt["covers_sec"]
    # переход между командами в эталоне: тот, где мяч меняет цвет владельца
    tr = []
    prev = None
    for e in gt["events"]:
        if not (lo <= e["t"] <= hi):
            continue
        who = e.get("team")
        if who and who != prev:
            if prev is not None:
                tr.append({"t": e["t"], "from": prev, "to": who})
            prev = who
    mine = [s for s in switches if lo <= s["t"] <= hi]
    used, hit = set(), 0
    for g in tr:
        best, bd = None, 1e9
        for i, s in enumerate(mine):
            if i in used: continue
            d = abs(s["t"] - g["t"])
            if d < bd: best, bd = i, d
        if best is not None and bd <= 2.5:
            used.add(best); hit += 1
    print("-" * 58)
    print("эталонных переходов между командами %d" % len(tr))
    print("найдено системой                    %d" % len(mine))
    print("совпало                             %d  (%.0f %%)" % (hit, 100 * hit / max(len(tr), 1)))
    print("лишних                              %d" % (len(mine) - len(used)))
    print("-" * 58)
    for g in tr:
        near = [s for s in mine if abs(s["t"] - g["t"]) <= 2.5]
        mark = "OK " if near else "НЕТ"
        print("%s %4.0f с  %s -> %s%s" % (mark, g["t"], g["from"], g["to"],
              ("   | найдено %.1f с" % near[0]["t"]) if near else ""))
