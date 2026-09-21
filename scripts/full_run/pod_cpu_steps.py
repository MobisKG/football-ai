"""Процессорные шаги одной игры — на самой арендованной машине.

    python pod_cpu_steps.py <имя_игры> [--matches matches.json] [--title "..."]

Раньше это считал мак (batch_run.mac_steps), из-за чего прогон зависел от
включённого ноутбука. Здесь то же самое делает машина сразу после разбора на
видеокарте: 5–7 минут её времени (~0,05 $) вместо привязки к ноутбуку.
Результат: stats.json, home.json — их забирает сторож и грузит на сайт.
Токен сайта на машину не попадает: грузит тот, у кого он есть.
"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
FB = os.environ.get("FB_DIR", "/root/fb")
opt = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
slug = sys.argv[1]
MATCHES = os.path.abspath(opt("--matches", os.path.join(FB, "matches.json")))   # home_side запускается из другой папки
TITLE = opt("--title", slug)
D = os.path.join(FB, "out", slug)
PY = sys.executable

def sh(cmd, timeout=3600):
    p = subprocess.run(cmd, shell=True, cwd=D, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr

def step(name, cmd, check_file, always=False):
    if os.path.exists(os.path.join(D, check_file)) and not always: return
    rc, out = sh(cmd)
    open(os.path.join(D, "cpu_%s.log" % name), "w").write(out)
    if rc != 0 or not os.path.exists(os.path.join(D, check_file)):
        raise RuntimeError("%s: %s" % (name, out[-300:]))

def scoreboard_ok():
    """Табло годится, если прочитаны оба названия и часы видны хотя бы в трети чтений."""
    p = os.path.join(D, "scoreboard.json")
    if not os.path.exists(p): return False
    try: b = json.load(open(p))
    except Exception: return False
    reads = b.get("reads") or []
    vis = sum(1 for r in reads if r.get("visible") and r.get("clock"))
    ok = bool(all(b.get("teams") or [None, None]) and reads and vis >= 0.3 * len(reads))
    if not ok and not os.path.exists(os.path.join(D, "scoreboard_bad.json")):
        os.rename(p, os.path.join(D, "scoreboard_bad.json"))
    return ok

S = lambda f: os.path.join(FB, f)
step("ball", "%s %s ball_cands.json ball.json" % (PY, S("ball_track.py")), "ball.json")
if not os.path.exists(os.path.join(D, "pos_final.json")):
    step("teams", "%s %s pos.json numbers.json pos_final.json" % (PY, S("team_from_kit.py")), "pos_final.json")
    # met_dummy — позиции «в формате метров» (те же пиксели, «на поле» всегда истина)
    pos = json.load(open(os.path.join(D, "pos.json")))
    json.dump({"fps": pos["fps"], "team_of": pos["team_of"], "numbers": {}, "player_of": {},
               "frames": [{"players": [[int(t), x, y, True] for t, x, y in f["players"]]} for f in pos["frames"]]},
              open(os.path.join(D, "met_dummy.json"), "w"))
    step("numbers", "%s %s pos_final.json met_dummy.json numbers.json --keep-teams" % (PY, S("apply_numbers.py")), "pos_final.json", always=True)
    step("cluster", "%s %s pos_final.json met_dummy.json osnet.json" % (PY, S("reid_cluster.py")), "pos_final.json", always=True)

sb = "--scoreboard scoreboard.json" if scoreboard_ok() else ""
rc, out = sh('%s %s pos_final.json ball.json stats.json %s --name "%s"' % (PY, S("match_stats.py"), sb, TITLE.replace('"', "")))
if rc != 0: raise SystemExit("stats: " + out[-300:])
open(os.path.join(D, "stats.txt"), "w").write(out)

home = {"confident": False, "reason": "matches.json на машине нет"}
if os.path.exists(MATCHES):
    rc, out = sh("%s %s %s %s" % (PY, S("home_side.py"), D, MATCHES))
    open(os.path.join(D, "cpu_home.log"), "w").write(out)
    p = os.path.join(D, "home.json")
    home = json.load(open(p)) if os.path.exists(p) else {"confident": False, "reason": out[-200:]}
json.dump(home, open(os.path.join(D, "home.json"), "w"), ensure_ascii=False)
print("CPU_STEPS_OK", slug, json.dumps(home, ensure_ascii=False)[:200])
