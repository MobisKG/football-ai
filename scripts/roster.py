"""Чистит номера и убирает людей за бровкой по плану поля.

Три правила, каждое видно на клипе 5:
1. Трек, который ≥ OFF_SHARE времени вне поля, — зритель или запасной. Ему
   снимаем команду и номер: у забора стояли «№23», «№9», «№2 и №2».
2. Номер, который в сумме по команде виден меньше MIN_SEC секунд, — ошибка
   чтения (85, 58, 39, 74). Снимаем номер, игрок остаётся с эллипсом команды.
3. Два игрока одной команды с одним номером не бывают на поле одновременно.
   Если трек большую часть жизни спорит номер с более длинным треком, номер
   у него снимаем (красных «9» одновременно было двое 867 кадров).

Вратарей не трогаем: их поставил goalkeepers_plan.py.

    python roster.py pos.json pos_metres.json
Оба файла перезаписываются.
"""
import json, sys
from collections import Counter, defaultdict

pos_path, met_path = sys.argv[1], sys.argv[2]
pos = json.load(open(pos_path)); met = json.load(open(met_path))
fps = met["fps"]
team = {int(k): v for k, v in pos["team_of"].items()}
nums = {int(k): v for k, v in (pos.get("numbers") or {}).items()}
gk = set(int(k) for k in (pos.get("goalkeepers") or {}))
OFF_SHARE, MIN_SEC, CONFLICT_SHARE = 0.7, 8.0, 0.3

on = Counter(); off = Counter()
for f in met["frames"]:
    for tid, X, Y, ins in f["players"]:
        (on if ins else off)[int(tid)] += 1

def number(t):
    n = (nums.get(t) or {}).get("number")
    return n if isinstance(n, int) else None

# Стоящие у самой бровки (в пределах BAND м от линии, медианная скорость
# меньше 1 м/с) — тоже зрители: два красных у забора на 160-й секунде стояли
# в 0,5–0,9 м за боковой и попадали «на поле» из-за запаса 1 м в to_metres.
BAND, SLOW = 0.8, 1.0
pts = defaultdict(list)
for f in met["frames"]:
    for tid, X, Y, ins in f["players"]:
        pts[int(tid)].append((X, Y))
def standing_at_edge(t):
    a = pts[t]
    if len(a) < fps: return False
    band = sum(1 for X, Y in a if X < BAND or X > 19 - BAND or Y < BAND or Y > 43.08 - BAND) / len(a)
    steps = sorted(((a[i][0] - a[i - 1][0]) ** 2 + (a[i][1] - a[i - 1][1]) ** 2) ** 0.5 for i in range(1, len(a)))
    return band >= OFF_SHARE and steps[len(steps) // 2] * fps < SLOW
# Исключение — ввод из аута. Игрок выходит за линию на несколько секунд,
# чтобы вбросить мяч, и трекер может завести на него отдельный короткий трек
# целиком «вне поля». Признаки, что это игрок, а не зритель:
#   а) в этом же треке он успел побегать по полю (≥2 с на поле со скоростью
#      выше 1 м/с);
#   б) он стоит близко к линии (до 2,5 м) и мяч рядом с ним (до 2 м) хотя бы
#      полсекунды — вбрасывает.
balls = [f.get("ball") for f in met["frames"]]
idx_of = defaultdict(list)
for k, f in enumerate(met["frames"]):
    for tid, X, Y, ins in f["players"]:
        idx_of[int(tid)].append((k, X, Y, ins))
def throw_in(t):
    pts = idx_of[t]
    # скорость по смещению за 0,5 с, а не по соседним кадрам: дрожание рамки
    # стоящего человека даёт 1 м/с между кадрами (так запасной с сложенными
    # руками проходил как «бегал»)
    w = int(0.5 * fps); fast_on = 0
    for i in range(w, len(pts)):
        a, b = pts[i - w], pts[i]
        if a[3] and b[3] and b[0] - a[0] <= w + 2 and ((b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2) ** 0.5 / 0.5 > 1.5:
            fast_on += 1
    if fast_on >= 2 * fps: return True
    near = 0
    for k, X, Y, ins in pts:
        b = balls[k]
        if b and -2.0 <= X <= 21.0 and -2.0 <= Y <= 45.1 and ((X - b[0]) ** 2 + (Y - b[1]) ** 2) ** 0.5 < 1.2:
            near += 1
    return near >= 1.0 * fps
sideline = set(t for t in set(on) | set(off)
               if t not in gk and on[t] + off[t] >= fps
               and (off[t] >= OFF_SHARE * (on[t] + off[t]) or standing_at_edge(t))
               and not throw_in(t))
#   в) продолжение: короткий трек (до 15 с) начинается там, где за секунды до
#      этого оборвался трек игрока (не зрителя) — трекер завёл на вышедшего
#      за линию человека новый номер. На 52-й секунде синий вышел к бровке
#      после аута, стоял 4,6 с и уходил в зрители.
players = [t for t in idx_of if t not in sideline and team.get(t, -1) in (0, 1) and on[t] >= 2 * fps]
ends = [(idx_of[t][-1][0], idx_of[t][-1][1], idx_of[t][-1][2]) for t in players]
def continuation(t):
    k0, X0, Y0, _ = idx_of[t][0]
    if idx_of[t][-1][0] - k0 > 15 * fps: return False
    return any(-5 * fps <= k0 - ke <= 1 * fps and ((X0 - Xe) ** 2 + (Y0 - Ye) ** 2) ** 0.5 < 3.0 for ke, Xe, Ye in ends)
sideline = [t for t in sideline if not continuation(t)]
for t in sideline:
    team[t] = -1
    if nums.get(t): nums[t] = dict(nums[t], number=None, sideline=True)

# 2. редкие номера
total = defaultdict(float)
for t, n in on.items():
    if team.get(t) in (0, 1) and number(t) and t not in gk:
        total[(team[t], number(t))] += n / fps
rare = 0
for t in list(nums):
    if t in gk or team.get(t) not in (0, 1) or not number(t): continue
    if total[(team[t], number(t))] < MIN_SEC:
        nums[t] = dict(nums[t], number=None, rare=True); rare += 1

# 3. одновременные одинаковые номера
conflict = Counter()
for f in met["frames"]:
    seen = defaultdict(list)
    for tid, X, Y, ins in f["players"]:
        tid = int(tid)
        if ins and team.get(tid) in (0, 1) and number(tid):
            seen[(team[tid], number(tid))].append(tid)
    for grp in seen.values():
        if len(grp) > 1:
            # вратарь (номер 1) всегда прав: полевой «№1» рядом с ним — ошибка чтения
            big = max(grp, key=lambda t: (t in gk, on[t]))
            for t in grp:
                if t != big and t not in gk: conflict[t] += 1
dup = 0
for t, c in conflict.items():
    if c >= CONFLICT_SHARE * on[t]:
        nums[t] = dict(nums[t], number=None, conflict=True); dup += 1

for d in (pos, met):
    d["team_of"] = {str(k): v for k, v in team.items()}
    d["numbers"] = {str(k): v for k, v in nums.items()}
    d["sideline"] = sorted(sideline)
json.dump(pos, open(pos_path, "w"), ensure_ascii=False)
json.dump(met, open(met_path, "w"), ensure_ascii=False)
print("за бровкой %d треков, редких номеров снято %d, дублей снято %d" % (len(sideline), rare, dup))
for tm in (0, 1):
    print(" команда %d: %s" % (tm, sorted([(n, round(s)) for (t_, n), s in total.items() if t_ == tm and s >= MIN_SEC], key=lambda x: -x[1])))
