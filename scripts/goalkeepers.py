"""Находит вратарей, приписывает их к командам и даёт им номер 1.

Проблема: вратарь одет не в форму своей команды, поэтому и кластеризация, и
чтение цвета формы относят его либо к чужим, либо в «не игроки». В ленте это
выглядит дико: «пас красных номеру 9», где девятка — вратарь синих.

Как узнаём вратаря: он держится у своих ворот и почти не перемещается вдоль
поля, тогда как полевой за клип пробегает его целиком.

Как узнаём его команду: по тому, кто ему отдаёт мяч и кому отдаёт он. Свои
пасуют вратарю в разы чаще соперников — это и есть признак, не требующий ни
плана поля, ни цвета формы.

Номер ставим 1: у вратарей он почти всегда такой, а прочитать его на снимке
со спины обычно нельзя.

    python goalkeepers.py pos.json out.json [--span 0.45]
"""
import json, sys
from collections import Counter, defaultdict

pos_path, out_path = sys.argv[1], sys.argv[2]
SPAN = float(sys.argv[sys.argv.index("--span") + 1]) if "--span" in sys.argv else 0.45

pos = json.load(open(pos_path))
frames, fps = pos["frames"], pos["fps"]
team = {int(k): v for k, v in pos["team_of"].items()}
W = 1920.0

tr = defaultdict(list)
for i, f in enumerate(frames):
    for tid, x, y in f["players"]:
        tr[int(tid)].append((i, x, y))

# --- кто похож на вратаря --------------------------------------------------
cand = []
for t, pts in tr.items():
    if len(pts) < int(3 * fps):          # слишком короткий трек ни о чём не говорит
        continue
    xs = [p[1] for p in pts]
    span = (max(xs) - min(xs)) / W
    mx = sum(xs) / len(xs) / W
    ys = [p[2] for p in pts]
    my = sum(ys) / len(ys) / 1080.0
    # Держится у самого края поля и почти не ходит вдоль него. Границы жёсткие:
    # при мягких в кандидаты попадали запасные у бровки — трек с двумя стоящими
    # запасными имел середину на 0.19 ширины и 0.80 высоты и был записан во
    # вратари. Ворота в этом кадре ближе к краю и выше по вертикали.
    if span <= SPAN and (mx < 0.10 or mx > 0.75) and 0.35 <= my <= 0.75:
        cand.append(t)

# --- чей вратарь: по тому, кто ему пасует ----------------------------------
MAX_DIST, HOLD = 170, 6
raw = []
for f in frames:
    if f["ball"] is None or not f["players"]:
        raw.append(None); continue
    bx, by = f["ball"]; best, bd = None, 1e9
    for tid, px, py in f["players"]:
        d = ((px - bx) ** 2 + (py - by) ** 2) ** 0.5
        if d < bd: best, bd = int(tid), d
    raw.append(best if bd <= MAX_DIST else None)
owner, pend, pend_n = None, None, 0
seq = []
for r in raw:
    if r is None: continue
    if r == owner:
        pend, pend_n = None, 0
    else:
        pend, pend_n = (r, pend_n + 1) if r == pend else (r, 1)
        if pend_n >= HOLD:
            if owner is not None: seq.append((owner, r))
            owner, pend, pend_n = r, None, 0

partners = defaultdict(Counter)
for a, b in seq:
    for who, other in ((a, b), (b, a)):
        if who in cand and team.get(other, -1) in (0, 1):
            partners[who][team[other]] += 1

gk = {}
for t in cand:
    c = partners.get(t)
    if not c:
        continue
    side, n = c.most_common(1)[0]
    if n >= 2 and n >= 0.6 * sum(c.values()):
        gk[t] = side

nums = pos.get("numbers") or {}
for t, side in gk.items():
    team[t] = side
    nums[str(t)] = {"number": 1, "kit": "вратарь"}
pos["team_of"] = {str(k): v for k, v in team.items()}
pos["numbers"] = nums
pos["goalkeepers"] = {str(k): v for k, v in gk.items()}
json.dump(pos, open(out_path, "w"), ensure_ascii=False)
print("кандидатов в вратари %d, определены с командой %d" % (len(cand), len(gk)))
for t, s in list(gk.items())[:8]:
    print("  трек #%-5d вратарь команды %d, обменов мячом с командами: %s"
          % (t, s, dict(partners[t])))
