"""Склеивает треки одного игрока по прочитанному номеру и цвету формы.

Склейка по траекториям не помогла: вчера она соединила сто обрывков из 419 и не
изменила ни одной цифры, потому что угадывала по движению и времени. Номер на
футболке опознаёт человека напрямую: треки #11 и #517 оба дали «18, синяя с
жёлтым» — это один игрок, разорванный трекером.

Два трека сливаются, если совпал номер и цвет формы, и они не пересекаются по
времени (один человек не может быть в двух местах в одном кадре).

    python stitch_by_number.py pos.json numbers.json out.json [--min-conf 0.7]
"""
import json, sys
from collections import defaultdict

pos_path, num_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
MIN_CONF = float(sys.argv[sys.argv.index("--min-conf") + 1]) if "--min-conf" in sys.argv else 0.7

pos = json.load(open(pos_path))
nums = json.load(open(num_path))
frames = pos["frames"]
team = {int(k): v for k, v in pos["team_of"].items()}

span = {}
for i, f in enumerate(frames):
    for tid, x, y in f["players"]:
        t = int(tid)
        if t in span:
            a, b = span[t]; span[t] = (min(a, i), max(b, i))
        else:
            span[t] = (i, i)

def key(tid):
    v = nums.get(str(tid))
    if not v:
        return None
    n = v.get("number")
    if not isinstance(n, int):
        return None
    if float(v.get("confidence") or 0) < MIN_CONF:
        return None
    kit = (v.get("kit") or "").strip().lower()
    if not kit:
        return None
    return (n, kit)

groups = defaultdict(list)
for tid in span:
    k = key(tid)
    if k:
        groups[k].append(tid)

parent = {t: t for t in span}
def find(t):
    while parent[t] != t:
        parent[t] = parent[parent[t]]; t = parent[t]
    return t

merges = 0
for k, tids in groups.items():
    tids.sort(key=lambda t: span[t][0])
    kept = []
    for t in tids:
        a, b = span[t]
        # пересечение по времени означает, что это разные люди с одним номером
        # (например, два седьмых номера в разных командах прочитаны одинаково)
        clash = any(not (b < span[u][0] or a > span[u][1]) for u in kept)
        if clash:
            continue
        if kept:
            parent[find(t)] = find(kept[0]); merges += 1
        kept.append(t)

newid = {t: find(t) for t in span}
for f in frames:
    f["players"] = [[newid[int(t)], x, y] for t, x, y in f["players"]]

team2, num2 = {}, {}
for t in span:
    n = newid[t]
    if team.get(t, -1) in (0, 1):
        team2.setdefault(n, team[t])
    k = key(t)
    if k:
        num2.setdefault(str(n), {"number": k[0], "kit": k[1]})
for t in span:
    team2.setdefault(newid[t], team.get(t, -1))
pos["team_of"] = {str(k): v for k, v in team2.items()}
pos["numbers"] = num2
pos["stitched_by_number"] = {"before": len(span), "after": len(set(newid.values())), "merges": merges}
json.dump(pos, open(out_path, "w"), ensure_ascii=False)
print("треков было %d, стало %d (склеек %d), с номером %d"
      % (len(span), len(set(newid.values())), merges, len(num2)))
