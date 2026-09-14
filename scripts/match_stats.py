"""Текстовая статистика одной игры: владение и вклад каждого игрока.

Собирает то, что уже посчитано по видео, в одну сводку для сайта:
  - владение мячом по командам (та же логика, что в render_overlay.py:
    команда ближайшего к мячу игрока, смена после 0,3 с подряд, пока мяча
    не видно — владение остаётся за прежней командой);
  - по каждому игроку: номер, время на поле, а если есть позиции в метрах —
    пробег, средняя и максимальная скорость (95-й процентиль, см. speeds.py).

Игрок — это склеенная группа треков (player_of из reid_cluster.py), а если
склейки нет — команда + номер. Игроки без номера сводятся в «без номера».
Передачи, голы и удары здесь не считаются: на видео они пока ненадёжны.

    python match_stats.py pos.json ball.json out.json [--metres pos_metres.json] [--name "A vs B"]
        [--scoreboard scoreboard.json]

Игровое время. С табло трансляции (scoreboard.py) берём часы тайма: если между
двумя чтениями (раз в 5 с) часы не сдвинулись или табло пропало, эти секунды
не игровые — перерыв, таймаут, приветствие. Плюс страховка: мяча нет в кадре
дольше 10 с — счёт владения стоит, пока мяч не появится. Касания: смена
ближайшего к мячу игрока, удержавшаяся 0,3 с, — одно касание.
"""
import json, math, sys
from collections import defaultdict
import numpy as np

pos_path, ball_path, out_path = sys.argv[1:4]
opt = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
met_path, name, sb_path = opt("--metres"), opt("--name", ""), opt("--scoreboard")

pos = json.load(open(pos_path)); ball = json.load(open(ball_path))
fps = pos["fps"]; H = float(ball.get("height") or 1080)
team = {int(k): v for k, v in pos["team_of"].items()}
names = {int(k): v for k, v in (pos.get("team_names") or {}).items()}
nums = {int(k): (v or {}).get("number") for k, v in (pos.get("numbers") or {}).items()}
gk = {int(k): v for k, v in (pos.get("goalkeepers") or {}).items()}
player_of = {int(k): v for k, v in (pos.get("player_of") or {}).items()}
side = set(pos.get("sideline") or [])

# --- владение ---------------------------------------------------------------
def nearest_team(players, b):
    best = None
    for tid, x, y in players:
        tm = team.get(int(tid), -1)
        if tm not in (0, 1) or int(tid) in side:
            continue
        d = math.hypot(x - b[0], y - b[1])
        if d < 2.2 * (26 + 34 * (y / H) ** 2) and (best is None or d < best[0]):
            best = (d, tm)
    return best[1] if best else None

def key_of(t):
    if t in gk: return ("gk", gk[t])
    if t in player_of: return ("p", player_of[t])
    return ("n", team.get(t), nums.get(t)) if nums.get(t) else ("t", t)

# --- игровое время по табло ------------------------------------------------
def clock_s(c):
    try:
        m, sec = str(c).split(":"); return int(m) * 60 + int(sec)
    except Exception: return None
paused = []                     # отрезки видео (с), когда часы стояли или табло не было
if sb_path:
    reads = sorted(json.load(open(sb_path))["reads"], key=lambda r: r["t"])
    for a, b in zip(reads, reads[1:]):
        ca, cb = clock_s(a.get("clock")) if a.get("visible") else None, clock_s(b.get("clock")) if b.get("visible") else None
        if ca is None or cb is None or cb <= ca:
            paused.append((a["t"], b["t"]))
def is_paused(t):
    return any(x <= t < y for x, y in paused)

def nearest_player(players, b):
    best = None
    for tid, x, y in players:
        tm = team.get(int(tid), -1)
        if tm not in (0, 1) or int(tid) in side: continue
        d = math.hypot(x - b[0], y - b[1])
        if d < 2.2 * (26 + 34 * (y / H) ** 2) and (best is None or d < best[0]):
            best = (d, tm, int(tid))
    return best

owner, cand, cand_n = None, None, 0
holder, hcand, hcand_n = None, None, 0
poss = [0.0, 0.0]; changes = 0; seen_ball = 0; game_s = 0.0; no_ball = 0
touches = defaultdict(int); passes = defaultdict(int); received = defaultdict(int)
hold_s = defaultdict(float); last_hold_t = -1e9
for k, f in enumerate(pos["frames"]):
    t = k / fps
    kb = min(int(k * ball["fps"] / fps), len(ball["frames"]) - 1)
    b = ball["frames"][kb]
    no_ball = 0 if b else no_ball + 1
    if b: seen_ball += 1
    if is_paused(t) or no_ball > 10 * fps:
        continue
    game_s += 1 / fps
    near = nearest_player(f["players"], b) if b else None
    tm = near[1] if near else None
    if tm is not None and tm != owner:
        cand_n = cand_n + 1 if tm == cand else 1; cand = tm
        if cand_n >= int(0.3 * fps):
            owner = tm; cand_n = 0; changes += 1
    else:
        cand_n = 0
    if owner in (0, 1):
        poss[owner] += 1 / fps
    # касания: новый ближайший игрок, удержавшийся 0,3 с
    tid = near[2] if near else None
    if tid is not None and tid != holder:
        hcand_n = hcand_n + 1 if tid == hcand else 1; hcand = tid
        if hcand_n >= int(0.3 * fps):
            # Передача: мяч перешёл к партнёру по команде не дольше чем за 3 с.
            # Приблизительно: в толкучке ближайший к мячу меняется и без паса,
            # на эталоне клипа 5 так находилась примерно половина передач.
            if holder is not None and team.get(holder) == team.get(tid) and key_of(holder) != key_of(tid) \
                    and t - last_hold_t <= 3.0:
                passes[holder] += 1; received[tid] += 1
            holder = tid; hcand_n = 0; touches[tid] += 1
    elif tid is None:
        hcand_n = 0
    if holder is not None and tid == holder:
        hold_s[holder] += 1 / fps; last_hold_t = t
tot = sum(poss) or 1

# --- игроки -----------------------------------------------------------------

on_time = defaultdict(float)
for f in pos["frames"]:
    for tid, x, y in f["players"]:
        t = int(tid)
        if team.get(t, -1) in (0, 1) and t not in side:
            on_time[key_of(t)] += 1 / fps

dist = defaultdict(float); vmax = defaultdict(float)
if met_path:
    met = json.load(open(met_path)); W = int(0.5 * fps); ker = np.ones(W) / W
    tracks = defaultdict(list)
    for k, f in enumerate(met["frames"]):
        for tid, X, Y, ins in f["players"]:
            if ins and team.get(int(tid), -1) in (0, 1) and int(tid) not in side:
                tracks[int(tid)].append((k, X, Y))
    for t, pts in tracks.items():
        a = np.array(pts, float)
        for seg in np.split(a, np.where(np.diff(a[:, 0]) > 0.2 * fps)[0] + 1):
            if len(seg) < 2 * W: continue
            sx = np.convolve(seg[:, 1], ker, "valid"); sy = np.convolve(seg[:, 2], ker, "valid")
            v = np.hypot(sx[W:] - sx[:-W], sy[W:] - sy[:-W]) / 0.5
            ok = v < 10.0
            if not ok.any(): continue
            dist[key_of(t)] += float(np.hypot(np.diff(sx), np.diff(sy))[:len(ok)][ok[:len(sx) - 1]].sum())
            vmax[key_of(t)] = max(vmax[key_of(t)], float(np.percentile(v[ok], 95)))

def label(key):
    if key[0] == "gk": return "вратарь"
    ts = [t for t in team if key_of(t) == key]
    ns = [nums.get(t) for t in ts if nums.get(t)]
    return "№%s" % max(set(ns), key=ns.count) if ns else "без номера"
def team_of_key(key):
    if key[0] == "gk": return key[1]
    for t in team:
        if key_of(t) == key: return team[t]
    return -1

rows = defaultdict(list)
for key, sec in on_time.items():
    tm = team_of_key(key)
    if tm not in (0, 1) or sec < 5: continue
    rows[tm].append({"player": label(key), "time_on_pitch_s": round(sec),
                     "touches": sum(n for t_, n in touches.items() if key_of(t_) == key),
                     "possession_s": round(sum(v for t_, v in hold_s.items() if key_of(t_) == key), 1),
                     "passes": sum(n for t_, n in passes.items() if key_of(t_) == key),
                     "distance_m": round(dist[key]) if met_path else None,
                     "top_speed_kmh": round(vmax[key] * 3.6, 1) if met_path and vmax[key] else None})

out = {"match": name, "duration_s": round(len(pos["frames"]) / fps), "game_time_s": round(game_s),
       "paused_s": round(sum(y - x for x, y in paused)),
       "ball_visible_pct": round(100 * seen_ball / max(1, len(pos["frames"]))),
       "possession_pct": {names.get(0, "команда 0"): round(100 * poss[0] / tot), names.get(1, "команда 1"): round(100 * poss[1] / tot)},
       "possession_changes": changes, "teams": {}}
lines = ["Игра: %s" % name, "Длительность видео %d:%02d, игровое время %d:%02d (пауз по табло %d:%02d), мяч виден в %d%% кадров" % (
             out["duration_s"] // 60, out["duration_s"] % 60, out["game_time_s"] // 60, out["game_time_s"] % 60,
             out["paused_s"] // 60, out["paused_s"] % 60, out["ball_visible_pct"]),
         "Владение: " + ", ".join("%s %d%%" % kv for kv in out["possession_pct"].items()), ""]
for tm in (0, 1):
    # безномерные куски одного игрока не суммируются: их нельзя приписать человеку
    # Один номер в команде — один человек: куски, которые склейка не соединила
    # (пересеклись на пару кадров), складываем. Пробег суммируется, скорость — максимум.
    merged = {}
    for r in rows[tm]:
        if r["player"] == "без номера": continue
        m = merged.setdefault(r["player"], {"player": r["player"], "time_on_pitch_s": 0, "touches": 0, "possession_s": 0, "passes": 0,
                                            "distance_m": 0 if met_path else None, "top_speed_kmh": None})
        m["time_on_pitch_s"] += r["time_on_pitch_s"]; m["touches"] += r["touches"]
        m["possession_s"] = round(m["possession_s"] + r["possession_s"], 1); m["passes"] += r["passes"]
        if met_path:
            m["distance_m"] += r["distance_m"] or 0
            if r["top_speed_kmh"]: m["top_speed_kmh"] = max(m["top_speed_kmh"] or 0, r["top_speed_kmh"])
    numbered = sorted(merged.values(), key=lambda r: -r["time_on_pitch_s"])
    anon = [r for r in rows[tm] if r["player"] == "без номера"]
    out["teams"][names.get(tm, str(tm))] = {"players": numbered,
                                             "unidentified_time_s": sum(r["time_on_pitch_s"] for r in anon)}
    lines.append("%s:" % names.get(tm, tm))
    for r in numbered:
        extra = ("  пробег %4d м  макс. %4.1f км/ч" % (r["distance_m"], r["top_speed_kmh"] or 0)) if met_path else ""
        lines.append("  %-10s на поле %4d с  касаний %3d  у мяча %5.1f с  передач %2d%s" % (
            r["player"], r["time_on_pitch_s"], r["touches"], r["possession_s"], r["passes"], extra))
    lines.append("  без номера: %d с суммарно" % out["teams"][names.get(tm, str(tm))]["unidentified_time_s"])
    lines.append("")
json.dump(out, open(out_path, "w"), ensure_ascii=False, indent=1)
print("\n".join(lines))
