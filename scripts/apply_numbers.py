"""Переносит номера и цвета формы из read_numbers2 в файлы позиций.

Для каждого трека (кроме вратарей): номер — результат голосования по снимкам
(или пусто), форма — самый частый ответ. Команда пересчитывается по новой
форме: первое слово «синий/голубой» — синие, «красный/белый/бордовый» —
красные; если форму не разобрать, команда остаётся прежней.

    python apply_numbers.py pos.json pos_metres.json numbers2.json
"""
import json, re, sys
from collections import Counter

pos_path, met_path, num_path = sys.argv[1:4]
pos = json.load(open(pos_path)); met = json.load(open(met_path)); new = json.load(open(num_path))
team = {int(k): v for k, v in pos["team_of"].items()}
nums = {int(k): v for k, v in (pos.get("numbers") or {}).items()}
gk = set(int(k) for k in (pos.get("goalkeepers") or {}))
BLUE, RED = ("син", "голуб"), ("красн", "бел", "бордов")
# Голоса пересчитываются по ответам на отдельные снимки (per_shot), а не
# берутся готовыми из read_numbers2:
# 1) цвет формы сравнивается по команде, а не по первому слову: «белый и
#    красный», «красный, белый» и «красный и белый» — одна форма; раньше
#    запятая или порядок слов выкидывали верный голос;
# 2) голос за 10 весит 0,3. Не видя цифр, модель чаще всего отвечает «10»:
#    158 прочтений на клипе против 81 у следующего номера, и все треки с
#    итоговым «№10» оказались игроком в манишке без номера. В эталоне
#    пользователя №10 нет ни у одной команды.
DEFAULT_GUESS = {10: 0.3}
def kit_class(kit):
    w = re.findall(r"[а-яёa-z]+", (kit or "").lower())
    if not w: return None
    if w[0].startswith(("син", "голуб")): return "B"
    if w[0].startswith(("красн", "бел")): return "R"
    if w[0].startswith(("розов", "оранж")): return "O"
    return w[0][:4]
def revote(r):
    per = r.get("per_shot") or []
    if not per: return r.get("number"), r.get("votes") or {}
    cls = Counter(kit_class(p.get("kit")) for p in per if kit_class(p.get("kit")))
    main = cls.most_common(1)[0][0] if cls else None
    votes = Counter()
    for p in per:
        n = p.get("number")
        if not isinstance(n, int): continue
        c = kit_class(p.get("kit"))
        if main and c and c != main: continue
        votes[n] += DEFAULT_GUESS.get(n, 1.0)
    if not votes: return None, {}
    best, w = votes.most_common(1)[0]
    ok = w >= 2 and w > sum(votes.values()) / 2
    return (best if ok else None), {str(k): round(v, 1) for k, v in votes.items()}
changed = 0; got = 0
for k, r in new.items():
    t = int(k)
    if t in gk: continue
    kit = (r.get("kit") or "").lower()
    # номер только при большинстве голосов: 2 из 5 (7:1, 1:2, 10:1, 17:1) — не номер
    n, v = revote(r)
    entry = {"number": n, "confidence": r.get("confidence"), "kit": kit or (nums.get(t) or {}).get("kit"),
             "votes": v, "role": "игрок"}
    if entry["number"]: got += 1
    w = kit.split()[0] if kit else ""
    old = team.get(t, -1)
    if w.startswith(BLUE): team[t] = 0
    elif w.startswith(RED): team[t] = 1
    if team.get(t, -1) != old: changed += 1
    nums[t] = entry
for d in (pos, met):
    d["team_of"] = {str(k): v for k, v in team.items()}
    d["numbers"] = {str(k): v for k, v in nums.items()}
json.dump(pos, open(pos_path, "w"), ensure_ascii=False)
json.dump(met, open(met_path, "w"), ensure_ascii=False)
print("треков с новым номером %d из %d, команда изменилась у %d" % (got, len(new), changed))
