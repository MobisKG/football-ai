"""Уверенные кадры для дообучения сети линий (самообучение по полям №1 и №2).

    python harvest_pseudo.py --games a,b,c [--every 20] [--cap 40] [--skip slug:from-to] [--weights ...]

Из каждого видео — кадр раз в --every с, разметка текущей моделью (с галереей площадки),
в выборку идут только уверенные: принята, покрытие линий ≥ 0,85, линий ≥ 5. Не больше --cap
на игру, равномерно по времени. Кадры → results/pitch/<slug>/pseudo/tСЕК.jpg,
список → results/pitch/pseudo_f12.json. --skip исключает отрезок, на котором потом проверка.
"""
import os, sys, json, time
import numpy as np, cv2
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from core import plan_of, FIELD_PITCH, ROOT
from lines_net import load_net, predict
from solve import calibrate
from retrieval import Gallery
def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
GAMES = opt("--games").split(","); EVERY = float(opt("--every", 20)); CAP = int(opt("--cap", 40))
EVERY_F3 = float(opt("--every-f3", 0) or 0); CAP_F3 = int(opt("--cap-f3", 0) or 0)
SKIP = {}
for s in (opt("--skip") or "").split(","):
    if s: g, rng = s.split(":"); a, b = rng.split("-"); SKIP[g] = (float(a), float(b))
jobs = {j["slug"]: j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json")))}
extra = json.load(open(os.path.join(ROOT, "results/pitch/new_matches.json"))) if os.path.exists(os.path.join(ROOT, "results/pitch/new_matches.json")) else {}
def field_of(s):
    if s in jobs: return jobs[s]["field"]
    return "Полей " + extra[s][0]["field"].split()[-1] if s in extra and extra[s] else None
net, dev = load_net(opt("--weights", os.path.join(ROOT, "weights/pitch_lines_v2.pt")))
OUT = opt("--out", os.path.join(ROOT, "results/pitch/pseudo_f12.json")); rows = json.load(open(OUT)) if os.path.exists(OUT) else []
done = {r["game"] for r in rows}; gals = {}
def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)
for s in GAMES:
    if s in done: log(s, "уже собрано"); continue
    field = field_of(s); plan = plan_of(FIELD_PITCH.get(field, "lower"))
    if field not in gals: gals[field] = Gallery(field)
    ev, cp = (EVERY_F3 or EVERY, CAP_F3 or CAP) if field == "Полей №3" else (EVERY, CAP)
    video = os.path.join(ROOT, "results/pitch/videos", s + ".mp4"); cap = cv2.VideoCapture(video)
    dur = cap.get(7) / max(1, cap.get(5)); good = []; t0 = time.time(); D = os.path.join(ROOT, "results/pitch", s, "pseudo"); os.makedirs(D, exist_ok=True)
    t = 5.0
    while t < dur - 2:
        if s in SKIP and SKIP[s][0] <= t <= SKIP[s][1]: t += ev; continue
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000); ok, im = cap.read(); t += ev
        if not ok: continue
        if im.shape[1] != 1920: im = cv2.resize(im, (1920, 1080))
        prob = predict(net, im, dev); inits = [("gal", Hg, inl) for Hg, inl, g, sec in gals[field].query(im, top=2)]
        r = calibrate(prob, plan, im.shape, img=im, H_inits=inits)
        if r is None or not r["ok"]: continue
        q = r["quality"]
        if q["coverage"] < 0.85 or q["lines"] < 5: continue
        sec = int(t - ev); p = os.path.join(D, "t%04d.jpg" % sec); cv2.imwrite(p, im, [cv2.IMWRITE_JPEG_QUALITY, 92])
        good.append({"game": s, "sec": sec, "field": field, "image": p, "H": r["H_img2plan"].tolist(), "plan": {"width": plan["width"], "length": plan["length"]}, "coverage": q["coverage"]})
    if len(good) > cp: good = [good[i] for i in np.linspace(0, len(good) - 1, cp).round().astype(int)]
    rows += good; json.dump(rows, open(OUT, "w"), ensure_ascii=False)
    log(s, field, "уверенных кадров %d за %.0f мин (всего в выборке %d)" % (len(good), (time.time() - t0) / 60, len(rows)))
log("ГОТОВО", len(rows))
