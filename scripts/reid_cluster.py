"""Склеивает треки в игроков по внешности, времени и номерам.

Правила (по убыванию силы):
1. Два трека, которые есть в кадре одновременно, — разные люди. Всегда.
2. Два трека с разными уверенно прочитанными номерами — разные люди.
3. Треки с одним и тем же уверенным номером склеиваются (внутри команды).
4. Внешность (reid_embed.py) — только как очень строгий довесок: замер на 66
   треках с надёжными номерами показал, что общий DINOv2 почти не различает
   игроков одной формы (медиана расстояния «свои» 0,19, «чужие» 0,23), поэтому
   Замер OSNet (обучен опознавать людей, MSMT17) на тех же 66 треках:
   по среднему вектору свои 0,10 / чужие 0,15 — лучше DINOv2, но всё ещё
   слабо; по ближайшей паре снимков при пороге 0,10 сходятся 21 % своих и
   только 2 % чужих. Поэтому расстояние — минимум по парам снимков, порог
   0,10 (--thr), и оно никогда не пересиливает номера и время.
Вратари не участвуют: их треки уже собраны goalkeepers_plan.py.

Номер игрока — сумма голосов по всем его трекам; он достаётся всем кускам,
включая те, где номер не читался. В файлы позиций пишется player_of
(трек -> игрок) и обновляются номера.

    python reid_cluster.py pos.json pos_metres.json embed.json [--thr 0.35]
"""
import json, sys
from collections import Counter, defaultdict
import numpy as np

pos_path, met_path, emb_path = sys.argv[1:4]
THR = float(sys.argv[sys.argv.index("--thr") + 1]) if "--thr" in sys.argv else 0.10
pos = json.load(open(pos_path)); met = json.load(open(met_path)); emb = json.load(open(emb_path))
team = {int(k): v for k, v in pos["team_of"].items()}
nums = {int(k): v for k, v in (pos.get("numbers") or {}).items()}
gk = {int(k): v for k, v in (pos.get("goalkeepers") or {}).items()}
side = set(pos.get("sideline") or [])

frames_of = defaultdict(set)
for k, f in enumerate(met["frames"]):
    for tid, X, Y, ins in f["players"]:
        frames_of[int(tid)].add(k)

def votes(t):
    v = (nums.get(t) or {}).get("votes") or {}
    return Counter({int(a): b for a, b in v.items()})
def sure(c):
    """номер, если он набрал ≥2 голоса и больше половины"""
    if not c: return None
    n, b = c.most_common(1)[0]
    return n if b >= 2 and b > sum(c.values()) / 2 else None

clusters = {}          # id -> {"tracks", "frames", "votes", "vec", "team"}
for k, r in emb.items():
    t = int(k)
    if t in gk or t in side or team.get(t, -1) not in (0, 1): continue
    v = np.array(r["vec"]); v = v / (np.linalg.norm(v) + 1e-9)
    shots = np.array(r.get("shots") or [r["vec"]], float)
    shots /= np.linalg.norm(shots, axis=1, keepdims=True) + 1e-9
    clusters[t] = {"tracks": [t], "frames": set(frames_of[t]), "votes": votes(t), "vec": v, "shots": shots, "n": r["n"], "team": team[t]}

def dist(a, b):
    """ближайшая пара снимков двух кластеров"""
    return float((1 - a["shots"] @ b["shots"].T).min())
def can_merge(a, b):
    if a["team"] != b["team"]: return False
    if len(a["frames"] & b["frames"]) > 3: return False
    ta, tb = a["votes"].most_common(1), b["votes"].most_common(1)
    if ta and tb and ta[0][1] >= 2 and tb[0][1] >= 2 and ta[0][0] != tb[0][0]: return False
    return True

merged = 0
while True:
    best = None
    ids = list(clusters)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = clusters[ids[i]], clusters[ids[j]]
            d = dist(a, b)
            na, nb = sure(a["votes"]), sure(b["votes"])
            same_num = na is not None and na == nb
            if (same_num or d < THR) and (best is None or d < best[0]) and can_merge(a, b):
                best = (d, ids[i], ids[j])
    if best is None: break
    d, i, j = best; a, b = clusters[i], clusters.pop(j)
    w = a["n"] + b["n"]
    a["vec"] = (a["vec"] * a["n"] + b["vec"] * b["n"]) / w; a["vec"] /= np.linalg.norm(a["vec"]) + 1e-9
    a["n"] = w; a["tracks"] += b["tracks"]; a["frames"] |= b["frames"]; a["votes"] += b["votes"]; merged += 1
    a["shots"] = np.vstack([a["shots"], b["shots"]])

player_of = {}
for pid, (cid, c) in enumerate(sorted(clusters.items(), key=lambda kv: -len(kv[1]["frames"])), start=1):
    n = sure(c["votes"])
    for t in c["tracks"]:
        player_of[t] = pid
        e = dict(nums.get(t) or {}); e["number"] = n; e["player"] = pid; nums[t] = e
for t in gk:
    player_of[t] = 100 + team[t]          # вратари: 100 синих, 101 красных
for d in (pos, met):
    d["numbers"] = {str(k): v for k, v in nums.items()}
    d["player_of"] = {str(k): v for k, v in player_of.items()}
json.dump(pos, open(pos_path, "w"), ensure_ascii=False)
json.dump(met, open(met_path, "w"), ensure_ascii=False)
print("склеек %d, игроков %d" % (merged, len(clusters)))
for tm in (0, 1):
    rows = [(len(c["frames"]) / met["fps"], sure(c["votes"]), len(c["tracks"]), dict(c["votes"].most_common(3)))
            for c in clusters.values() if c["team"] == tm]
    rows.sort(key=lambda r: -r[0])
    print(" команда %d:" % tm)
    for sec, n, nt, v in rows[:14]:
        print("   №%-4s %5.0f с  треков %2d  голоса %s" % (n or "?", sec, nt, v))
