"""Подставляет мяч, найденный WASB, в файл позиций от analyze_match.

Зачем: наш детектор видит мяч в трети кадров, WASB — в четырёх пятых, и всё,
что считается по владению, упирается именно в это. Игроки и команды остаются
от analyze_match, меняется только мяч.

Съёмка идёт в 60 кадрах в секунду, analyze_match читает каждый второй кадр,
поэтому записи соотносятся как один к двум.

    python merge_ball.py pos.json wasb.json out.json [--min-score 0]
"""
import json, sys

pos_path, wasb_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
min_score = 0.0
if "--min-score" in sys.argv:
    min_score = float(sys.argv[sys.argv.index("--min-score") + 1])

pos = json.load(open(pos_path))
wasb = json.load(open(wasb_path))
step = round(wasb["fps"] / pos["fps"])
assert step >= 1, "частота кадров не сходится"

было = sum(1 for f in pos["frames"] if f["ball"] is not None)
стало = 0
for i, rec in enumerate(pos["frames"]):
    j = i * step
    p = wasb["frames"][j] if j < len(wasb["frames"]) else None
    if p and p[2] >= min_score:
        rec["ball"] = [round(p[0], 1), round(p[1], 1)]
        стало += 1
    else:
        rec["ball"] = None
pos["ball_source"] = "wasb"
json.dump(pos, open(out_path, "w"), ensure_ascii=False)
n = len(pos["frames"])
print("мяч: было %.1f %% кадров, стало %.1f %% (%d из %d)"
      % (100 * было / n, 100 * стало / n, стало, n))
