"""Автоматические опоры для всех кадров игры (каждые 15 с) и лист проверки.

    python calibrate_game.py <slug> [--weights weights/pitch_lines.pt] [--exclude-manual] [--frames dir]

Пишет results/pitch/<slug>/auto/frame_t<сек>.json (формат как у pitch_marker: frame_sec, H_img2plan,
plan, source=auto, quality, ok), results/pitch/<slug>/auto/summary.json и check_auto.jpg —
8 случайных принятых кадров с наложенной разметкой (+ 4 худших/отклонённых для контроля).
Кандидаты матрицы для кадра: DLT по найденным линиям, сверка с опорами площадки (SIFT),
матрица соседнего кадра; каждый уточняется по картам классов, берётся лучший по покрытию.
"""
import os, sys, json, glob, time, re, random
import numpy as np, cv2
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from core import load_manual, plan_of, FIELD_PITCH, ROOT, landmark_error
from lines_net import load_net, predict
from solve import calibrate, draw_plan, class_dist, refine_cls, detect_lines, solve_dlt
from retrieval import Gallery

def jsonable(o):
    if isinstance(o, dict): return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [jsonable(v) for v in o]
    if isinstance(o, (np.bool_,)): return bool(o)
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)): return float(o)
    if isinstance(o, np.ndarray): return o.tolist()
    return o
def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
def frames_of(slug, frames_dir=None):
    d = frames_dir or os.path.join(ROOT, "results/pitch", slug, "frames_%s_full" % slug)
    if not os.path.isdir(d):
        alt = glob.glob(os.path.join(ROOT, "results/pitch", slug, "full_*")); d = alt[0] if alt else d
    fs = sorted(glob.glob(os.path.join(d, "t*.jpg")), key=lambda f: int(re.search(r"(\d+)", os.path.basename(f)).group(1)))
    return [(int(re.search(r"(\d+)", os.path.basename(f)).group(1)), f) for f in fs]

def run(slug, weights, exclude_manual=False, frames_dir=None, log=print, use_gallery=True, self_gallery=True):
    jobs = {j["slug"]: j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json")))}
    job = jobs[slug]; field = job["field"]; plan = plan_of(FIELD_PITCH.get(field, "lower"))
    # ширина поля: как в ручных опорах этой игры/площадки, если есть
    net, dev = load_net(weights)
    gal = Gallery(field, exclude_games=(slug,) if exclude_manual else ()) if use_gallery else None
    if gal is not None: log("галерея площадки %s: %d опор" % (field, len(gal.items)))
    out_dir = os.path.join(ROOT, "results/pitch", slug, "auto"); os.makedirs(out_dir, exist_ok=True)
    fr = frames_of(slug, frames_dir); log("кадров %d" % len(fr))
    results = []; prev = None; t0 = time.time()
    for k, (sec, path) in enumerate(fr):
        im = cv2.imread(path)
        if im.shape[1] != 1920: im = cv2.resize(im, (1920, 1080), interpolation=cv2.INTER_LINEAR)   # матрицы — в координатах 1920×1080
        prob = predict(net, im, dev)
        inits = []
        if gal is not None:
            for Hg, inl, g, s in gal.query(im, top=2): inits.append(("gal_%s_%d" % (g, s), Hg, inl))
        if prev is not None: inits.append(("prev", prev))
        r = calibrate(prob, plan, im.shape, img=im, H_inits=inits)
        best = (None, r) if r is not None else None
        rec = {"frame_sec": sec, "sec": sec, "source": "auto", "plan": {"width": plan["width"], "length": plan["length"]}, "image": path, "game": slug}
        if best is None:
            rec.update({"H_img2plan": None, "ok": False, "quality": None, "src": None}); prev = None
        else:
            r = best[1]; rec.update({"H_img2plan": r["H_img2plan"].tolist(), "ok": r["ok"], "tier": r.get("tier"), "quality": {k2: v for k2, v in r["quality"].items() if k2 not in ("per_line", "good")}, "per_line": r["quality"]["per_line"], "src": r["src"], "det": r["det"]})
            prev = r["H_img2plan"] if r["ok"] else None
        rec = jsonable(rec); json.dump(rec, open(os.path.join(out_dir, "frame_t%04d.json" % sec), "w"), ensure_ascii=False, indent=1); results.append(rec)
        if (k + 1) % 20 == 0: log("  %d/%d, принято %d, %.0f с" % (k + 1, len(fr), sum(1 for x in results if x["ok"]), time.time() - t0))
    acc = [r for r in results if r["ok"]]
    # второй проход: галерея из собственных уверенных кадров игры для отклонённых/слабых
    if self_gallery:
        seeds = [(r["image"], np.array(r["H_img2plan"]), slug, r["sec"]) for r in acc if r["quality"]["coverage"] >= 0.8 and r["quality"]["lines"] >= 5]
        if len(seeds) >= 3:
            g2 = Gallery(field, exclude_games=(slug,) if exclude_manual else (), extra=seeds, nfeat=3000) if gal is not None else Gallery("__none__", extra=seeds, nfeat=3000)
            redo = [k for k, r in enumerate(results) if not r["ok"] or r["quality"]["coverage"] < 0.7]; fixed = 0
            log("второй проход: галерея из %d своих кадров, пересчёт %d кадров" % (len(seeds), len(redo)))
            for k in redo:
                r0 = results[k]; im = cv2.imread(r0["image"])
                if im.shape[1] != 1920: im = cv2.resize(im, (1920, 1080))
                prob = predict(net, im, dev); inits = [("self_%d" % s2, Hg, inl) for Hg, inl, g, s2 in g2.query(im, top=3)]
                if not inits: continue
                r = calibrate(prob, plan, im.shape, img=im, H_inits=inits, classfree=False)
                if r is None: continue
                better = r["ok"] and (not r0["ok"] or r["quality"]["coverage"] > r0["quality"]["coverage"])
                if better:
                    r0.update(jsonable({"H_img2plan": r["H_img2plan"].tolist(), "ok": r["ok"], "tier": r.get("tier"), "quality": {k2: v for k2, v in r["quality"].items() if k2 not in ("per_line", "good")}, "per_line": r["quality"]["per_line"], "src": r["src"], "det": r["det"]}))
                    json.dump(r0, open(os.path.join(out_dir, "frame_t%04d.json" % r0["sec"]), "w"), ensure_ascii=False, indent=1); fixed += 1
            log("второй проход: улучшено %d" % fixed); acc = [r for r in results if r["ok"]]
    summ = {"slug": slug, "field": field, "frames": len(results), "accepted": len(acc), "strong": sum(1 for r in acc if r.get("tier") == "strong"), "weights": os.path.basename(weights), "time_s": round(time.time() - t0),
            "sources": {}, "coverage_median": float(np.median([r["quality"]["coverage"] for r in acc])) if acc else None}
    for r in acc: summ["sources"][r["src"].split("_")[0]] = summ["sources"].get(r["src"].split("_")[0], 0) + 1
    # сверка с ручными опорами этой игры, если есть
    man = {m["sec"]: m for m in load_manual() if m["game"] == slug and m["H"] is not None}
    errs = []
    for r in results:
        if r["sec"] in man and r["H_img2plan"] is not None:
            e = landmark_error(np.array(r["H_img2plan"]), man[r["sec"]]["points"], man[r["sec"]]["plan"]); errs.append((r["sec"], float(np.mean(list(e.values()))), float(max(e.values())), r["ok"]))
    if errs:
        a = [e for e in errs if e[3]]
        summ["manual_check"] = {"n": len(errs), "accepted": len(a), "mean_m": float(np.mean([e[1] for e in a])) if a else None, "max_m": float(max(e[2] for e in a)) if a else None, "rows": errs}
    json.dump(jsonable(summ), open(os.path.join(out_dir, "summary.json"), "w"), ensure_ascii=False, indent=1)
    sheet(slug, results, plan, os.path.join(ROOT, "results/pitch", slug, "check_auto.jpg"))
    log("принято %d из %d; %s" % (len(acc), len(results), json.dumps(summ.get("manual_check", {}), ensure_ascii=False)[:300]))
    return summ

def sheet(slug, results, plan, out_path, n=8, seed=0):
    acc = [r for r in results if r["ok"]]; rej = [r for r in results if not r["ok"]]
    rnd = random.Random(seed); pick = rnd.sample(acc, min(n, len(acc)))
    worst = sorted(acc, key=lambda r: r["quality"]["coverage"])[:2] + rnd.sample(rej, min(2, len(rej)))
    if not acc and not rej: return
    tiles = []
    for r, tag in [(r, "случайный") for r in pick] + [(r, "контроль") for r in worst]:
        im = cv2.resize(cv2.imread(r["image"]), (960, 540))
        if r["H_img2plan"] is not None: draw_plan(im, np.array(r["H_img2plan"]), plan, (0, 255, 255) if r["ok"] else (0, 0, 255), 2)
        q = r["quality"] or {}
        cv2.rectangle(im, (0, 0), (960, 28), (0, 0, 0), -1)
        cv2.putText(im, "%s t=%d %s %s cov %.2f lines %s %s" % (slug, r["sec"], tag, "OK" if r["ok"] else "ОТКЛ", q.get("coverage", 0) or 0, q.get("lines", "-"), r.get("src") or ""), (8, 20), 0, 0.6, (255, 255, 255), 1)
        tiles.append(im)
    if not tiles: return
    while len(tiles) % 2: tiles.append(np.zeros((540, 960, 3), np.uint8))
    rows = [np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)]
    cv2.imwrite(out_path, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 80])

if __name__ == "__main__":
    slug = sys.argv[1]; w = opt("--weights", os.path.join(ROOT, "weights/pitch_lines.pt"))
    run(slug, w, "--exclude-manual" in sys.argv, opt("--frames"), log=lambda s: print(time.strftime("%H:%M:%S"), slug, s, flush=True), use_gallery="--no-gallery" not in sys.argv)
