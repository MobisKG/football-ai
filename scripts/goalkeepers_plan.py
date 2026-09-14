"""Вратари по плану поля: кто стоит глубже всех у своих ворот.

Старый goalkeepers.py искал вратаря по долям кадра и цвету формы и ошибался:
вратарь синих в голубой куртке уходил в синие полевые, вратарь красных в
розовой майке оставался вовсе без команды. Теперь есть позиции в метрах, и
вратарь виден просто: на каждом кадре это самый глубокий игрок у ворот,
не дальше GK_DEPTH м от линии. Трек, который «самый глубокий» большую часть
своей жизни, — вратарь этого края.

Чья половина: команда, чьи игроки чаще держатся у этого края, его защищает
(защитники стоят глубже нападающих). Проверено на клипе 5: синие у ближних
ворот, красные у дальних, совпало с цветами вратарских курток.

Заодно достаём из «без команды» полевых, у которых цвет формы прочитан
однозначно (первое слово «синий»/«красный»), — они играли, но подписи
не получали.

    python goalkeepers_plan.py pos.json pos_metres.json
Оба файла перезаписываются: team_of, numbers, goalkeepers.
"""
import json, sys
from collections import Counter, defaultdict
import numpy as np

pos_path, met_path = sys.argv[1], sys.argv[2]
pos = json.load(open(pos_path)); met = json.load(open(met_path))
fps = met["fps"]; L = met["plan"]["length"] if "length" in met.get("plan", {}) else 43.08
team = {int(k): v for k, v in pos["team_of"].items()}
nums = {int(k): v for k, v in (pos.get("numbers") or {}).items()}
GK_DEPTH, MIN_SEC, SHARE = 6.0, 1.5, 0.6

# --- полевые без команды, но с понятным цветом формы -------------------------
on = Counter()
for f in met["frames"]:
    for tid, X, Y, ins in f["players"]:
        if ins: on[int(tid)] += 1
fixed = 0
for t, n in on.items():
    if team.get(t, -1) in (0, 1) or n < MIN_SEC * fps: continue
    kit = ((nums.get(t) or {}).get("kit") or "").split()
    if kit and kit[0].startswith("син"): team[t] = 0; fixed += 1
    elif kit and kit[0].startswith("красн"): team[t] = 1; fixed += 1

# --- самый глубокий у каждого края на каждом кадре ---------------------------
deepest = defaultdict(Counter)          # трек -> {край: сколько кадров он самый глубокий}
for f in met["frames"]:
    pl = [(int(t), X, Y) for t, X, Y, ins in f["players"] if ins]
    for end, key in ((0, lambda p: p[2]), (1, lambda p: L - p[2])):
        # за линией ворот (глубина < 0) стоят зрители: человек в чёрном за
        # дальними воротами попадал во вратари
        near = [p for p in pl if -0.2 <= key(p) <= GK_DEPTH]
        if near:
            deepest[min(near, key=key)[0]][end] += 1

gk = {}
for t, c in deepest.items():
    end, n = c.most_common(1)[0]
    if on[t] >= MIN_SEC * fps and n >= SHARE * on[t]:
        gk[t] = end

# --- обрывки вратаря, где он не «самый глубокий» -------------------------------
# Вратарь — один человек, его достаточно запомнить один раз. Две приметы:
# 1) форма: слова расцветки, которые есть у подтверждённых вратарей края и
#    почти не встречаются у полевых («розовый» у красных);
# 2) пустые ворота: трек у ворот (в 6 м, не за линией), пока подтверждённого
#    вратаря этого края в кадре нет, — это он же, просто трекер оборвал трек.
def kit_of(t):
    return ((nums.get(t) or {}).get("kit") or "").lower()
frames_of = defaultdict(list)
for k, f in enumerate(met["frames"]):
    for tid, X, Y, ins in f["players"]:
        frames_of[int(tid)].append((k, X, Y, ins))
present = {0: set(), 1: set()}
for t, end in gk.items():
    present[end].update(k for k, _, _, _ in frames_of[t])
outfield_kits = Counter(kit_of(t) for t in on if t not in gk and team.get(t, -1) in (0, 1))
n_out = max(1, sum(outfield_kits.values()))
signature = {}
for end in (0, 1):
    c = Counter(kit_of(t) for t, e in gk.items() if e == end)
    for kit, n in c.items():
        if kit and n >= 2 and outfield_kits[kit] / n_out < 0.05:
            signature[kit] = end
print("приметы вратарей по форме:", signature)
added = 0
for t, pts in frames_of.items():
    if t in gk or len(pts) < fps: continue
    ys = np.array([Y for _, _, Y, _ in pts])
    for end, depth in ((0, ys), (1, L - ys)):
        deep = ((depth >= -0.2) & (depth <= GK_DEPTH)).mean()
        empty = 1 - sum(1 for k, _, _, _ in pts if k in present[end]) / len(pts)
        if (kit_of(t) in signature and signature[kit_of(t)] == end and deep >= 0.3) or (deep >= 0.8 and empty >= 0.8):
            gk[t] = end; added += 1; break
print("добавлено обрывков вратарей: %d" % added)

# --- какая команда защищает какой край ---------------------------------------
depth = {0: [], 1: []}
for f in met["frames"]:
    for t, X, Y, ins in f["players"]:
        t = int(t)
        if ins and t not in gk and team.get(t, -1) in (0, 1): depth[team[t]].append(Y)
share_near = {tm: float(np.mean(np.array(d) < 10)) for tm, d in depth.items()}
defends = {0: max(share_near, key=share_near.get)}          # край y=0
defends[1] = 1 - defends[0]
print("ближние ворота (y=0) защищает команда %d, дальние — %d; доли у ближнего края %s"
      % (defends[0], defends[1], {k: round(v, 2) for k, v in share_near.items()}))

for t, end in gk.items():
    team[t] = defends[end]
    old = nums.get(t) or {}
    nums[t] = {"number": 1, "kit": "вратарь (" + (old.get("kit") or "?") + ")"}
    print("  трек #%-5d вратарь края %d, команда %d, кадров %d, %.0f–%.0f с, форма: %s"
          % (t, end, defends[end], on[t], min(k for k, f in enumerate(met["frames"]) if any(int(p[0]) == t for p in f["players"])) / fps,
             max(k for k, f in enumerate(met["frames"]) if any(int(p[0]) == t for p in f["players"])) / fps, old.get("kit")))

for d in (pos, met):
    d["team_of"] = {str(k): v for k, v in team.items()}
    d["numbers"] = {str(k): v for k, v in nums.items()}
    d["goalkeepers"] = {str(k): defends[e] for k, e in gk.items()}
json.dump(pos, open(pos_path, "w"), ensure_ascii=False)
json.dump(met, open(met_path, "w"), ensure_ascii=False)
print("вратарских треков %d, полевых получили команду по цвету формы %d" % (len(gk), fixed))
