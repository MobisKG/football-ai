"""Местный шаг после машины: метры, пробег, скорость, проверка правдоподобия, загрузка на сайт.

    python finish_game.py <slug> [--upload] [--track путь]

Нужен готовый results/pitch/<slug>/track_auto.json (с машины). Пишет speeds_auto/ со
stats.json, stats.txt и plausibility.json. Грузит на сайт только при --upload и только если
проверка пройдена (пробег 40–70 м/мин, макс. скорость медиана 16–24 км/ч, дрожание ≤ 1 м/с).
"""
import os, sys, json, subprocess
HERE = os.path.dirname(os.path.abspath(__file__)); SCR = os.path.dirname(HERE); ROOT = os.path.dirname(SCR)
sys.path.insert(0, HERE)
from speed_game import plausible, jitter, log
def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
slug = sys.argv[1]; D = os.path.join(ROOT, "results/pitch", slug)
track = opt("--track") or os.path.join(D, "track_auto.json")
if not os.path.exists(track): sys.exit("нет привязки " + track)
job = next(j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json"))) if j["slug"] == slug)
sp = os.path.join(D, "speeds_auto")
r = subprocess.run([sys.executable, os.path.join(SCR, "pitch_metres.py"), slug, track, "--out-sub", "speeds_auto", "--win", "1.5"], capture_output=True, text=True)
print(r.stdout[-900:])
if r.returncode != 0: sys.exit("метры/статистика: " + r.stderr[-400:])
st = json.load(open(os.path.join(sp, "stats.json")))
pl = plausible(st); pl.update(jitter(os.path.join(sp, "pos_metres.json"), None))
if pl.get("standing_speed_median_mps") is not None and pl["standing_speed_median_mps"] > 1.0:
    pl["ok"] = False; pl["reason"] = (pl["reason"] + "; " if pl["reason"] else "") + "дрожание у стоящих %.2f м/с > 1" % pl["standing_speed_median_mps"]
try:
    from check_tracks import summary as track_summary
    pl["tracks"] = track_summary(os.path.join(sp, "pos_metres.json"))
except Exception as e: pl["tracks"] = {"error": str(e)[:80]}
anch = json.load(open(os.path.join(D, "auto", "summary.json"))) if os.path.exists(os.path.join(D, "auto", "summary.json")) else {}
pl["anchors"] = anch.get("accepted"); pl["frames"] = anch.get("frames")
json.dump(pl, open(os.path.join(sp, "plausibility.json"), "w"), ensure_ascii=False, indent=1)
log(slug, "правдоподобие:", json.dumps(pl, ensure_ascii=False))
# точность разметки поля (отложенные кадры разметки каждого кадра): медиана ≤ 0,7 м, p90 ≤ 1,5 м
res_p = os.path.join(os.path.dirname(os.path.abspath(track)), "perframe_full", "result.json")
if os.path.exists(res_p):
    ho = json.load(open(res_p)).get("holdout") or {}
    if ho.get("median_m") is None or ho["median_m"] > 0.7 or (ho.get("p90_m") or 99) > 1.5:
        pl["ok"] = False; pl["reason"] = (pl["reason"] + "; " if pl["reason"] else "") + "точность разметки: медиана %s м, p90 %s м" % (ho.get("median_m"), ho.get("p90_m"))
        log(slug, "не прошла точность разметки:", ho)
if "--upload" not in sys.argv or not pl["ok"]: sys.exit(0)
if not job.get("home_colour") or not job.get("match_guid") or job.get("merged_into"):
    log(slug, "без загрузки: нет цвета хозяев, match_guid или сдвоенная игра"); sys.exit(0)
args = [sys.executable, os.path.join(SCR, "upload_analytics.py"), "stats.json", "--match-guid", job["match_guid"], "--home-colour", job["home_colour"],
        "--video-url", "https://www.youtube.com/watch?v=" + job["yt"], "--version", "2026-09-16-speeds"]
if job.get("scoreboard"): args += ["--scoreboard", os.path.join(ROOT, "results/batch_2026-09-14", slug, "scoreboard.json")] + (["--home-is-top"] if job.get("home_is_top") else [])
r = subprocess.run(args, cwd=sp, capture_output=True, text=True)
open(os.path.join(sp, "upload.log"), "w").write(r.stdout + r.stderr); log(slug, "загрузка:", (r.stdout or r.stderr)[:200].strip())
