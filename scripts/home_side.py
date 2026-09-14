"""Кто хозяева по цвету формы и какая строка табло — хозяева.

    python home_side.py <папка результата> <matches.json>

В папке: stats.json (цвета команд и номера с временем на поле), scoreboard.json
(названия команд на табло, верхняя строка — team 1), pos_final.json. Рядом с
matches.json лежит rosters.json — составы команд с сайта (номера игроков).

Цвет хозяев определяется по составам: у каждой команды свои номера на сайте,
у каждого цвета — прочитанные с футболок номера с временем на поле. Считаем,
сколько секунд «своих» номеров у каждого варианта раскладки; номера, которые
есть в обоих составах, не различают команды и не считаются, если известны оба
состава. Проверено на Gz United vs Real 91: верный вариант 2890 с против 1532.

Строка табло сопоставляется с названием хозяев/гостей по похожести букв
(табло читается латиницей с ошибками: «PEAL 91» — это «Реал-91»).

Итог в home.json: match_guid, home_colour, home_is_top, confident, reason.
Если сомнение — confident=false, загружать нельзя, решает человек утром.
"""
import json, os, re, sys, difflib

d, matches_path = sys.argv[1], sys.argv[2]
slug = os.path.basename(os.path.abspath(d))
M = json.load(open(matches_path)); rosters = json.load(open(os.path.join(os.path.dirname(os.path.abspath(matches_path)), "rosters.json")))
v = next((x for x in M["videos"] if x["slug"] == slug), None) or {}
stats = json.load(open(os.path.join(d, "stats.json")))
sb = json.load(open(os.path.join(d, "scoreboard.json"))) if os.path.exists(os.path.join(d, "scoreboard.json")) else None
out = {"slug": slug, "match_guid": v.get("match_guid"), "home": v.get("home"), "away": v.get("away"),
       "home_colour": None, "home_is_top": None, "confident": False, "reason": ""}

TR = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
      "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sh",
      "ы": "y", "э": "e", "ю": "yu", "я": "ya", "ө": "o", "ү": "u", "ң": "n", "ь": "", "ъ": ""}
def norm(s):
    s = (s or "").lower(); s = "".join(TR.get(c, c) for c in s)
    return re.sub(r"[^a-z0-9]", "", s)
def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b: return 0.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    if a in b or b in a: r = max(r, 0.8)
    return r

# --- 1. матч для безымянного видео: по названиям на табло ---
sb_names = [t for t in (sb or {}).get("teams") or [] if t]
if not out["match_guid"] and len(sb_names) == 2:
    best = []
    for c in M.get("candidates_for_untitled", []):
        s1 = sim(sb_names[0], c["home"]) + sim(sb_names[1], c["away"]); s2 = sim(sb_names[0], c["away"]) + sim(sb_names[1], c["home"])
        best.append((max(s1, s2), c))
    best.sort(key=lambda x: -x[0])
    if best and best[0][0] >= 1.2 and (len(best) == 1 or best[0][0] - best[1][0] >= 0.3):
        c = best[0][1]; out.update(match_guid=c["match_guid"], home=c["home"], away=c["away"]); v = dict(v, **c)
        out["reason"] += "матч по табло: %s vs %s (%.2f); " % (c["home"], c["away"], best[0][0])
    else:
        out["reason"] += "матч по табло %s не найден среди кандидатов (%s); " % (sb_names, [(round(s, 2), c["home"]) for s, c in best[:3]])
if not out["match_guid"]:
    out["reason"] += "нет match_guid"; json.dump(out, open(os.path.join(d, "home.json"), "w"), ensure_ascii=False, indent=1); print(json.dumps(out, ensure_ascii=False)); sys.exit(0)

# --- 2. какая строка табло — хозяева ---
if len(sb_names) == 2:
    s_top_home = sim(sb_names[0], v["home"]) + sim(sb_names[1], v["away"])
    s_top_away = sim(sb_names[0], v["away"]) + sim(sb_names[1], v["home"])
    if abs(s_top_home - s_top_away) >= 0.25 and max(s_top_home, s_top_away) >= 0.9:
        out["home_is_top"] = s_top_home > s_top_away
        out["reason"] += "табло %s: хозяева %s (%.2f/%.2f); " % (sb_names, "сверху" if out["home_is_top"] else "снизу", s_top_home, s_top_away)
    else:
        out["reason"] += "табло %s не сопоставилось с %s/%s (%.2f/%.2f); " % (sb_names, v["home"], v["away"], s_top_home, s_top_away)
    # счёт табло против счёта судьи — для отчёта
    fs = sb.get("final_score") or [None, None]
    if out["home_is_top"] is not None and v.get("home_goals") is not None:
        exp = [v["home_goals"], v["away_goals"]] if out["home_is_top"] else [v["away_goals"], v["home_goals"]]
        out["score_match"] = (list(fs) == exp); out["reason"] += "счёт табло %s, судьи %s; " % (fs, exp)
else:
    out["reason"] += "табло не прочитано; "

# --- 3. цвет хозяев по составам ---
colours = list(stats["possession_pct"])
secs = {c: {} for c in colours}
for c in colours:
    for p in stats["teams"].get(c, {}).get("players", []):
        n = p["player"][1:]
        if n.isdigit(): secs[c][int(n)] = secs[c].get(int(n), 0) + p["time_on_pitch_s"]
# «10» и «1» не считаем: Qwen отвечает «10», не видя цифр, а «1» читает с табло и
# обрывков 7/11/14 (см. apply_numbers.py). На Талас 91 vs 361 синие получили один «№10»
# на 570 с, и это чуть не решило, кто хозяева.
UNRELIABLE = {0, 1, 10}
rh = set((rosters.get(v.get("home_guid")) or {}).get("numbers") or []) - UNRELIABLE
ra = set((rosters.get(v.get("away_guid")) or {}).get("numbers") or []) - UNRELIABLE
used = {}                     # (цвет, состав) -> различающие номера, давшие вклад
def score(col, ros, other):
    own = ros - other if (ros and other) else ros
    hit = {n: s for n, s in secs[col].items() if n in own}
    used[(col, id(ros))] = set(hit)
    return sum(hit.values())
if len(colours) == 2 and (rh or ra):
    A = score(colours[0], rh, ra) + score(colours[1], ra, rh)      # цвет 0 — хозяева
    B = score(colours[0], ra, rh) + score(colours[1], rh, ra)      # цвет 1 — хозяева
    out["roster_scores"] = {colours[0] + "=хозяева": round(A), colours[1] + "=хозяева": round(B)}
    hi, lo = max(A, B), min(A, B)
    # сколько разных номеров подтверждают победивший вариант: один игрок — не доказательство
    win = [(colours[0], rh), (colours[1], ra)] if A >= B else [(colours[0], ra), (colours[1], rh)]
    support = set().union(*[{(c, n) for n in used.get((c, id(r)), set())} for c, r in win])
    out["support_numbers"] = sorted("%s №%d" % cn for cn in support)
    decided = hi >= 120 and hi >= 1.6 * lo and len(support) >= 2
    if hi >= 120 and hi >= 1.6 * lo and len(support) < 2:
        out["reason"] += "перевес держится на одном номере %s — мало; " % out["support_numbers"]
    if decided and not (rh and ra):
        # Состав только одной команды: номера 1–20 есть почти в любом составе, и перевес
        # по совпадениям может выйти случайно. Дополнительно требуем, чтобы у цвета этой
        # команды доля времени номеров ИЗ её состава была заметно выше, чем у другого цвета.
        known = rh or ra
        share = {}
        for c in colours:
            tot = sum(secs[c].values()) or 1
            tot = sum(t for n, t in secs[c].items() if n not in UNRELIABLE) or 1
            share[c] = sum(t for n, t in secs[c].items() if n in known) / tot
        known_col = max(colours, key=lambda c: share[c])
        other = [c for c in colours if c != known_col][0]
        out["roster_share"] = {c: round(share[c], 2) for c in colours}
        # цвет известной команды по совпадениям должен совпасть с цветом по доле, и разрыв ≥ 0,15
        by_sum = colours[0] if (A > B) == bool(rh) else colours[1]
        if share[known_col] - share[other] < 0.15 or by_sum != known_col:
            decided = False
            out["reason"] += "один состав: доли номеров из состава %s — разрыв мал или противоречит; " % out["roster_share"]
    if decided:
        out["home_colour"] = colours[0] if A > B else colours[1]
        out["reason"] += "по составам: %s хозяева (%d с против %d с)%s; " % (out["home_colour"], hi, lo, "" if (rh and ra) else ", состав только одной команды")
    else:
        out["reason"] += "составы не различают цвета (%d с против %d с); " % (hi, lo)
else:
    out["reason"] += "нет составов на сайте (%s / %s); " % (len(rh), len(ra))

# Табло, чей итоговый счёт не совпал со счётом судьи, не используем: на
# «Михайловка vs Баш Кара Суу» табло показало 0 : 2 при счёте судьи 5 : 1 в пользу
# Михайловки. Голы с такого табло на сайт не грузим (home_is_top = None, и batch_run
# не передаёт --scoreboard), а статистику по цвету — грузим, если цвет надёжен.
if out.get("score_match") is False:
    out["scoreboard_rejected"] = True; out["home_is_top"] = None
    out["reason"] += "табло отброшено: счёт не совпал с судейским, голы не грузим; "
out["confident"] = bool(out["home_colour"]) and (sb is None or out["home_is_top"] is not None or out.get("scoreboard_rejected", False))
if out["home_colour"] and sb is not None and out["home_is_top"] is None and not out.get("scoreboard_rejected"):
    out["reason"] += "цвет есть, но строки табло не сопоставлены — без табло голы не загрузить; "
json.dump(out, open(os.path.join(d, "home.json"), "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False))
