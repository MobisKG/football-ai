"""Проверка автоматической привязки на ручных опорах: python eval_manual.py weights.pt [игры через запятую] [--vis dir]"""
import os, sys, json, time
import numpy as np, cv2
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from core import load_manual, landmark_error
from lines_net import load_net, predict, CLASSES
from solve import calibrate, draw_plan, detect_lines
w = sys.argv[1]; games = sys.argv[2].split(",") if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
vis = sys.argv[sys.argv.index("--vis") + 1] if "--vis" in sys.argv else None
net, dev = load_net(w); M = [m for m in load_manual() if m["H"] is not None and (not games or m["game"] in games)]
rows = []; t0 = time.time()
for m in M:
    im = cv2.imread(m["image"])
    if im.shape[1] != 1920: im = cv2.resize(im, (1920, 1080))
    p = predict(net, im, dev); r = calibrate(p, m["plan"], im.shape)
    det = detect_lines(p)
    if r is None: print("%-15s %5d  нет решения; линии: %s" % (m["game"], m["sec"], {k: len(v) for k, v in det.items()})); rows.append((m["game"], m["sec"], None, None, 0, False)); continue
    e = landmark_error(r["H_img2plan"], m["points"], m["plan"]); q = r["quality"]
    rows.append((m["game"], m["sec"], np.mean(list(e.values())), max(e.values()), q["coverage"], r["ok"]))
    print("%-15s %5d  %s ошибка %.2f/%.2f  cov %.2f lines %d ok %s  найдено %s" % (m["game"], m["sec"], r["src"], rows[-1][2], rows[-1][3], q["coverage"], q["lines"], r["ok"], r["det"]), flush=True)
    if vis:
        os.makedirs(vis, exist_ok=True); v = cv2.resize(im, (960, 540)); draw_plan(v, m["H"], m["plan"], (0, 255, 255), 1); draw_plan(v, r["H_img2plan"], m["plan"], (0, 0, 255), 2)
        pred = p.argmax(0); conf = p.max(0); cols = [(0,0,0),(255,0,0),(0,255,0),(0,0,255),(255,255,0),(255,0,255),(0,255,255),(255,255,255)]
        pv = np.zeros_like(v)
        for c in range(1, 8): pv[(pred == c) & (conf > 0.5)] = cols[c]
        cv2.putText(v, "%s %d err %.2f/%.2f cov %.2f ok %s" % (m["game"], m["sec"], rows[-1][2], rows[-1][3], q["coverage"], r["ok"]), (10, 25), 0, 0.7, (255, 255, 255), 2)
        cv2.imwrite(os.path.join(vis, "ev_%s_%d.jpg" % (m["game"], m["sec"])), np.vstack([v, pv]))
ok = [r for r in rows if r[2] is not None]
acc = [r for r in ok if r[5]]
print("кадров %d, решено %d, принято %d; среди принятых: mean %.2f (медиана %.2f), max %.2f; доля принятых с mean<0.7&max<1.5: %.2f; %.1f с/кадр" % (
    len(rows), len(ok), len(acc), np.mean([r[2] for r in acc]) if acc else 0, np.median([r[2] for r in acc]) if acc else 0, max([r[3] for r in acc]) if acc else 0,
    np.mean([(r[2] < 0.7 and r[3] < 1.5) for r in acc]) if acc else 0, (time.time() - t0) / len(rows)))
