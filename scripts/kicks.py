"""События по физике мяча: удар, полёт, приём. Не по тому, кто ближе.

Почему не «ближайший к мячу»: когда у мяча толкутся двое, ближайший меняется
каждые несколько кадров, и каждая смена рождает ложное событие, а тип паса или
потери зависит от случайного момента. Замерено: склейка треков не помогает,
потому что владелец прыгает между разными людьми, а не между обрывками одного.

Здесь событие — это удар: мяч резко ускоряется, летит по прямой, замедляется у
другого игрока. Отправитель — кто был рядом в момент удара, получатель — у кого
мяч остановился. Толкучка со стоящим мячом событий не порождает.

    python kicks.py pos.json out.json [--v-kick 9] [--v-stop 4] [--near 150]
"""
import json, sys, math

pos_path, out_path = sys.argv[1], sys.argv[2]
def opt(n, d):
    return float(sys.argv[sys.argv.index(n) + 1]) if n in sys.argv else d
V_KICK = opt("--v-kick", 9.0)     # px за кадр: быстрее — мяч точно бьют
V_STOP = opt("--v-stop", 4.0)     # px за кадр: медленнее — мяч принят или стоит
NEAR = opt("--near", 300.0)       # px: кто считается «рядом» с мячом
MIN_FLIGHT = 3                    # кадров: короче — это не полёт, а дрожь
MIN_FLIGHT_PX = opt("--min-px", 120.0)
CAM = sys.argv[sys.argv.index("--camera") + 1] if "--camera" in sys.argv else ""
# Остановки игры. По эталону 34 % времени клипа — ауты, штрафные, споры с судьёй,
# и 40 % найденных событий приходились именно на них: мяч носят в руках, пинают
# в сторону, а система видит «пасы». Признак остановки — игроки не бегут:
# средняя скорость игроков (с поправкой на камеру) ниже STOP_SPEED дольше STOP_SEC.
STOP_SPEED = opt("--stop-speed", 60.0)   # px/с в среднем по игрокам в кадре
STOP_SEC = opt("--stop-sec", 2.5)
MERGE_SEC = opt("--merge-sec", 0.8)      # два удара ближе этого с общим участником — один удар
GAP_FILL = 6                      # кадров: такие дыры в траектории заполняем линейно

pos = json.load(open(pos_path))
frames, fps = pos["frames"], pos["fps"]
team = {int(k): v for k, v in pos["team_of"].items()}
NAMES = {int(k): v for k, v in (pos.get("team_names") or {}).items()}
NUM = {int(k): v.get("number") for k, v in (pos.get("numbers") or {}).items()}
name = lambda t: NAMES.get(t, "команда %s" % t)

# --- траектория мяча с заполнением коротких дыр ------------------------------
ball = [f["ball"] for f in frames]
n = len(ball)
i = 0
while i < n:
    if ball[i] is None:
        j = i
        while j < n and ball[j] is None:
            j += 1
        if 0 < i and j < n and (j - i) <= GAP_FILL:
            a, b = ball[i - 1], ball[j]
            for k in range(i, j):
                t = (k - i + 1) / (j - i + 1)
                ball[k] = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
        i = j
    else:
        i += 1

# Камера в этой съёмке двигается 78 % времени (медиана 35 px за 1/6 с). Панорама
# превращает стоящий мяч в «летящий», и детектор ударов видит удар там, где
# двинулась камера. Вычитаем сдвиг камеры между кадрами из смещения мяча.
cam = None
if CAM:
    c = json.load(open(CAM))
    cstep = max(1, int(round(c["fps"] / fps)))     # камера считалась на 60 fps, позиции — на 30
    sh = c["shift"]
    cam = []
    for k in range(n):
        dx = dy = 0.0
        for j in range(k * cstep + 1, min(len(sh), (k + 1) * cstep + 1)):
            dx += sh[j][0]; dy += sh[j][1]
        cam.append((dx, dy))

def speed(k):
    if k <= 0 or ball[k] is None or ball[k - 1] is None:
        return None
    dx = ball[k][0] - ball[k - 1][0]
    dy = ball[k][1] - ball[k - 1][1]
    if cam is not None:
        dx -= cam[k][0]; dy -= cam[k][1]
    return math.hypot(dx, dy)

# сглаживаем скорость окном в 3 кадра, иначе дрожь детекции даёт ложные удары
raw = [speed(k) for k in range(n)]
sp = []
for k in range(n):
    w = [raw[j] for j in (k - 1, k, k + 1) if 0 <= j < n and raw[j] is not None]
    sp.append(sum(w) / len(w) if w else None)

SEARCH = 15                       # кадров: сколько искать игрока вокруг удара и приёма

def nearest_at(k, b):
    """Ближайший к точке b игрок с командой в кадре k."""
    best, bd = None, 1e9
    for tid, x, y in frames[k]["players"]:
        t = int(tid)
        if team.get(t, -1) not in (0, 1):
            continue
        d = math.hypot(x - b[0], y - b[1])
        if d < bd:
            best, bd = t, d
    return best, bd

def nearest(k, direction):
    """Кто был у мяча около кадра k. Разбор показал: в момент удара мяч чаще
    всего не виден — он у ноги, закрыт игроком, и появляется уже в полёте. Поэтому
    берём последнюю известную точку мяча до удара (или первую после приёма) и
    ищем игрока в окне кадров вокруг неё, а не строго в одном кадре."""
    ref = None
    for j in range(k, k - direction * SEARCH, -direction):
        if 0 <= j < n and ball[j] is not None:
            ref = ball[j]; break
    if ref is None:
        return None, None
    best, bd = None, 1e9
    for j in range(k - SEARCH, k + SEARCH + 1):
        if 0 <= j < n:
            t, d = nearest_at(j, ref)
            if t is not None and d < bd:
                best, bd = t, d
    return (best, bd) if bd <= NEAR else (None, None)

# --- где игра стоит --------------------------------------------------------------
def player_speed(k):
    """Средняя скорость игроков между кадрами k-1 и k, px/с, с поправкой на камеру."""
    if k <= 0:
        return None
    prev = {int(t): (x, y) for t, x, y in frames[k - 1]["players"]}
    cx, cy = cam[k] if cam is not None else (0.0, 0.0)
    v = []
    for t, x, y in frames[k]["players"]:
        p = prev.get(int(t))
        if p:
            v.append(math.hypot(x - p[0] - cx, y - p[1] - cy) * fps)
    return sum(v) / len(v) if v else None

W = int(fps)   # окно в 1 с
ps = [player_speed(k) for k in range(n)]
smooth = []
for k in range(n):
    w = [ps[j] for j in range(max(0, k - W), min(n, k + W + 1)) if ps[j] is not None]
    smooth.append(sum(w) / len(w) if w else None)
dead = [False] * n
run = 0
for k in range(n):
    slow = smooth[k] is not None and smooth[k] < STOP_SPEED
    run = run + 1 if slow else 0
    if run >= STOP_SEC * fps:
        for j in range(k - run + 1, k + 1):
            dead[j] = True
dead_share = 100 * sum(dead) / max(n, 1)

# --- ищем удары ---------------------------------------------------------------
events = []
k = 1
while k < n:
    if sp[k] is None or sp[k] < V_KICK:
        k += 1
        continue
    # начало полёта: последний медленный кадр перед разгоном
    start = k
    while start > 0 and sp[start - 1] is not None and sp[start - 1] >= V_STOP:
        start -= 1
    # конец полёта: первый медленный кадр после
    end = k
    while end + 1 < n and sp[end + 1] is not None and sp[end + 1] >= V_STOP:
        end += 1
    if end - start >= MIN_FLIGHT:
        sender, _ = nearest(start, +1)      # ищем назад от старта
        receiver, _ = nearest(end, -1)      # ищем вперёд от конца
        dist = math.hypot(ball[end][0] - ball[start][0], ball[end][1] - ball[start][1]) \
            if ball[end] and ball[start] else 0
        # Короткие рывки — не передачи: мяч дрожит у ноги или отскакивает.
        if dead[start]:
            k = end + 1
            continue                       # игра стоит: это не событие
        if sender is not None and receiver is not None and sender != receiver and dist >= MIN_FLIGHT_PX:
            ts, tr = team.get(sender, -1), team.get(receiver, -1)
            kind = "пас" if ts == tr else "потеря"
            events.append({"t": round(start / fps, 2), "t_end": round(end / fps, 2),
                           "frame": start, "kind": kind,
                           "from_track": sender, "to_track": receiver,
                           "from_number": NUM.get(sender), "to_number": NUM.get(receiver),
                           "team": name(tr), "team_id": tr,
                           "flight_px": round(dist), "flight_sec": round((end - start) / fps, 2)})
    k = end + 1

# Один пас, разорванный пропуском кадра, даёт два «удара» подряд с общим
# участником. Сливаем: оставляем первый, конец берём у последнего.
merged = []
for e in events:
    if merged and e["t"] - merged[-1]["t_end"] <= MERGE_SEC and \
       (e["from_track"] == merged[-1]["to_track"] or e["from_track"] == merged[-1]["from_track"]):
        merged[-1]["t_end"] = e["t_end"]
        merged[-1]["to_track"] = e["to_track"]; merged[-1]["to_number"] = e["to_number"]
        tb = team.get(e["to_track"], -1); ta = team.get(merged[-1]["from_track"], -1)
        merged[-1]["kind"] = "пас" if ta == tb else "потеря"
        merged[-1]["team"] = name(tb); merged[-1]["team_id"] = tb
        continue
    merged.append(e)
events = merged

# накопительные счётчики для просмотрщика
from collections import Counter
cnt = Counter()
for e in events:
    cnt[e["kind"]] += 1
    e["passes_total"] = cnt["пас"]; e["losses_total"] = cnt["потеря"]
    e["possession"] = {"A": 0, "B": 0}

out = {"title": "события по ударам", "fps": fps, "duration_sec": round(n / fps, 1),
       "summary": {"passes": cnt["пас"], "losses": cnt["потеря"], "owner_changes": len(events),
                   "ball_found_pct": round(100 * sum(1 for b in ball if b) / n, 1),
                   "possession_pct": {}, "vote_stability": None},
       "events": events}
json.dump(out, open(out_path, "w"), ensure_ascii=False, indent=1)
print("ударов найдено %d: пасов %d, потерь %d | игра стояла %.0f %% времени" % (len(events), cnt["пас"], cnt["потеря"], dead_share))
