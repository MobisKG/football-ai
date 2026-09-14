"""Превращает покадровые позиции в список событий с временными метками.

Считает ровно той же логикой, что и stats.py, чтобы цифры сходились:
владелец мяча — ближайший к нему игрок по ногам, смена владельца засчитывается
только после HOLD подряд кадров. Отличие одно: события не схлопываются в сумму,
а сохраняются с временем, чтобы их можно было сверять с видео.

    python events.py out/pos.json out/events.json "Авто-Рейс"
"""
import json, sys
from collections import Counter

src, dst = sys.argv[1], sys.argv[2]
title = sys.argv[3] if len(sys.argv) > 3 else "матч"

def _opt(n, d):
    return float(sys.argv[sys.argv.index(n) + 1]) if n in sys.argv else d

MAX_DIST = _opt("--max-dist", 170)
HOLD = int(_opt("--hold", 6))
CAM = sys.argv[sys.argv.index("--camera") + 1] if "--camera" in sys.argv else ""
# Треть времени игра стоит: ауты, штрафные, споры с судьёй. Там мяч носят в
# руках и пинают в сторону, а система видит «пасы». Отсекаем по скорости
# игроков с поправкой на движение камеры (она движется 78 % времени).
STOP_SPEED = _opt("--stop-speed", 0)     # 0 — отсев выключен
STOP_SEC = _opt("--stop-sec", 2.0)
# Мяч виден далеко не всегда: на проверенном клипе его находят в 31,6 % кадров,
# а самый длинный провал длится 17,7 с. Прежний расчёт всё это время продолжал
# записывать владение последнему хозяину, и получалось 96 % против 4 %.
# Держим владение только MAX_CARRY кадров после последнего появления мяча,
# дальше считаем, что владение неизвестно, и в статистику эти кадры не идут.
MAX_CARRY = 15   # полсекунды при 30 кадрах

d = json.load(open(src))
fps = d["fps"]
team_of = {int(k): v for k, v in d["team_of"].items()}
frames = d["frames"]

# Один игрок часто разорван на несколько треков, и переход мяча между его же
# обрывками выглядит как передача самому себе. На проверке такое событие стояло
# на 4:11: «номер 9 отдал пас номеру 9». Если у обоих участников прочитан
# ОДИН И ТОТ ЖЕ номер и цвет формы — это не событие.
NUM = {int(k): (v.get("number"), v.get("kit")) for k, v in (d.get("numbers") or {}).items()}

def same_person(a, b):
    ka, kb = NUM.get(a), NUM.get(b)
    return ka is not None and ka == kb

cam = None
if CAM:
    import math as _m
    _c = json.load(open(CAM))
    _step = max(1, int(round(_c["fps"] / fps)))
    _sh = _c["shift"]
    cam = []
    for k in range(len(frames)):
        dx = dy = 0.0
        for j in range(k * _step + 1, min(len(_sh), (k + 1) * _step + 1)):
            dx += _sh[j][0]; dy += _sh[j][1]
        cam.append((dx, dy))

dead = [False] * len(frames)
if STOP_SPEED > 0:
    import math as _m
    ps = [None]
    for k in range(1, len(frames)):
        prev = {int(t): (x, y) for t, x, y in frames[k - 1]["players"]}
        cx, cy = cam[k] if cam else (0.0, 0.0)
        v = [_m.hypot(x - p[0] - cx, y - p[1] - cy) * fps
             for t, x, y in frames[k]["players"] for p in [prev.get(int(t))] if p]
        ps.append(sum(v) / len(v) if v else None)
    W = int(fps)
    run = 0
    for k in range(len(frames)):
        w = [ps[j] for j in range(max(0, k - W), min(len(frames), k + W + 1)) if ps[j] is not None]
        sm = sum(w) / len(w) if w else None
        slow = sm is not None and sm < STOP_SPEED
        run = run + 1 if slow else 0
        if run >= STOP_SEC * fps:
            for j in range(k - run + 1, k + 1):
                dead[j] = True

raw = []
for f in frames:
    if f["ball"] is None or not f["players"]:
        raw.append(None); continue
    bx, by = f["ball"]
    best, bd = None, 1e9
    for tid, px, py in f["players"]:
        dist = ((px - bx) ** 2 + (py - by) ** 2) ** 0.5
        if dist < bd:
            best, bd = tid, dist
    raw.append(best if bd <= MAX_DIST else None)

owner, pending, pend_n = None, None, 0
smooth = []
since_ball = 0
for r, f in zip(raw, frames):
    if f["ball"] is None:
        since_ball += 1
    else:
        since_ball = 0
    if since_ball > MAX_CARRY:
        owner, pending, pend_n = None, None, 0
        smooth.append(None); continue
    if r is None:
        smooth.append(owner); continue
    if r == owner:
        pending, pend_n = None, 0
    else:
        pending, pend_n = (r, pend_n + 1) if r == pending else (r, 1)
        if pend_n >= HOLD:
            owner, pending, pend_n = r, None, 0
    smooth.append(owner)

# названия берём из разбора: они выведены из среднего цвета футболок, а не
# назначены по номеру группы, иначе подпись могла оказаться перевёрнутой
_n = {int(k): v for k, v in (d.get("team_names") or {}).items()}
TEAM_NAME = {0: _n.get(0, "команда A"), 1: _n.get(1, "команда B"), -1: "не игрок"}

events, prev, prev_i = [], None, 0
hold = Counter()
for i, s in enumerate(smooth):
    if s is not None and team_of.get(s, -1) in (0, 1):
        hold[team_of.get(s, -1)] += 1
    if s is not None and s != prev:
        if prev is not None and not dead[i]:
            ta, tb = team_of.get(prev, -1), team_of.get(s, -1)
            if ta not in (0, 1) or tb not in (0, 1):
                prev = s
                continue   # вратарь или посторонний: ни пас, ни потеря
            if same_person(prev, s):
                prev = s
                continue   # тот же игрок под другим номером трека
            kind = "пас" if ta == tb else "потеря"
            tot = sum(hold.values()) or 1
            events.append({
                "t": round(i / fps, 2),
                "frame": i,
                "kind": kind,
                "from_track": int(prev),
                "to_track": int(s),
                "team": TEAM_NAME[tb],
                "team_id": tb,
                "possession": {"A": round(100 * hold.get(0, 0) / tot, 1),
                               "B": round(100 * hold.get(1, 0) / tot, 1)},
            })
        prev, prev_i = s, i

# накопительные счётчики на момент каждого события — чтобы просмотрщик
# не пересчитывал их сам и показывал ровно то же, что итоговый отчёт
cnt = Counter()
for e in events:
    cnt[(e["kind"], e["team_id"])] += 1
    e["passes_total"] = sum(v for (k, _), v in cnt.items() if k == "пас")
    e["losses_total"] = sum(v for (k, _), v in cnt.items() if k == "потеря")

tot = sum(hold.values()) or 1
out = {
    "title": title,
    "fps": fps,
    "duration_sec": round(len(frames) / fps, 1),
    "summary": {
        "possession_known_pct": round(100 * sum(1 for s in smooth if s is not None) / len(frames), 1),
        "ball_found_pct": round(100 * sum(1 for f in frames if f["ball"] is not None) / len(frames), 1),
        "possession_pct": {TEAM_NAME[0]: round(100 * hold.get(0, 0) / tot, 1),
                           TEAM_NAME[1]: round(100 * hold.get(1, 0) / tot, 1)},
        "passes": sum(1 for e in events if e["kind"] == "пас"),
        "losses": sum(1 for e in events if e["kind"] == "потеря"),
        "owner_changes": len(events),
        "vote_stability": d.get("vote_stability"),
    },
    "events": events,
}
json.dump(out, open(dst, "w"), ensure_ascii=False, indent=1)
print("событий: %d, записано в %s" % (len(events), dst))
print(json.dumps(out["summary"], ensure_ascii=False))
