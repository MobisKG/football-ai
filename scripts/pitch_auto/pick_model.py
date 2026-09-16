"""Сравнение моделей сети линий по двум замерам и выбор лучшей.

    python pick_model.py <папка_замеров> v2 v3 [v4]  → печатает лучшую и пишет <папка>/choice.json

Замеры на модель: elkat_<m>.txt (вывод eval_manual.py на ручных опорах Элката) и
mt_<m>/result.json (разметка каждого кадра, Манас Талас 10–15 мин).
Правило: больше доля удачных кадров Манас Таласа — лучше, если ошибка на ручных опорах
не выросла больше чем на 0,1 м и худшие 10 % на отложенных кадрах — не больше чем на 0,5 м.
"""
import os, sys, json, re
D = sys.argv[1]; models = sys.argv[2:]; rows = {}
for m in models:
    r = {"model": m}
    t = open(os.path.join(D, "elkat_%s.txt" % m)).read() if os.path.exists(os.path.join(D, "elkat_%s.txt" % m)) else ""
    g = re.search(r"принято (\d+); среди принятых: mean ([\d.]+) \(медиана ([\d.]+)\), max ([\d.]+)", t)
    if g: r.update({"elkat_accepted": int(g.group(1)), "elkat_mean_m": float(g.group(2)), "elkat_max_m": float(g.group(4))})
    p = os.path.join(D, "mt_%s" % m, "result.json")
    if os.path.exists(p):
        j = json.load(open(p)); r.update({"mt_ok": j["model_ok_share"], "mt_covered": j["covered_share"], "mt_p90": j["holdout"]["p90_m"], "mt_median": j["holdout"]["median_m"], "mt_sec_per_frame": j["sec_per_frame"]})
    rows[m] = r
best = models[0]
for m in models[1:]:
    a, b = rows[best], rows[m]
    if "mt_ok" not in b: continue
    worse_manual = b.get("elkat_mean_m", 9) > a.get("elkat_mean_m", 9) + 0.1
    worse_tail = b.get("mt_p90", 99) > a.get("mt_p90", 99) + 0.5
    if b["mt_ok"] >= a.get("mt_ok", 0) and not worse_manual and not worse_tail: best = m
json.dump({"best": best, "rows": rows}, open(os.path.join(D, "choice.json"), "w"), ensure_ascii=False, indent=1)
for m, r in rows.items(): print(m, json.dumps(r, ensure_ascii=False))
print("BEST", best)
