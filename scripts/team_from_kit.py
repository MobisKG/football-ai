"""Назначает команду по цвету формы, названному моделью, а не по кластеризации.

Кластеризация по внешнему виду ошибается: на проверке она отнесла к синей
команде 18 треков, у которых форма прямо названа «красный белый». Отсюда и
путаница паса с потерей — тип события определяется только командами участников.

Модель, читающая номер, заодно называет цвет формы словами. Первое цветовое
слово и есть команда: синие против бело-красных. Всё, что не попало ни в одну
группу, — вратари, судьи, зрители, и они не участвуют в событиях.

    python team_from_kit.py pos.json numbers.json out.json
"""
import json, sys
from collections import Counter

pos_path, num_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
pos = json.load(open(pos_path))
nums = json.load(open(num_path))

# Цвета команд больше не зашиты (было «синие против красных» клипа 5): две
# команды — два самых частых первых цветовых слова формы у треков. Форма
# «красный белый» и «белый красный» — одна, поэтому считаем по первому слову,
# а белый у красно-белых сливается с красным через пары слов ниже.
import re
def words(k):
    return re.findall(r"[а-яёa-z]+", (k or "").lower())
STEM = lambda w: w[:4]
first_words = Counter()
for v in nums.values():
    w = [x for x in words(v.get("kit")) if x not in ("и", "с")]
    if w: first_words[STEM(w[0])] += 1
top = [w for w, _ in first_words.most_common(2)]
# Один цвет формы подавляет остальные — модель прочитала обе команды одинаково
# («Манас Талас vs Iron Mix»: «синий» у 72 % треков, в 8,2 раза чаще второго цвета;
# у нормальных игр ночи 15.09 не больше 2,8). Тогда деление по форме ломается,
# и оставляем исходные команды кластеризации analyze_match.py.
_fw = first_words.most_common(2)
DOMINANT = len(_fw) == 2 and _fw[0][1] >= 4 * _fw[1][1]
if DOMINANT:
    print("один цвет формы подавляет остальные (%s): оставляю команды кластеризации" % dict(first_words.most_common(3)))
if len(top) < 2: top = (top + ["?", "?"])[:2]
A_WORDS = (top[0],)          # команда 0
B_WORDS = (top[1],)          # команда 1
print("цвета команд по форме:", dict(first_words.most_common(4)))

# Редкие расцветки — это не форма команды, а вратарь, судья или ошибка чтения.
# На проверке вратарь синих в голубой куртке был прочитан как «красный зелёный»
# и попал в красную команду: в ленте появился «пас красных номеру 9», где
# девятка — вратарь соперника. Команду задают только массовые расцветки.
# Строгий режим включается флагом: он убирает вратарей в чужой одежде из
# команд, но на эталоне режет найденные события вдвое, поэтому по умолчанию выключен.
STRICT = "--strict-kits" in sys.argv
MIN_SHARE = 0.05 if STRICT else 0.0

def team_of_kit(kit):
    k = (kit or "").strip().lower()
    if not k:
        return -1
    first = k.split()[0]
    if any(first.startswith(w) for w in A_WORDS):
        return 0
    if any(first.startswith(w) for w in B_WORDS):
        return 1
    # цвет мог встать вторым словом: «белый красный»
    if any(w in k for w in B_WORDS):
        return 1
    if any(w in k for w in A_WORDS):
        return 0
    return -1

# Запасные и тренеры стоят на газоне у бровки и попадают в детекцию. Разбор
# на арендованной машине их отсеивал, но назначение команды по форме это
# перебивало: запасной в бело-красной форме снова становился игроком, и в ленте
# появлялись «передачи» от того, кто вообще не играл. Считаем скорость трека
# прямо здесь и возвращаем отсев: кто почти не двигается, тот не игрок.
MIN_SPEED = float(sys.argv[sys.argv.index("--min-speed") + 1]) if "--min-speed" in sys.argv else 12.0
fps = pos["fps"]
track_xy = {}
for i, f in enumerate(pos["frames"]):
    for tid, x, y in f["players"]:
        track_xy.setdefault(int(tid), []).append((i, x, y))

def median_speed(tid):
    pts = track_xy.get(tid, [])
    if len(pts) < 8:
        return float("inf")      # слишком короткий трек: скорость не измерить
    sp = []
    for (k1, x1, y1), (k2, x2, y2) in zip(pts, pts[1:]):
        dt = (k2 - k1) / fps
        if dt > 0:
            sp.append((((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5) / dt)
    sp.sort()
    return sp[len(sp) // 2] if sp else 0.0

kit_counts = Counter()
for v in nums.values():
    k = (v.get("kit") or "").strip().lower()
    if k:
        kit_counts[k] += 1
total_kits = sum(kit_counts.values()) or 1
COMMON = {k for k, c in kit_counts.items() if c / total_kits >= MIN_SHARE}
print("массовые расцветки: %s" % sorted(COMMON))

old = {int(k): v for k, v in pos["team_of"].items()}
new = {}
changed = 0
for tid in old:
    v = nums.get(str(tid))
    kit = (v.get("kit") or "").strip().lower() if v else ""
    t = team_of_kit(kit) if kit in COMMON else -1
    if DOMINANT:
        t = old[tid] if old[tid] in (0, 1) else -1
    if v and str(v.get("role", "")).startswith("вратар"):
        t = -1                     # вратарь в своей форме, владение ему не приписываем
    if median_speed(tid) < MIN_SPEED:
        t = -1                     # стоит на месте: запасной, тренер или зритель
    new[tid] = t
    if t != old[tid]:
        changed += 1

pos["team_of"] = {str(k): v for k, v in new.items()}
NAMES = {"син": "синие", "сини": "синие", "голу": "голубые", "крас": "красные", "бел": "белые", "белы": "белые", "черн": "чёрные",
         "жёлт": "жёлтые", "желт": "жёлтые", "зелё": "зелёные", "зеле": "зелёные", "оран": "оранжевые", "фиол": "фиолетовые",
         "серы": "серые", "розо": "розовые", "бord": "бордовые", "борд": "бордовые"}
pos["team_names"] = {"0": NAMES.get(A_WORDS[0], A_WORDS[0]), "1": NAMES.get(B_WORDS[0], B_WORDS[0])}
if DOMINANT:
    # названия — по цвету футболок из кластеризации (analyze_match.py), и они должны различаться
    tn = {str(k): v for k, v in (pos.get("team_names") or {}).items()}
    if tn.get("0") and tn.get("1") and tn["0"] != tn["1"]:
        pos["team_names"] = {"0": tn["0"], "1": tn["1"]}
    else:
        pos["team_names"] = {"0": "команда A", "1": "команда B"}
# Номера кладём рядом с позициями: расчёт событий по ним отсеивает переходы
# «сам себе», когда один игрок разорван на два трека.
pos["numbers"] = {str(t): {"number": (nums.get(str(t)) or {}).get("number"),
                           "kit": ((nums.get(str(t)) or {}).get("kit") or "").strip().lower()}
                  for t in old if (nums.get(str(t)) or {}).get("number") is not None}
json.dump(pos, open(out_path, "w"), ensure_ascii=False)
c_old, c_new = Counter(old.values()), Counter(new.values())
print("было: %s" % dict(c_old))
print("стало: %s  (изменено треков: %d)" % (dict(c_new), changed))
