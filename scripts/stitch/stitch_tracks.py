"""Склейка коротких треков по месту и времени на поле: номер, прочитанный на одном треке,
переходит на соседние треки того же игрока. Без картинок — только pos_metres.json.

    python stitch_tracks.py <pos_metres.json> [--gap 3] [--speed 8] [--eval]

Правило: трек B продолжает трек A, если B начинается не позже чем через gap с после конца A,
в точке не дальше speed·Δt + 1,5 м от конца A, команды не противоречат, и у A нет другого
кандидата-продолжения с похожей оценкой (неоднозначные стыки не склеиваются).
--eval: прячет номера у части треков с номером и смотрит, верно ли склейка их возвращает.
"""
import json, sys, random
from collections import defaultdict

def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
GAP = float(opt("--gap", 3)); SPEED = float(opt("--speed", 8)); AMBIG = 1.5

def load(path):
    d = json.load(open(path)); fps = d["fps"]
    tr = defaultdict(list)
    for i, f in enumerate(d["frames"]):
        for p in f["players"]:
            if p[3]: tr[p[0]].append((i / fps, p[1], p[2]))
    team = {int(k): v for k, v in d["team_of"].items()}
    num = {int(k): v["number"] for k, v in d["numbers"].items() if v.get("number") not in (None, 0, 10)}
    info = {}
    for t, pts in tr.items():
        if len(pts) < 3: continue
        info[t] = {"t0": pts[0][0], "t1": pts[-1][0], "p0": pts[0][1:], "p1": pts[-1][1:], "team": team.get(t, -1), "dur": pts[-1][0] - pts[0][0]}
    return d, info, num

def links(info):
    """однозначные стыки A→B"""
    ends = sorted(info.items(), key=lambda kv: kv[1]["t1"])
    starts = sorted(info.items(), key=lambda kv: kv[1]["t0"])
    cand = defaultdict(list)
    for a, A in ends:
        for b, B in starts:
            dt = B["t0"] - A["t1"]
            if dt < -0.2: continue
            if dt > GAP: break
            if a == b or (A["team"] >= 0 and B["team"] >= 0 and A["team"] != B["team"]): continue
            dist = ((A["p1"][0] - B["p0"][0]) ** 2 + (A["p1"][1] - B["p0"][1]) ** 2) ** 0.5
            lim = SPEED * max(dt, 0) + 1.5
            if dist <= lim: cand[a].append((dist / lim + dt / GAP, b))
    out = {}; used_b = defaultdict(list)
    for a, cs in cand.items():
        cs.sort()
        if len(cs) > 1 and cs[1][0] < cs[0][0] * AMBIG + 0.15: continue   # неоднозначно
        used_b[cs[0][1]].append((cs[0][0], a))
    for b, as_ in used_b.items():
        as_.sort()
        if len(as_) > 1 and as_[1][0] < as_[0][0] * AMBIG + 0.15: continue
        out[as_[0][1]] = b
    return out

def chains(info, nxt):
    prev = {b: a for a, b in nxt.items()}; seen = set(); res = []
    for t in sorted(info, key=lambda t: info[t]["t0"]):
        if t in seen or t in prev: continue
        c = [t]; seen.add(t)
        while c[-1] in nxt and nxt[c[-1]] not in seen: c.append(nxt[c[-1]]); seen.add(c[-1])
        res.append(c)
    return res

def assign(info, num, chs):
    """номер цепочки — по времени треков с номером; противоречивая цепочка номер не получает"""
    got = {}
    for c in chs:
        votes = defaultdict(float)
        for t in c:
            if t in num: votes[num[t]] += info[t]["dur"]
        if not votes: continue
        best = max(votes, key=votes.get)
        if votes[best] < 0.8 * sum(votes.values()): continue
        for t in c: got[t] = best
    return got

if __name__ == "__main__":
    d, info, num = load(sys.argv[1])
    nxt = links(info); chs = chains(info, nxt); got = assign(info, num, chs)
    tot = sum(i["dur"] for i in info.values())
    before = sum(info[t]["dur"] for t in num if t in info); after = sum(info[t]["dur"] for t in got)
    print("треков %d, стыков %d, цепочек %d" % (len(info), len(nxt), len(chs)))
    print("время с номером: было %.0f с (%.0f%%), стало %.0f с (%.0f%%) из %.0f с" % (before, 100 * before / tot, after, 100 * after / tot, tot))
    if "--eval" in sys.argv:
        random.seed(1); rep = []
        for r in range(5):
            hide = set(random.sample(sorted(t for t in num if t in info), len(num) // 3))
            g = assign(info, {t: n for t, n in num.items() if t not in hide}, chs)
            back = [t for t in hide if t in g]; ok = [t for t in back if g[t] == num[t]]
            rep.append((len(hide), len(back), len(ok)))
        h, b, o = map(sum, zip(*rep))
        print("проверка (спрятано 1/3 номеров, 5 раз): вернулось %d из %d, из них верно %d (%.0f%%)" % (b, h, o, 100 * o / max(1, b)))
    if "--out" in sys.argv:
        json.dump({str(t): n for t, n in got.items()}, open(opt("--out"), "w"))
