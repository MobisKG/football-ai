"""Проверка привязки по всем трекам, а не только по игрокам с прочитанным номером.

Номера читаются не во всех играх, и проверка по ним отбраковывала хорошие привязки
(«мало игроков с ≥5 мин»). Здесь берутся все треки на поле: положение сглаживается
окном 1,5 с, пробег считается по сглаженной траектории, скорость — по тому же окну.

    python check_tracks.py <pos_metres.json>
"""
import json, sys
import numpy as np

def track_stats(met_path, win_s=1.5, min_s=30, step_s=0.5):
    M = json.load(open(met_path)); fps = M["fps"]; fr = M["frames"]
    W = max(2, int(round(win_s * fps))); S = max(1, int(round(step_s * fps)))
    seen = {}
    for k, f in enumerate(fr):
        for tid, x, y, inside in f["players"]:
            if inside: seen.setdefault(tid, []).append((k, x, y))
    rows = []
    for tid, pts in seen.items():
        if len(pts) < min_s * fps * 0.8: continue          # трек живёт на поле хотя бы min_s секунд
        a = np.array(pts, float); idx = a[:, 0]
        # разрывы больше 2 с рвут трек на куски
        cuts = np.flatnonzero(np.diff(idx) > 2 * fps) + 1
        segs = np.split(a, cuts); dist = 0.0; speeds = []; dur = 0.0
        for seg in segs:
            if len(seg) < W + S: continue
            ker = np.ones(W) / W
            xs = np.convolve(seg[:, 1], ker, "valid"); ys = np.convolve(seg[:, 2], ker, "valid")
            if len(xs) < S + 1: continue
            dx = xs[S:] - xs[:-S]; dy = ys[S:] - ys[:-S]
            d = np.hypot(dx, dy); dist += float(d.sum()); dur += len(seg) / fps
            speeds.append(d / (S / fps) * 3.6)
        if dur < min_s or not speeds: continue
        v = np.concatenate(speeds)
        rows.append({"track": int(tid), "minutes": dur / 60, "dist_m": dist, "per_min": dist / (dur / 60),
                     "top_kmh": float(np.percentile(v, 99)), "median_kmh": float(np.median(v))})
    return rows

def summary(met_path, **kw):
    rows = track_stats(met_path, **kw)
    if len(rows) < 15: return {"tracks": len(rows), "ok": False, "reason": "мало треков: %d" % len(rows)}
    pm = np.array([r["per_min"] for r in rows]); tp = np.array([r["top_kmh"] for r in rows])
    r = {"tracks": len(rows), "dist_per_min_median": round(float(np.median(pm)), 1),
         "dist_per_min_range": [round(float(pm.min())), round(float(pm.max()))],
         "top_speed_median": round(float(np.median(tp)), 1), "top_speed_max": round(float(tp.max()), 1),
         "top_over_30": int((tp > 30).sum()), "over_32_share": round(float((tp > 32).mean()), 3),
         "median_minutes": round(float(np.median([x["minutes"] for x in rows])), 2)}
    # треки живут секунды-полминуты (слежение рвётся), поэтому пробег в минуту по ним завышен:
    # проверяем то, что от длины трека не зависит — скорость и доля невозможных значений
    bad = []
    if not 16 <= r["top_speed_median"] <= 26: bad.append("скорость треков медиана %.1f вне 16–26" % r["top_speed_median"])
    if r["over_32_share"] > 0.05: bad.append("быстрее 32 км/ч %.0f %% треков" % (100 * r["over_32_share"]))
    r["ok"] = not bad; r["reason"] = "; ".join(bad)
    return r
if __name__ == "__main__":
    print(json.dumps(summary(sys.argv[1]), ensure_ascii=False, indent=1))
