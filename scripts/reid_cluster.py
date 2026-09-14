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
GUESS_W = 0.3        # вес голоса за «10» в apply_numbers.py: так модель отвечает, не видя цифр
def sure(c, n_shots=None):
    """номер, если он набрал ≥2 голоса и больше половины. «10» — только если
    так прочитана хотя бы половина всех снимков кластера (сырых голосов, а не
    веса): на полном матче 0,3 × много снимков перевешивало редкие настоящие
    прочтения, и в каждой команде выходило по пять «№10»."""
    if not c: return None
    n, b = c.most_common(1)[0]
    if not (b >= 2 and b > sum(c.values()) / 2): return None
    if n == 10:
        raw = b / GUESS_W
        if n_shots is None or raw < 4 or raw < 0.5 * n_shots: return None
    return n

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

def weak_conflict(a, b):
    """Склейка по одной внешности запрещена, если хоть один полноценный голос
    (вес ≥ 1, то есть не «10 по умолчанию») говорит за другой номер. Красный
    с «7» на спине, прочитанный один раз как «1», по внешности приклеился к
    №11; на клипе так было у 26 треков из 47 склеенных только по внешности."""
    ta = [n for n, w in a["votes"].most_common(1) if w >= 1]
    tb = [n for n, w in b["votes"].most_common(1) if w >= 1]
    return bool(ta and tb and ta[0] != tb[0])

# Расстояния между всеми парами считаются один раз матрицей: на полном матче
# 2600 треков, и пересчёт всех пар на каждом шаге (как было) не заканчивался
# за 10 минут. Минимум по парам снимков берётся через reduceat по группам
# столбцов (снимки лежат подряд по трекам). При склейке строка нового
# кластера — поминимум двух строк.
ids = list(clusters)
allshots = np.vstack([clusters[c]["shots"] for c in ids]).astype(np.float32)
sizes = np.array([len(clusters[c]["shots"]) for c in ids])
starts = np.concatenate([[0], np.cumsum(sizes)[:-1]])
n = len(ids)
D = np.empty((n, n), dtype=np.float32)
for i in range(n):
    g = 1 - allshots[starts[i]:starts[i] + sizes[i]] @ allshots.T          # снимки i × все снимки
    D[i] = np.minimum.reduceat(g, starts, axis=1).min(axis=0)
np.fill_diagonal(D, np.inf)
alive = np.ones(n, bool)
merged = 0

def do_merge(i, j):
    global merged
    a, b = clusters[ids[i]], clusters.pop(ids[j])
    w = a["n"] + b["n"]
    a["vec"] = (a["vec"] * a["n"] + b["vec"] * b["n"]) / w; a["vec"] /= np.linalg.norm(a["vec"]) + 1e-9
    a["n"] = w; a["tracks"] += b["tracks"]; a["frames"] |= b["frames"]; a["votes"] += b["votes"]; merged += 1
    a["shots"] = np.vstack([a["shots"], b["shots"]])
    D[i] = np.minimum(D[i], D[j]); D[:, i] = D[i]; D[i, i] = np.inf
    D[j, :] = np.inf; D[:, j] = np.inf; alive[j] = False

# 1) склейка по одинаковому уверенному номеру внутри команды, без пересечения по времени
by_num = defaultdict(list)
for i, c in enumerate(ids):
    nn = sure(clusters[c]["votes"], clusters[c]["n"])
    if nn is not None: by_num[(clusters[c]["team"], nn)].append(i)
for key, members in by_num.items():
    members.sort(key=lambda i: -len(clusters[ids[i]]["frames"]))
    heads = []
    for i in members:
        for h in heads:
            if alive[i] and can_merge(clusters[ids[h]], clusters[ids[i]]):
                do_merge(h, i); break
        else:
            heads.append(i)

# 2) внешность: ближайшие пары по очереди; отвергнутая пара больше не рассматривается
while True:
    flat = int(np.argmin(D)); i, j = divmod(flat, n)
    d = float(D[i, j])
    if not np.isfinite(d) or d >= THR: break
    a, b = clusters[ids[i]], clusters[ids[j]]
    if not weak_conflict(a, b) and can_merge(a, b):
        do_merge(i, j)
    else:
        D[i, j] = D[j, i] = np.inf

player_of = {}
for pid, (cid, c) in enumerate(sorted(clusters.items(), key=lambda kv: -len(kv[1]["frames"])), start=1):
    n = sure(c["votes"], c["n"])
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
    rows = [(len(c["frames"]) / met["fps"], sure(c["votes"], c["n"]), len(c["tracks"]), dict(c["votes"].most_common(3)))
            for c in clusters.values() if c["team"] == tm]
    rows.sort(key=lambda r: -r[0])
    print(" команда %d:" % tm)
    for sec, n, nt, v in rows[:14]:
        print("   №%-4s %5.0f с  треков %2d  голоса %s" % (n or "?", sec, nt, v))
