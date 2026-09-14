"""Дообучение WASB на нашей съёмке. Разметка — наша же очищенная траектория.

Размеченного мяча у нас нет, поэтому учим на псевдоразметке: связная траектория
из ball_track.py. Новой информации она не несёт, но даёт отрицательные примеры —
кадры, где мяча не видно. Именно они должны отучить сеть срабатывать на заборе и
белой обуви: на проверке это давало «пас» между людьми в 1100 пикселях друг от
друга.

Штатный тренер WASB отключён в их репозитории (`runners/__init__.py`, строка
`#'train': Trainer`), а их загрузчик требует кадры в PNG и разметку в XML
формата CVAT. Здесь ничего этого не нужно: кадры берутся из заранее нарезанных
JPEG, цели строятся на лету.

    python wasb_finetune.py --frames кадры/ --labels траектория.json \
        --weights исходные.pth.tar --out дообученные.pth.tar [--epochs 3]
"""
import argparse, json, math, os, os.path as osp, random, sys, time
import numpy as np
import cv2
import torch
from torch import nn
from torch.utils.data import Dataset, DataLoader
from omegaconf import OmegaConf

HERE = osp.dirname(osp.abspath(__file__))
sys.path.insert(0, HERE)
from models import build_model

ap = argparse.ArgumentParser()
ap.add_argument("--frames", required=True, help="папка с кадрами %05d.jpg уже в нужном размере")
ap.add_argument("--labels", required=True, help="json из ball_track.py: frames[i] = [x,y,score] или null")
ap.add_argument("--weights", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--config-dir", default=osp.join(HERE, "configs"))
ap.add_argument("--epochs", type=int, default=3)
ap.add_argument("--batch", type=int, default=6)
ap.add_argument("--lr", type=float, default=2e-4)
ap.add_argument("--val-frac", type=float, default=0.15, help="последняя часть клипа — на проверку")
ap.add_argument("--neg-share", type=float, default=0.35, help="доля кадров без мяча в обучении")
ap.add_argument("--sigma", type=float, default=3.0, help="радиус пятна цели в пикселях выхода")
a = ap.parse_args()

cfg = OmegaConf.create({"model": OmegaConf.load(osp.join(a.config_dir, "model", "wasb.yaml"))})
lab = json.load(open(a.labels))
SRC_W, SRC_H = lab["width"], lab["height"]

files = sorted(f for f in os.listdir(a.frames) if f.endswith(".jpg"))
IMG_H, IMG_W = cv2.imread(osp.join(a.frames, files[0])).shape[:2]
cfg["model"]["inp_width"] = IMG_W; cfg["model"]["out_width"] = IMG_W
cfg["model"]["inp_height"] = IMG_H; cfg["model"]["out_height"] = IMG_H
FRAMES_IN = cfg["model"]["frames_in"]
SX, SY = IMG_W / SRC_W, IMG_H / SRC_H
print("кадров %d, размер %dx%d, окно %d" % (len(files), IMG_W, IMG_H, FRAMES_IN), flush=True)

xy = []
for p in lab["frames"][:len(files)]:
    xy.append(None if p is None else (p[0] * SX, p[1] * SY))
with_ball = [i for i, v in enumerate(xy) if v is not None]
print("мяч размечен в %d кадрах (%.1f %%)" % (len(with_ball), 100 * len(with_ball) / len(xy)), flush=True)

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)

def take(out):
    """Сеть отдаёт словарь «масштаб -> тензор»; нам нужен нулевой масштаб."""
    if isinstance(out, dict):
        return out[0] if 0 in out else next(iter(out.values()))
    if isinstance(out, (list, tuple)):
        return out[0]
    return out

def heatmap(c):
    hm = np.zeros((IMG_H, IMG_W), np.float32)
    if c is None:
        return hm
    cx, cy = int(round(c[0])), int(round(c[1]))
    r = int(a.sigma * 3)
    x0, x1 = max(0, cx - r), min(IMG_W, cx + r + 1)
    y0, y1 = max(0, cy - r), min(IMG_H, cy + r + 1)
    if x0 >= x1 or y0 >= y1:
        return hm
    ys, xs = np.mgrid[y0:y1, x0:x1]
    hm[y0:y1, x0:x1] = np.exp(-(((xs - cx) ** 2 + (ys - cy) ** 2) / (2 * a.sigma ** 2)))
    return hm

class Clip(Dataset):
    def __init__(self, starts):
        self.starts = starts
    def __len__(self):
        return len(self.starts)
    def __getitem__(self, k):
        s = self.starts[k]
        imgs, hms = [], []
        for j in range(s, s + FRAMES_IN):
            im = cv2.imread(osp.join(a.frames, files[j]))
            im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.
            im = (im - MEAN) / STD
            imgs.append(torch.from_numpy(im.transpose(2, 0, 1)))
            hms.append(torch.from_numpy(heatmap(xy[j])))
        return torch.cat(imgs, 0), torch.stack(hms, 0)

n_val = int(len(files) * a.val_frac)
split = len(files) - n_val
all_starts = [i for i in range(len(files) - FRAMES_IN + 1)]
def pick(starts):
    pos = [s for s in starts if any(xy[j] is not None for j in range(s, s + FRAMES_IN))]
    neg = [s for s in starts if all(xy[j] is None for j in range(s, s + FRAMES_IN))]
    k = int(len(pos) * a.neg_share / max(1e-6, 1 - a.neg_share))
    random.shuffle(neg)
    return pos + neg[:k]
train_starts = pick([s for s in all_starts if s + FRAMES_IN <= split])
val_starts = [s for s in all_starts if s >= split]
# Проверять каждое окно незачем: на 3090 полная проверка занимала минуты и
# съедала больше времени, чем сама эпоха. Берём равномерную выборку.
VAL_MAX = 400
if len(val_starts) > VAL_MAX:
    step = len(val_starts) / VAL_MAX
    val_starts = [val_starts[int(k * step)] for k in range(VAL_MAX)]
print("обучение %d окон, проверка %d окон" % (len(train_starts), len(val_starts)), flush=True)

device = "cuda" if torch.cuda.is_available() else "cpu"
model = build_model(cfg)
ck = torch.load(a.weights, map_location="cpu")
sd = ck.get("model_state_dict", ck)
model.load_state_dict({k.replace("module.", ""): v for k, v in sd.items()})
model = model.to(device)

# Взвешенная бинарная перекрёстная энтропия: мяч занимает считанные пиксели из
# полумиллиона, без веса сеть просто выучит «мяча нигде нет».
POS_W = torch.tensor(200.0, device=device)
lossf = nn.BCEWithLogitsLoss(pos_weight=POS_W)
opt = torch.optim.Adam(model.parameters(), lr=a.lr)

def run_val():
    model.eval()
    hit = tot = 0
    with torch.no_grad():
        for imgs, hms in DataLoader(Clip(val_starts), batch_size=a.batch, num_workers=4):
            out = model(imgs.to(device))
            pred = take(out)
            pred = torch.sigmoid(pred).cpu().numpy()
            tgt = hms.numpy()
            for b in range(pred.shape[0]):
                for e in range(pred.shape[1]):
                    if tgt[b, e].max() < 0.5:
                        continue
                    ty, tx = np.unravel_index(tgt[b, e].argmax(), tgt[b, e].shape)
                    py, px = np.unravel_index(pred[b, e].argmax(), pred[b, e].shape)
                    tot += 1
                    if math.hypot(px - tx, py - ty) <= 8 and pred[b, e].max() > 0.5:
                        hit += 1
    model.train()
    return hit, tot

h, t = run_val()
print("до обучения: попаданий %d из %d (%.1f %%)" % (h, t, 100 * h / max(t, 1)), flush=True)

loader = DataLoader(Clip(train_starts), batch_size=a.batch, shuffle=True, num_workers=6, drop_last=True)
model.train()
for ep in range(a.epochs):
    t0, tot_loss, nb = time.time(), 0.0, 0
    for i, (imgs, hms) in enumerate(loader):
        imgs, hms = imgs.to(device), hms.to(device)
        out = model(imgs)
        pred = take(out)
        loss = lossf(pred, hms)
        opt.zero_grad(); loss.backward(); opt.step()
        tot_loss += loss.item(); nb += 1
        if i % 50 == 0:
            print("эпоха %d, окно %d/%d, потеря %.4f, %.1f окон/с"
                  % (ep + 1, i, len(loader), tot_loss / nb, (i + 1) * a.batch / max(time.time() - t0, .01)), flush=True)
    h, t = run_val()
    print("эпоха %d закончена: потеря %.4f, попаданий %d из %d (%.1f %%)"
          % (ep + 1, tot_loss / max(nb, 1), h, t, 100 * h / max(t, 1)), flush=True)
    torch.save({"model_state_dict": model.state_dict()}, a.out)
print("сохранено:", a.out)
