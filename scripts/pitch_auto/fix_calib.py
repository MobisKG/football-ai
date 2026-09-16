"""Исправление разметки проблемных игр на мак мини, без участия человека.

    python fix_calib.py [--games a,b,c] [--every 5] [--upload]

По каждой игре по очереди (нагрузка умеренная): кадры каждые --every с из исходника 1080p →
автоопоры (сеть линий v2, галерея из опор всех игр той же площадки) → протягивание по опорам,
каждая 3-я отложена → ошибка на отложенных (validate_track) → метры, скорости, проверка →
лист проверки → загрузка на сайт, если проверка пройдена и указан --upload.
Состояние — results/pitch/fix_state.json, перезапуск продолжает. Журнал — fix_calib.log.
"""
import os, sys, json, time, subprocess, glob, shutil
HERE = os.path.dirname(os.path.abspath(__file__)); SCR = os.path.dirname(HERE); ROOT = os.path.dirname(SCR)
def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
GAMES = (opt("--games") or "untitled_3735,manastalas_ironmix,dolon,birimdik_abai,atscargo_ruor,elkat_ailin,talas91_361").split(",")
EVERY = opt("--every", "5"); UPLOAD = "--upload" in sys.argv; PY = sys.executable
STATE = os.path.join(ROOT, "results/pitch/fix_state.json")
st = json.load(open(STATE)) if os.path.exists(STATE) else {}
env = dict(os.environ, OMP_NUM_THREADS="4", OPENBLAS_NUM_THREADS="4", PYTORCH_ENABLE_MPS_FALLBACK="1")
def log(*a): print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)
def save(): json.dump(st, open(STATE, "w"), indent=1)
def run(slug, name, args, timeout=6 * 3600):
    t0 = time.time()
    r = subprocess.run(["nice", "-n", "15", PY] + args, capture_output=True, text=True, env=env, timeout=timeout)
    with open(os.path.join(ROOT, "results/pitch", slug, "fix.log"), "a") as f:
        f.write("== %s %s\n%s\n%s\n" % (time.strftime("%H:%M:%S"), name, r.stdout[-4000:], r.stderr[-3000:]))
    tail = (r.stdout.strip().splitlines() or [r.stderr.strip()[-160:]])[-1][:200]
    log(slug, name, "ok" if r.returncode == 0 else "ОШИБКА", "%.0f мин |" % ((time.time() - t0) / 60), tail)
    return r.returncode == 0
for slug in GAMES:
    D = os.path.join(ROOT, "results/pitch", slug); os.makedirs(D, exist_ok=True); g = st.setdefault(slug, {})
    if g.get("done"): log(slug, "уже сделано"); continue
    try:
        if not g.get("frames"):
            if run(slug, "кадры", [os.path.join(HERE, "extract_frames.py"), slug, "--every", EVERY]): g["frames"] = True; save()
            else: continue
        if not g.get("anchors"):
            if os.path.isdir(os.path.join(D, "auto")) and not os.path.isdir(os.path.join(D, "auto_15s")):
                shutil.move(os.path.join(D, "auto"), os.path.join(D, "auto_15s"))
            if run(slug, "опоры", [os.path.join(HERE, "run_games.py"), os.path.join(ROOT, "weights/pitch_lines_v2.pt"), slug]):
                s = json.load(open(os.path.join(D, "auto", "summary.json"))); g["anchors"] = [s["accepted"], s["frames"]]; save()
                log(slug, "принято опор %d из %d" % tuple(g["anchors"]))
            else: continue
        if not g.get("track"):
            if run(slug, "привязка", [os.path.join(HERE, "track_game.py"), slug, "--holdout", "3"]): g["track"] = True; save()
            else: g["done"] = "привязка не удалась"; save(); continue
        if not g.get("validation"):
            if run(slug, "проверка точности", [os.path.join(HERE, "validate_track.py"), slug]):
                v = json.load(open(os.path.join(D, "validation.json"))); g["validation"] = {k: v[k] for k in ("median_m", "p90_m", "share_under_1m", "measured")}; save()
        if not g.get("speeds"):
            # грузим только при точной привязке между опорами (критерий задачи: < 0,7 м в среднем,
            # < 1,5 м максимум); правдоподобные скорости при p90 ~10 м — случайность, не качество
            v = g.get("validation") or {}
            accurate = v.get("median_m") is not None and v["median_m"] <= 0.7 and v.get("p90_m", 99) <= 1.5
            g["accurate"] = bool(accurate); save()
            if not accurate: log(slug, "точность недостаточна (медиана %s м, 90%% %s м) — на сайт не грузим" % (v.get("median_m"), v.get("p90_m")))
            if run(slug, "скорости", [os.path.join(HERE, "finish_game.py"), slug] + (["--upload"] if (UPLOAD and accurate) else [])):
                p = json.load(open(os.path.join(D, "speeds_auto", "plausibility.json")))
                g["speeds"] = {k: p.get(k) for k in ("ok", "reason", "players", "dist_per_min_median", "top_speed_median", "standing_speed_median_mps")}
                up = os.path.join(D, "speeds_auto", "upload.log"); g["uploaded"] = os.path.exists(up) and "SUCCESS" in open(up).read(); save()
        g["done"] = True; save()
    except Exception as e:
        log(slug, "СБОЙ", str(e)[:200]); g["error"] = str(e)[:200]; save()
log("ВСЁ ГОТОВО:", json.dumps({k: {"опоры": v.get("anchors"), "точность": v.get("validation"), "скорости": v.get("speeds"), "загружено": v.get("uploaded")} for k, v in st.items()}, ensure_ascii=False))
