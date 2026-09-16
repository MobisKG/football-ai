"""Загружает статистику игры в бэкенд sportx (v1/match/analytics).

Переводит stats.json (match_stats.py) и scoreboard.json (scoreboard.py) в
формат бэкенда: стороны «home»/«away» вместо цветов формы.

Токен — пользователя лиги этого матча. Берётся из переменной окружения
SPORTX_TOKEN или строки SPORTX_TOKEN=... в football/.env либо в общем
~/Documents/sport/.env; в репозиторий не попадает.

    python upload_analytics.py stats.json --match-guid <guid> --home-colour красные \
        [--scoreboard scoreboard.json] [--home-is-top] [--video-url URL] \
        [--base https://cp.sportx.kg] [--dry-run]

--home-colour — цвет формы хозяев в этой игре (как в stats.json).
--home-is-top — хозяева в верхней строке табло (иначе в нижней).
"""
import json, os, sys, urllib.request, urllib.error

def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
stats_path = sys.argv[1]
guid = opt("--match-guid"); home_colour = opt("--home-colour")
sb_path = opt("--scoreboard"); base = opt("--base", "https://cp.sportx.kg").rstrip("/")
video_url = opt("--video-url", ""); dry = "--dry-run" in sys.argv
home_is_top = "--home-is-top" in sys.argv
if not guid or not home_colour:
    sys.exit("нужны --match-guid и --home-colour")

st = json.load(open(stats_path))
colours = list(st["possession_pct"])
if home_colour not in colours:
    sys.exit("цвета в stats.json: %s" % colours)
away_colour = [c for c in colours if c != home_colour][0]
side_of = {home_colour: "home", away_colour: "away"}

def players(colour):
    """ВРЕМЕННО (16.09.2026): №10 и игроки без прочитанного номера идут на сайт одной строкой №0
    («номер не прочитан или манишка»). Работает и для stats.json, посчитанных до этого правила."""
    t = st["teams"].get(colour) or {}
    out = []; zero = None
    for p in t.get("players", []):
        num = p["player"][1:] if p["player"].startswith("№") else None
        n = int(num) if num and num.isdigit() else None
        row = {"number": n, "time_on_pitch_s": p["time_on_pitch_s"], "touches": p.get("touches"),
               "possession_s": p.get("possession_s"), "passes": p.get("passes"),
               "distance_m": p.get("distance_m"), "top_speed_kmh": p.get("top_speed_kmh")}
        if n in (0, 10, None):
            if zero is None:
                zero = {"number": 0, "time_on_pitch_s": 0, "touches": 0, "possession_s": 0, "passes": 0, "distance_m": None, "top_speed_kmh": None}
            for k in ("time_on_pitch_s", "touches", "passes"): zero[k] += row[k] or 0
            zero["possession_s"] = round(zero["possession_s"] + (row["possession_s"] or 0), 1)
            continue
        out.append(row)
    if zero is None and t.get("unidentified_time_s"):
        zero = {"number": 0, "time_on_pitch_s": 0, "touches": 0, "possession_s": 0, "passes": 0, "distance_m": None, "top_speed_kmh": None}
    if zero is not None:
        # старые stats.json: время кусков без номера лежит отдельно — добавляем, если его нет в строке №0
        if not any(p["player"] == "№0" for p in t.get("players", [])): zero["time_on_pitch_s"] += t.get("unidentified_time_s", 0)
        out.append(zero)
    return {"colour": colour, "players": out, "unidentified_time_s": t.get("unidentified_time_s", 0)}

data = {
    "possession": {"home": st["possession_pct"][home_colour], "away": st["possession_pct"][away_colour]},
    "game_time_s": st.get("game_time_s"), "video_duration_s": st.get("duration_s"),
    "paused_s": st.get("paused_s"), "ball_visible_pct": st.get("ball_visible_pct"),
    "teams": {"home": players(home_colour), "away": players(away_colour)},
}
sb = json.load(open(sb_path)) if sb_path else st.get("scoreboard")
if sb:
    # на табло команда 1 — верхняя строка, 2 — нижняя
    top, bottom = ("home", "away") if home_is_top else ("away", "home")
    data["goals"] = [{"side": top if g["team"] == 1 else bottom, "video_t": g["video_t"], "half": g.get("half"),
                      "clock": g.get("clock"), "score_after": g.get("score_after")} for g in sb["goals"]]
    fs = sb["final_score"]
    data["scoreboard_final"] = {top: fs[0], bottom: fs[1]}

body = {"match_guid": guid, "source": "football-ai", "version": opt("--version", "2026-09-14"),
        "video_url": video_url, "data": data}
if dry:
    print(json.dumps(body, ensure_ascii=False, indent=1)); sys.exit(0)

token = os.environ.get("SPORTX_TOKEN")
if not token:
    here = os.path.dirname(os.path.abspath(__file__))
    # football/.env или общий ~/Documents/sport/.env (он вне репозиториев)
    for env in (os.path.join(here, "..", ".env"), os.path.join(here, "..", "..", ".env")):
        for line in open(env) if os.path.exists(env) else []:
            if line.startswith("SPORTX_TOKEN=") and not token: token = line.split("=", 1)[1].strip()
if not token:
    sys.exit("нет SPORTX_TOKEN (переменная окружения или строка в football/.env)")
req = urllib.request.Request(base + "/v1/match/analytics", data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json", "Authorization": "Token " + token})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(r.status, r.read().decode()[:300])
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode()[:500]); sys.exit(1)
