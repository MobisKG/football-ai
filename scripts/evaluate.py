"""Сверяет найденные события с эталоном, размеченным человеком.

Событие считается угаданным, если найденное совпадает с эталонным по времени
с допуском TOL секунд. Совпадение ищется жадно, каждое эталонное событие можно
закрыть только одним найденным, иначе точность завышается.

    python evaluate.py out/events.json truth/эталон.json [допуск_сек]
"""
import json, sys

ev = json.load(open(sys.argv[1]))
gt = json.load(open(sys.argv[2]))
TOL = float(sys.argv[3]) if len(sys.argv) > 3 else 2.5
lo, hi = gt["covers_sec"]

# в эталоне есть ауты и удары — переходов владения между игроками там нет,
# система их не умеет находить в принципе, поэтому считаем отдельно
KNOWN = {"пас", "потеря"}
truth = [e for e in gt["events"] if lo <= e["t"] <= hi and e["kind"] in KNOWN]
other = [e for e in gt["events"] if lo <= e["t"] <= hi and e["kind"] not in KNOWN]
found = [e for e in ev["events"] if lo <= e["t"] <= hi]

# Сопоставление должно быть оптимальным, а не жадным. На проверке жадное
# сопоставление отдало находку 8,6 с эталонному событию на 8-й секунде, из-за
# чего следующая пара тоже разъехалась, и верные по сути события засчитались
# как ошибка по команде. Считаем цену всех пар и разбираем от дешёвых к дорогим,
# учитывая не только время, но и совпадение типа и команды.
cost = []
for gi, g in enumerate(truth):
    for fi, f in enumerate(found):
        d = abs(f["t"] - g["t"])
        if d > TOL:
            continue
        c = d
        if f["kind"] != g["kind"]:
            c += 0.8
        if f["team"] != g["team"]:
            c += 0.8
        cost.append((c, d, gi, fi))
cost.sort()
used, taken = set(), {}
for c, d, gi, fi in cost:
    if gi in taken or fi in used:
        continue
    taken[gi] = (fi, d)
    used.add(fi)
pairs = []
for gi, g in enumerate(truth):
    if gi in taken:
        fi, d = taken[gi]
        pairs.append((g, found[fi], d))
    else:
        pairs.append((g, None, None))

hit = [p for p in pairs if p[1]]
miss = [p for p in pairs if not p[1]]
extra = [f for i, f in enumerate(found) if i not in used]
kind_ok = [p for p in hit if p[1]["kind"] == p[0]["kind"]]
team_ok = [p for p in hit if p[1]["team"] == p[0]["team"]]

print("=" * 62)
print("Сверка с эталоном, допуск %.1f с, отрезок %d–%d с" % (TOL, lo, hi))
print("=" * 62)
print("в эталоне переходов мяча   %d  (плюс %d удар/аут, их система не ищет)" % (len(truth), len(other)))
print("система нашла событий      %d" % len(found))
print("-" * 62)
print("совпало по времени         %d из %d  (%.0f %%)" % (len(hit), len(truth), 100 * len(hit) / max(len(truth), 1)))
print("  из них верный тип        %d  (%.0f %%)" % (len(kind_ok), 100 * len(kind_ok) / max(len(hit), 1)))
print("  из них верная команда    %d  (%.0f %%)" % (len(team_ok), 100 * len(team_ok) / max(len(hit), 1)))
print("пропущено                  %d" % len(miss))
print("лишних, которых не было    %d" % len(extra))
print("-" * 62)
for g, f, d in pairs:
    if f:
        mark = "OK " if (f["kind"] == g["kind"] and f["team"] == g["team"]) else "~  "
        print("%s %4.0f с  эталон: %-7s %-8s | найдено %5.1f с: %-7s %-8s #%d->#%d"
              % (mark, g["t"], g["kind"], g["team"], f["t"], f["kind"], f["team"],
                 f["from_track"], f["to_track"]))
    else:
        print("НЕТ %4.0f с  эталон: %-7s %-8s | не найдено" % (g["t"], g["kind"], g["team"]))
for f in extra:
    print("ЛИШ %4.1f с  найдено: %-7s %-8s #%d->#%d  (в эталоне ничего)"
          % (f["t"], f["kind"], f["team"], f["from_track"], f["to_track"]))
