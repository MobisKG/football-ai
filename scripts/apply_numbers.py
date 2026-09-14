"""Переносит номера и цвета формы из read_numbers2 в файлы позиций.

Для каждого трека (кроме вратарей): номер — результат голосования по снимкам
(или пусто), форма — самый частый ответ. Команда пересчитывается по новой
форме: первое слово «синий/голубой» — синие, «красный/белый/бордовый» —
красные; если форму не разобрать, команда остаётся прежней.

    python apply_numbers.py pos.json pos_metres.json numbers2.json
"""
import json, sys

pos_path, met_path, num_path = sys.argv[1:4]
pos = json.load(open(pos_path)); met = json.load(open(met_path)); new = json.load(open(num_path))
team = {int(k): v for k, v in pos["team_of"].items()}
nums = {int(k): v for k, v in (pos.get("numbers") or {}).items()}
gk = set(int(k) for k in (pos.get("goalkeepers") or {}))
BLUE, RED = ("син", "голуб"), ("красн", "бел", "бордов")
changed = 0; got = 0
for k, r in new.items():
    t = int(k)
    if t in gk: continue
    kit = (r.get("kit") or "").lower()
    # номер только при большинстве голосов: 2 из 5 (7:1, 1:2, 10:1, 17:1) — не номер
    v = r.get("votes") or {}
    n = r.get("number")
    if n is not None and v.get(str(n), 0) <= sum(v.values()) / 2: n = None
    entry = {"number": n, "confidence": r.get("confidence"), "kit": kit or (nums.get(t) or {}).get("kit"),
             "votes": r.get("votes"), "role": "игрок"}
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
