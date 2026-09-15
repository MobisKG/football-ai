"""Сеть сегментации линий поля по классам (U-Net) и разметка для неё из ручных опор.

Классы линий не привязаны к концу поля (у постройки / у деревьев): какой конец в кадре,
решает геометрия (штрафная открыта в сторону центра). Рабочее разрешение 960×540.
"""
import os, sys, json, glob, time, random
import numpy as np, cv2
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from core import load_manual, plan_lines, project, front_sign, ROOT

CLASSES = ["bg", "touch_far", "touch_near", "goal", "box_l", "box_r", "box_f", "half"]
LINE_CLASS = {"far_touch": 1, "near_touch": 2, "goal0": 3, "goal1": 3, "box0_l": 4, "box1_l": 4, "box0_r": 5, "box1_r": 5, "box0_f": 6, "box1_f": 6, "half": 7}
WW, WH = 960, 540; SCALE = 0.5

def render_label(H_img2plan, plan, thick=3, ring=3):
    """карта классов 540×960: линии плана по матрице; кольцо вокруг линии — 255 (не учитывать)"""
    lab = np.zeros((WH, WW), np.uint8); ring_m = np.zeros((WH, WW), np.uint8)
    Hp2i = np.linalg.inv(H_img2plan); sg = front_sign(Hp2i, plan); S = np.diag([SCALE, SCALE, 1.0])
    for name, a, b in plan_lines(plan, circle=False):
        a, b = np.array(a, float), np.array(b, float); n = int(np.hypot(*(b - a)) / 0.1) + 2
        pts = a + np.linspace(0, 1, n)[:, None] * (b - a); uv, w = project(S @ Hp2i, pts); ok = w * sg > 1e-6
        run = []
        for (u, v), o in zip(uv, ok):
            if o and -2000 < u < 4000 and -2000 < v < 3000: run.append((int(round(u)), int(round(v))))
            else:
                if len(run) > 1: cv2.polylines(ring_m, [np.array(run, np.int32)], False, 1, thick + 2 * ring); cv2.polylines(lab, [np.array(run, np.int32)], False, LINE_CLASS[name], thick)
                run = []
        if len(run) > 1: cv2.polylines(ring_m, [np.array(run, np.int32)], False, 1, thick + 2 * ring); cv2.polylines(lab, [np.array(run, np.int32)], False, LINE_CLASS[name], thick)
    lab[(ring_m > 0) & (lab == 0)] = 255
    return lab

def load_auto(min_cov=0.8, min_lines=5, exclude_games=()):
    """принятые автоматические опоры как псевдоразметка: results/pitch/*/auto/frame_t*.json"""
    out = []
    for f in sorted(glob.glob(os.path.join(ROOT, "results/pitch/*/auto/frame_t*.json"))):
        try: j = json.load(open(f))
        except Exception: continue
        game = j.get("game") or f.split("/")[-3]
        if game in exclude_games or not j.get("ok") or not j.get("H_img2plan"): continue
        q = j.get("quality") or {}
        if q.get("coverage", 0) < min_cov or q.get("lines", 0) < min_lines: continue
        if not os.path.exists(j["image"]): continue
        plan = {"width": j["plan"]["width"], "length": j["plan"]["length"], "half": 21.54, "goal": 3.4, "box_w": 9.0, "box_d": 6.4}
        out.append({"game": game, "sec": j["sec"], "image": j["image"], "H": np.array(j["H_img2plan"], float), "plan": plan, "points": {}})
    return out

def dataset(exclude_games=(), only_games=None, auto=False, per_game_cap=40, seed=0):
    out = []
    for m in load_manual():
        if m["H"] is None or m["game"] in exclude_games: continue
        if only_games and m["game"] not in only_games: continue
        im = cv2.resize(cv2.imread(m["image"]), (WW, WH), interpolation=cv2.INTER_AREA)
        out.append((im, render_label(m["H"], m["plan"]), m))
    if auto:
        rng = random.Random(seed); by = {}
        for m in load_auto(exclude_games=exclude_games): by.setdefault(m["game"], []).append(m)
        for g, ms in by.items():
            for m in rng.sample(ms, min(per_game_cap, len(ms))):
                im = cv2.imread(m["image"])
                if im is None: continue
                out.append((cv2.resize(im, (WW, WH), interpolation=cv2.INTER_AREA), render_label(m["H"], m["plan"]), m))
    return out

# ---------- сеть
def build_net(nc=len(CLASSES), ch=(24, 48, 96, 160, 224)):
    import torch, torch.nn as nn
    def blk(i, o): return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True), nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True))
    class UNet(nn.Module):
        def __init__(s):
            super().__init__(); s.enc = nn.ModuleList(); p = 3
            for c in ch: s.enc.append(blk(p, c)); p = c
            s.dec = nn.ModuleList(); s.up = nn.ModuleList()
            for k in range(len(ch) - 1, 0, -1):
                s.up.append(nn.ConvTranspose2d(ch[k], ch[k - 1], 2, stride=2)); s.dec.append(blk(ch[k - 1] * 2, ch[k - 1]))
            s.head = nn.Conv2d(ch[0], nc, 1); s.pool = nn.MaxPool2d(2)
        def forward(s, x):
            fs = []
            for k, e in enumerate(s.enc):
                x = e(x if k == 0 else s.pool(x)); fs.append(x)
            x = fs[-1]
            for u, d, f in zip(s.up, s.dec, fs[-2::-1]):
                x = u(x)
                if x.shape[-2:] != f.shape[-2:]: x = nn.functional.interpolate(x, size=f.shape[-2:])
                x = d(torch.cat([x, f], 1))
            return s.head(x)
    return UNet()

MEAN = np.array([0.45, 0.45, 0.45], np.float32); STD = np.array([0.25, 0.25, 0.25], np.float32)
def to_tensor(im):
    import torch
    x = (im[:, :, ::-1].astype(np.float32) / 255 - MEAN) / STD
    return torch.from_numpy(np.ascontiguousarray(x.transpose(2, 0, 1)))

def augment(im, lab, rng, crop=(640, 384)):
    h, w = lab.shape
    s = rng.uniform(0.65, 1.25); ang = rng.uniform(-8, 8); cx, cy = rng.uniform(0.2, 0.8) * w, rng.uniform(0.2, 0.8) * h
    Mx = cv2.getRotationMatrix2D((cx, cy), ang, s); Mx[:, 2] += (crop[0] / 2 - cx, crop[1] / 2 - cy)
    im2 = cv2.warpAffine(im, Mx, crop, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    lab2 = cv2.warpAffine(lab, Mx, crop, flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    if rng.random() < 0.5: im2 = im2[:, ::-1].copy(); lab2 = lab2[:, ::-1].copy()
    # цвет: яркость, контраст, насыщенность, размытие, шум
    hsv = cv2.cvtColor(im2, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] *= rng.uniform(0.6, 1.4); hsv[:, :, 2] = hsv[:, :, 2] * rng.uniform(0.7, 1.3) + rng.uniform(-25, 25)
    im2 = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    if rng.random() < 0.4: im2 = cv2.GaussianBlur(im2, (0, 0), rng.uniform(0.5, 1.6))
    if rng.random() < 0.4: im2 = np.clip(im2.astype(np.float32) + rng.normal(0, rng.uniform(2, 8), im2.shape), 0, 255).astype(np.uint8)
    return im2, lab2

def predict(net, im, device):
    """im — BGR любого размера → вероятности классов (C, 540, 960) float32"""
    import torch
    x = to_tensor(cv2.resize(im, (WW, WH), interpolation=cv2.INTER_AREA))[None].to(device)
    with torch.no_grad(): p = torch.softmax(net(x), 1)[0].float().cpu().numpy()
    return p

def load_net(path, device=None):
    import torch
    if device is None: device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    net = build_net(); net.load_state_dict(torch.load(path, map_location="cpu")); net.to(device).eval()
    return net, device

def evaluate(net, device, data):
    """по кадрам: полнота линий (доля пикселей линии с тем же классом в ±3 px) и точность (доля предсказанных
    пикселей класса, лежащих в ±3 px от линии этого класса)"""
    rows = []
    for im, lab, m in data:
        p = predict(net, im, device); pred = p.argmax(0); conf = p.max(0)
        rec = {}; prec = {}
        for c in range(1, len(CLASSES)):
            gt = (lab == c).astype(np.uint8)
            if gt.sum() < 20: continue
            near = cv2.dilate(gt, np.ones((7, 7), np.uint8)) > 0
            pr = (pred == c) & (conf > 0.5)
            rec[CLASSES[c]] = float((cv2.dilate(pr.astype(np.uint8), np.ones((7, 7), np.uint8))[gt > 0] > 0).mean())
            prec[CLASSES[c]] = float(near[pr].mean()) if pr.sum() > 0 else 0.0
        allgt = (lab > 0) & (lab < 255); anyp = (pred > 0) & (conf > 0.5)
        rows.append({"game": m["game"], "sec": m["sec"], "recall": rec, "precision": prec,
                     "recall_any": float((cv2.dilate(anyp.astype(np.uint8), np.ones((7, 7), np.uint8))[allgt] > 0).mean()),
                     "false_any": float((anyp & ~(cv2.dilate(allgt.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0)).mean())})
    return rows

def train(out_path, exclude=(), epochs=60, iters=40, bs=4, lr=1e-3, seed=0, log=print, duty=1.0, auto=False, init=None):
    import torch, torch.nn as nn
    device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    torch.manual_seed(seed); rng = np.random.default_rng(seed)
    tr = dataset(exclude_games=exclude, auto=auto); va = dataset(only_games=exclude) if exclude else []
    log("устройство %s, обучение %d кадров, проверка %d, исключены %s" % (device, len(tr), len(va), list(exclude)))
    net = build_net()
    if init: net.load_state_dict(torch.load(init, map_location="cpu")); log("старт с весов " + init)
    net = net.to(device); opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=epochs * iters, pct_start=0.15)
    wts = torch.tensor([0.2] + [1.0] * (len(CLASSES) - 1), device=device)
    crit = nn.CrossEntropyLoss(weight=wts, ignore_index=255)
    t0 = time.time(); best = None
    for ep in range(epochs):
        net.train(); tot = 0
        for it in range(iters):
            xs, ys = [], []
            for _ in range(bs):
                im, lab, _ = tr[rng.integers(len(tr))]; a, b = augment(im, lab, rng); xs.append(to_tensor(a)); ys.append(torch.from_numpy(b.astype(np.int64)))
            x = torch.stack(xs).to(device); y = torch.stack(ys).to(device)
            loss = crit(net(x), y); opt.zero_grad(); loss.backward(); opt.step(); sched.step(); tot += loss.item()
            if duty < 1.0: time.sleep((1 - duty) * 0.3)
        msg = "эпоха %d/%d loss %.3f %.0f с" % (ep + 1, epochs, tot / iters, time.time() - t0)
        if va and ((ep + 1) % 10 == 0 or ep + 1 == epochs):
            net.eval(); rows = evaluate(net, device, va)
            r = np.mean([x["recall_any"] for x in rows]); f = np.mean([x["false_any"] for x in rows])
            pc = {c: np.mean([x["recall"][c] for x in rows if c in x["recall"]]) for c in CLASSES[1:]}
            msg += " | проверка: полнота %.2f ложные %.4f | по классам %s" % (r, f, " ".join("%s %.2f" % (k[:5], v) for k, v in pc.items()))
        log(msg)
    torch.save(net.state_dict(), out_path)
    if va:
        net.eval(); rows = evaluate(net, device, va); json.dump(rows, open(out_path.replace(".pt", "_val.json"), "w"), ensure_ascii=False, indent=1)
    return net

if __name__ == "__main__":
    # python lines_net.py <weights.pt> [--exclude game1,game2] [--epochs N]
    out = sys.argv[1]; opt = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    ex = tuple(x for x in (opt("--exclude", "") or "").split(",") if x)
    train(out, exclude=ex, epochs=int(opt("--epochs", 60)), iters=int(opt("--iters", 40)), bs=int(opt("--bs", 4)), duty=float(opt("--duty", 1.0)),
          auto="--auto" in sys.argv, init=opt("--init"), log=lambda s: print(time.strftime("%H:%M:%S"), s, flush=True))
