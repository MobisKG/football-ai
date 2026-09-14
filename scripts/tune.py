"""Подбирает пороги расчёта владения по эталону, размеченному человеком.

Пересчёт идёт из готового файла позиций, видеокарта не нужна: перебор сотен
сочетаний занимает секунды на ноутбуке. Оптимизируем число угаданных событий,
при равенстве — меньшее число лишних.

    python tune.py pos.json truth.json
"""
import json, sys, itertools
from collections import Counter

pos = json.load(open(sys.argv[1]))
gt = json.load(open(sys.argv[2]))
fps = pos["fps"]; frames = pos["frames"]
team_of = {int(k): v for k, v in pos["team_of"].items()}
lo, hi = gt["covers_sec"]
truth = [e for e in gt["events"] if lo <= e["t"] <= hi and e["kind"] in ("пас", "потеря")]
TOL = 2.5

def run(max_dist, hold, carry, keep_unknown):
    raw = []
    for f in frames:
        if f["ball"] is None or not f["players"]:
            raw.append(None); continue
        bx, by = f["ball"]; best, bd = None, 1e9
        for tid, px, py in f["players"]:
            if not keep_unknown and team_of.get(tid, -1) not in (0, 1):
                continue
            dd = ((px - bx) ** 2 + (py - by) ** 2) ** 0.5
            if dd < bd: best, bd = tid, dd
        raw.append(best if bd <= max_dist else None)
    owner, pending, pend_n, since = None, None, 0, 0
    ev = []
    for i, (r, f) in enumerate(zip(raw, frames)):
        since = 0 if f["ball"] is not None else since + 1
        if since > carry:
            owner, pending, pend_n = None, None, 0; continue
        if r is None: continue
        if r == owner:
            pending, pend_n = None, 0
        else:
            pending, pend_n = (r, pend_n + 1) if r == pending else (r, 1)
            if pend_n >= hold:
                if owner is not None:
                    ta, tb = team_of.get(owner, -1), team_of.get(r, -1)
                    if ta in (0, 1) and tb in (0, 1):
                        ev.append({"t": i / fps, "kind": "пас" if ta == tb else "потеря",
                                   "team": "синие" if tb == 0 else "красные"})
                owner, pending, pend_n = r, None, 0
    return ev

def score(ev):
    used, hit, kind_ok = set(), 0, 0
    for g in truth:
        best, bd = None, 1e9
        for i, f in enumerate(ev):
            if i in used: continue
            dd = abs(f["t"] - g["t"])
            if dd < bd: best, bd = i, dd
        if best is not None and bd <= TOL:
            used.add(best); hit += 1
            if ev[best]["kind"] == g["kind"]: kind_ok += 1
    return hit, kind_ok, len(ev) - len(used)

best = []
for md in (110, 140, 170, 220, 280):
    for hold in (2, 3, 4, 6, 9):
        for carry in (5, 15, 30, 60):
            for ku in (False, True):
                ev = run(md, hold, carry, ku)
                h, k, extra = score(ev)
                best.append((h, k, -extra, md, hold, carry, ku, len(ev)))
best.sort(reverse=True)
print("эталонных переходов: %d" % len(truth))
print("%-6s %-6s %-6s %-7s %-5s %-6s %-6s %s" % ("угад", "тип", "лишн", "дист", "hold", "carry", "вратари", "всего"))
for h, k, ne, md, hold, carry, ku, n in best[:12]:
    print("%-6d %-6d %-6d %-7d %-5d %-6d %-6s %d" % (h, k, -ne, md, hold, carry, "да" if ku else "нет", n))
