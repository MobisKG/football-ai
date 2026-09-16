"""Скорости и пробег одной игры по автоматическим опорам, на ноутбуке.

    python speed_game.py <slug> [--anchors auto|manual] [--upload] [--step N]

Нужны: видео results/pitch/videos/<slug>.mp4 (download_videos.py), опоры
results/pitch/<slug>/auto/frame_t*.json с ok=true (calibrate_game.py) или manual/.
Делает: pitch_track.py по опорам (сверка только с соседними опорами, смешение прямого и
обратного прохода), контрольные кадры → results/pitch/<slug>/check_track.jpg,
метры и статистика (окно 1,5 с) → results/pitch/<slug>/speeds_auto/, сводка правдоподобия
→ speeds_auto/plausibility.json. Загрузка на сайт — только с --upload и только если
правдоподобие в норме (см. plausible()).
"""
import os, sys, json, glob, subprocess, time, statistics, random
import numpy as np, cv2
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE)); SCR = os.path.dirname(HERE)
PY = sys.executable
def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def plausible(st):
    """проверка 3 из задачи: пробег 40–70 м/мин у игроков с ≥5 мин, макс. скорость медиана 16–24, единичные до 30"""
    rows = [p for t in st["teams"].values() for p in t["players"] if p.get("distance_m") is not None and p["time_on_pitch_s"] >= 300]
    # игроки собираются из треков по номерам; в части игр номеров читается мало.
    # трёх игроков хватает, чтобы увидеть неверную привязку (пробег и скорость уезжают у всех сразу)
    if len(rows) < 3: return {"ok": False, "reason": "мало игроков с ≥5 мин: %d (номера прочитаны плохо)" % len(rows), "players": len(rows)}
    per_min = [p["distance_m"] / (p["time_on_pitch_s"] / 60) for p in rows]; tops = [p["top_speed_kmh"] for p in rows if p.get("top_speed_kmh")]
    r = {"players": len(rows), "dist_per_min_median": round(statistics.median(per_min), 1), "dist_per_min_range": [round(min(per_min)), round(max(per_min))],
         "top_speed_median": round(statistics.median(tops), 1), "top_speed_max": round(max(tops), 1), "top_over_30": sum(1 for t in tops if t > 30)}
    reasons = []
    if not 40 <= r["dist_per_min_median"] <= 70: reasons.append("пробег медиана %.0f м/мин вне 40–70" % r["dist_per_min_median"])
    if not 16 <= r["top_speed_median"] <= 24: reasons.append("макс. скорость медиана %.1f вне 16–24" % r["top_speed_median"])
    if r["top_over_30"] > max(2, len(tops) // 5): reasons.append("свыше 30 км/ч у %d игроков" % r["top_over_30"])
    r["ok"] = not reasons; r["reason"] = "; ".join(reasons); return r

def jitter(met_path, pos_path):
    """дрожание у стоящих: медиана скорости точек (окно 1 с) у треков, чья 10-с траектория в метрах почти не двигается"""
    M = json.load(open(met_path)); fps = M["fps"]; fr = M["frames"]; W = int(round(fps))
    tr = {}
    for k, f in enumerate(fr):
        for tid, x, y, ins in f["players"]:
            if ins: tr.setdefault(tid, []).append((k, x, y))
    sp = []
    for tid, pts in tr.items():
        if len(pts) < 10 * W: continue
        a = np.array(pts); 
        for s in range(0, len(a) - 10 * W, 10 * W):
            seg = a[s:s + 10 * W]
            if np.ptp(seg[:, 1]) < 1.5 and np.ptp(seg[:, 2]) < 1.5:      # стоит: разброс < 1,5 м за 10 с
                d = np.hypot(np.diff(seg[::W, 1]), np.diff(seg[::W, 2])); sp.extend(d.tolist())
    return {"standing_speed_median_mps": round(float(np.median(sp)), 2) if sp else None, "standing_speed_p90_mps": round(float(np.percentile(sp, 90)), 2) if sp else None, "samples": len(sp)}

if __name__ == "__main__":
    slug = sys.argv[1]; kind = opt("--anchors", "auto"); D = os.path.join(ROOT, "results/pitch", slug)
    job = next(j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json"))) if j["slug"] == slug)
    video = os.path.join(ROOT, "results/pitch/videos", slug + ".mp4")
    if not os.path.exists(video): sys.exit("нет видео " + video)
    anc = sorted(glob.glob(os.path.join(D, kind, "frame_t*.json")))
    good = [f for f in anc if json.load(open(f)).get("H_img2plan") and not json.load(open(f)).get("skip") and json.load(open(f)).get("ok", True)]
    if len(good) < 6: sys.exit("мало опор: %d" % len(good))
    cap = cv2.VideoCapture(video); src_fps = cap.get(5); n = cap.get(7); w = cap.get(3); dur = n / src_fps; cap.release()
    step = int(opt("--step", max(1, round(src_fps / job["pos_fps"])))); ascale = w / 1920.0
    secs = sorted(json.load(open(f))["frame_sec"] for f in good); ref = good[len(good) // 2]; ref_sec = json.load(open(ref))["frame_sec"]
    out_track = os.path.join(D, "track_%s.json" % kind); logf = os.path.join(D, "track_%s.log" % kind)
    log(slug, "опор %d (каждые ~%.0f с), видео %.0f×, %.2f к/с, шаг %d, %.0f мин" % (len(good), np.median(np.diff(secs)) if len(secs) > 1 else 0, w, src_fps, step, dur / 60))
    if not os.path.exists(out_track) or "--redo" in sys.argv:
        cmd = [PY, os.path.join(SCR, "pitch_track.py"), video, ref, out_track, "--step", str(step), "--ref-sec", str(ref_sec), "--no-ground-mask", "--anchor-scale", str(ascale), "--near-anchors", "40", "--blend"]
        for f in good: cmd += ["--anchor", f]
        t0 = time.time(); r = subprocess.run(["nice", "-n", "15"] + cmd, capture_output=True, text=True); open(logf, "w").write(r.stdout + r.stderr)
        if "готово" not in r.stdout: sys.exit("протягивание не удалось: " + (r.stdout + r.stderr)[-400:])
        log("протягивание: %s (%.0f мин)" % (r.stdout.strip().splitlines()[-1][:120], (time.time() - t0) / 60))
    # контрольные кадры между опорами: 8 случайных моментов
    rnd = random.Random(1); mids = sorted(rnd.sample([(a + b) / 2 for a, b in zip(secs, secs[1:])], min(8, len(secs) - 1)))
    chk = os.path.join(D, "check_track_%s" % kind); subprocess.run(["rm", "-rf", chk]); 
    subprocess.run([PY, os.path.join(SCR, "pitch_check.py"), video, out_track, chk] + [str(m) for m in mids], capture_output=True, text=True)
    ims = [cv2.imread(f) for f in sorted(glob.glob(os.path.join(chk, "*.jpg")))]
    if ims:
        ims = [cv2.resize(i, (960, 540)) for i in ims]
        while len(ims) % 2: ims.append(np.zeros_like(ims[0]))
        cv2.imwrite(os.path.join(D, "check_track_%s.jpg" % kind), np.vstack([np.hstack(ims[i:i + 2]) for i in range(0, len(ims), 2)]), [cv2.IMWRITE_JPEG_QUALITY, 80])
    r = subprocess.run([PY, os.path.join(SCR, "pitch_metres.py"), slug, out_track, "--out-sub", "speeds_" + kind, "--win", "1.5"], capture_output=True, text=True)
    print(r.stdout[-1200:])
    if r.returncode != 0: sys.exit("метры/статистика: " + r.stderr[-400:])
    sp = os.path.join(D, "speeds_" + kind); st = json.load(open(os.path.join(sp, "stats.json")))
    pl = plausible(st); pl.update(jitter(os.path.join(sp, "pos_metres.json"), None))
    if pl.get("standing_speed_median_mps") is not None and pl["standing_speed_median_mps"] > 1.0: pl["ok"] = False; pl["reason"] = (pl["reason"] + "; " if pl["reason"] else "") + "дрожание у стоящих %.2f м/с > 1" % pl["standing_speed_median_mps"]
    json.dump(pl, open(os.path.join(sp, "plausibility.json"), "w"), ensure_ascii=False, indent=1); log("правдоподобие:", json.dumps(pl, ensure_ascii=False))
    if "--upload" not in sys.argv: sys.exit(0)
    if not pl["ok"]: log("НЕ ГРУЖУ: " + pl["reason"]); sys.exit(0)
    if not job.get("home_colour") or not job.get("match_guid") or job.get("merged_into"): log("без загрузки (нет цвета хозяев/match_guid или сдвоенная игра)"); sys.exit(0)
    args = [PY, os.path.join(SCR, "upload_analytics.py"), "stats.json", "--match-guid", job["match_guid"], "--home-colour", job["home_colour"],
            "--video-url", "https://www.youtube.com/watch?v=" + job["yt"], "--version", "2026-09-16-speeds"]
    if job.get("scoreboard"): args += ["--scoreboard", os.path.join(ROOT, "results/batch_2026-09-14", slug, "scoreboard.json")] + (["--home-is-top"] if job.get("home_is_top") else [])
    r = subprocess.run(args, cwd=sp, capture_output=True, text=True); open(os.path.join(sp, "upload.log"), "w").write(r.stdout + r.stderr); log("загрузка:", (r.stdout or r.stderr)[:160].strip())
