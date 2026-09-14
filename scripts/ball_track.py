"""Собирает связную траекторию мяча из всех кандидатов, а не жадно по кадрам.

Зачем: жадный выбор берёт лучшего кандидата в каждом кадре и допускает скачок
до 300 пикселей. Ложное срабатывание на заборе или на белой обуви такой фильтр
проходит, и в статистике появляется несуществующая смена владельца: на проверке
двое «владельцев» подряд стояли в 1100 пикселях друг от друга.

Здесь путь выбирается целиком по всему клипу динамическим программированием.
Каждый кадр — либо один из кандидатов, либо «мяча не видно». За скачок между
соседними кадрами платим штраф, за пропуск кадра платим фиксированную цену.
Выигрывает не самый яркий отклик, а самая правдоподобная траектория.

    python ball_track.py cands.json out.json [--vmax 45] [--miss 0.55]
"""
import json, sys, math

cands_path, out_path = sys.argv[1], sys.argv[2]
def opt(name, default):
    return float(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default

VMAX = opt("--vmax", 45.0)     # px за кадр: больше этого мяч не летает
MISS = opt("--miss", 0.55)     # цена кадра без мяча, в единицах отклика
JUMP = opt("--jump", 1.2)      # во сколько раз штрафовать перелёт сверх VMAX

d = json.load(open(cands_path))
N = d["num_frames"]
cands = {int(k): v for k, v in d["candidates"].items()}

# Отклик — это сумма веса пятна на тепловой карте, разброс огромный: от 0,5 до
# 256 при медиане 2,3. Нормируем по верхнему проценту, иначе цена «взять
# кандидата» почти всегда выше цены «пропустить кадр», и путь выходит пустым.
all_scores = sorted(c[2] for cs in cands.values() for c in cs)
REF = all_scores[int(0.75 * (len(all_scores) - 1))] if all_scores else 1.0
def det_cost(s):
    return max(0.0, 1.0 - s[2] / REF)

prev_states, prev_cost, prev_back = [None], [0.0], [None]
last_known = [None]          # для каждого состояния: (x, y, кадр) последней точки с мячом
back_all = []
state_all = []
for i in range(N):
    states = [None] + [tuple(c) for c in cands.get(i, [])]
    cost = []
    back = []
    for s in states:
        best, bi = float("inf"), 0
        for j, p in enumerate(prev_states):
            c = prev_cost[j]
            if s is None:
                c += MISS
            elif p is None:
                # Лазейка: перелёт через одиночный пропуск стоил дешевле штрафа за
                # скачок, и путь «телепортировался» через кадр без мяча. На 30 fps
                # это давало скорости 660 px/кадр у 5 % кадров. Теперь при повторном
                # подхвате платим за расстояние от последней известной точки.
                c += det_cost(s) + 0.2
                lk = last_known[j]
                if lk is not None:
                    gap_len = i - lk[2]
                    dist = math.hypot(s[0] - lk[0], s[1] - lk[1])
                    allowed = VMAX * gap_len
                    if dist > allowed:
                        c += JUMP * (dist / allowed - 1.0)
            else:
                dist = math.hypot(s[0] - p[0], s[1] - p[1])
                pen = 0.0 if dist <= VMAX else JUMP * (dist / VMAX - 1.0)
                c += det_cost(s) + pen
            if c < best:
                best, bi = c, j
        cost.append(best)
        back.append(bi)
    new_last = []
    for s_, bi in zip(states, back):
        new_last.append((s_[0], s_[1], i) if s_ is not None else last_known[bi])
    state_all.append(states)
    back_all.append(back)
    prev_states, prev_cost, prev_back = states, cost, back
    last_known = new_last

# обратный проход
k = min(range(len(prev_cost)), key=lambda j: prev_cost[j])
path = [None] * N
for i in range(N - 1, -1, -1):
    s = state_all[i][k]
    path[i] = None if s is None else [round(s[0], 1), round(s[1], 1), round(s[2], 3)]
    k = back_all[i][k]

found = sum(1 for p in path if p)
jumps = []
pts = [(i, p) for i, p in enumerate(path) if p]
for (i1, p1), (i2, p2) in zip(pts, pts[1:]):
    if i2 - i1 == 1:
        jumps.append(math.hypot(p2[0] - p1[0], p2[1] - p1[1]))
jumps.sort()
big = sum(1 for j in jumps if j > 150)
out = {"fps": d["fps"], "width": d["width"], "height": d["height"], "frames": path,
       "ball_found_pct": round(100 * found / N, 1)}
json.dump(out, open(out_path, "w"))
print("мяч найден в %.1f %% кадров | медиана скачка %.1f px | скачков >150px %.1f %%"
      % (out["ball_found_pct"], jumps[len(jumps)//2] if jumps else 0,
         100 * big / max(len(jumps), 1)))
