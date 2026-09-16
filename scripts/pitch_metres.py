"""Пробег и скорость по готовой привязке: позиции -> метры -> статистика.

    python pitch_metres.py <slug> <track.json> [--out-sub имя] [--win 1.5]

Параметры игры — из results/pitch/jobs.json: откуда брать позиции (ночная папка или
исправленная), нужно ли табло. Пишет results/pitch/<slug>/: pos_metres.json,
stats.json и stats.txt с пробегом и максимальной скоростью, плюс сводку правдоподобия.
"""
import json, os, sys, subprocess, statistics
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
PY = sys.executable
slug, track = sys.argv[1], sys.argv[2]
opt = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
OUT_SUB, WIN = opt("--out-sub", ""), opt("--win")
job = next(j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json"))) if j["slug"] == slug)
B = os.path.join(ROOT, "results/batch_2026-09-14")
src = os.path.join(B, job["posdir"]); night = os.path.join(B, slug)
out = os.path.join(ROOT, "results/pitch", slug, OUT_SUB); os.makedirs(out, exist_ok=True)
pos_final = os.path.join(src, "pos_final.json")
ball = os.path.join(night, "ball.json")
met = os.path.join(out, "pos_metres.json")
def run(args):
    r = subprocess.run([PY] + args, capture_output=True, text=True)
    if r.returncode != 0: sys.exit("ошибка %s:\n%s" % (args[0], r.stderr[-800:]))
    return r.stdout
print(run([os.path.join(HERE, "to_metres.py"), pos_final, track, met]).strip())
args = [os.path.join(HERE, "match_stats.py"), pos_final, ball, os.path.join(out, "stats.json"), "--metres", met, "--name", slug]
if WIN: args += ["--win", WIN]
if job["scoreboard"]: args += ["--scoreboard", os.path.join(night, "scoreboard.json")]
txt = run(args); open(os.path.join(out, "stats.txt"), "w").write(txt)
st = json.load(open(os.path.join(out, "stats.json")))
game_min = (st.get("game_time_s") or st["duration_s"]) / 60
rows = [(c, p) for c, t in st["teams"].items() for p in t["players"] if p.get("distance_m") is not None]
print("игровое время %.1f мин, игроков с номером %d" % (game_min, len(rows)))
long_ = [(c, p) for c, p in rows if p["time_on_pitch_s"] >= 300]
if long_:
    per_min = [p["distance_m"] / (p["time_on_pitch_s"] / 60) for _, p in long_]
    tops = [p["top_speed_kmh"] for _, p in long_ if p.get("top_speed_kmh")]
    print("игроки с ≥5 мин на поле: %d; пробег в минуту: медиана %.0f м/мин (от %.0f до %.0f); макс. скорость: медиана %.1f км/ч, максимум %.1f" % (
        len(long_), statistics.median(per_min), min(per_min), max(per_min), statistics.median(tops) if tops else 0, max(tops) if tops else 0))
for c, t in st["teams"].items():
    top = sorted(t["players"], key=lambda p: -p["time_on_pitch_s"])[:5]
    print("  %s: %s" % (c, "; ".join("%s %d с, %s м, %s км/ч" % (p["player"], p["time_on_pitch_s"], p.get("distance_m"), p.get("top_speed_kmh")) for p in top)))
