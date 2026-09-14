"""Детекция мяча по видео моделью WASB, без их машинерии с кадрами и XML.

Зачем отдельный скрипт: штатный запуск WASB требует заранее нарезанных PNG и
разметки в формате CVAT, а нам нужно просто прогнать видео и получить положение
мяча по кадрам, чтобы сравнить с нынешним детектором YOLO.

Модель смотрит три кадра подряд и выдаёт тепловую карту вероятного положения
мяча, а не рамку. Именно это и отличает её от YOLO: мяч ищется по движению,
поэтому маленький и размытый мяч не теряется.

Кладётся внутрь WASB-SBDT/src, чтобы работали их импорты.

    python wasb_video.py --video match.mp4 --weights wasb_soccer_best.pth.tar \
        --out ball.json [--step 1] [--inp-width 512 --inp-height 288] [--vis out.mp4]
"""
import argparse, json, os, os.path as osp, sys, time
from collections import defaultdict
import numpy as np
import cv2
import torch
from torch import nn
from omegaconf import OmegaConf

HERE = osp.dirname(osp.abspath(__file__))
sys.path.insert(0, HERE)

from models import build_model
from detectors.postprocessor import TracknetV2Postprocessor
from trackers import build_tracker
from utils.image import get_affine_transform

ap = argparse.ArgumentParser()
ap.add_argument("--video", required=True)
ap.add_argument("--weights", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--config-dir", default=osp.join(HERE, "configs"))
ap.add_argument("--model", default="wasb")
ap.add_argument("--step", type=int, default=1,
                help="сдвиг окна в кадрах: 1 — каждый кадр оценивается трижды, 3 — по разу")
ap.add_argument("--inp-width", type=int, default=0, help="переопределить ширину входа модели")
ap.add_argument("--inp-height", type=int, default=0)
ap.add_argument("--score-threshold", type=float, default=0.5)
ap.add_argument("--max-frames", type=int, default=0)
ap.add_argument("--batch", type=int, default=8)
ap.add_argument("--vis", default="", help="если задан — записать видео с отметкой мяча")
ap.add_argument("--dump-candidates", default="",
                help="сохранить ВСЕ кандидаты по кадрам: позволяет потом подбирать "
                     "пороги трекера на ноутбуке, не оплачивая новый прогон")
a = ap.parse_args()

cfg = OmegaConf.create({
    "model": OmegaConf.load(osp.join(a.config_dir, "model", a.model + ".yaml")),
    "detector": OmegaConf.load(osp.join(a.config_dir, "detector", "tracknetv2.yaml")),
    "tracker": OmegaConf.load(osp.join(a.config_dir, "tracker", "online.yaml")),
    "dataloader": OmegaConf.load(osp.join(a.config_dir, "dataloader", "default.yaml")),
    "runner": {"device": "cuda", "gpus": [0]},
})
if a.inp_width:
    cfg["model"]["inp_width"] = a.inp_width
    cfg["model"]["out_width"] = a.inp_width
if a.inp_height:
    cfg["model"]["inp_height"] = a.inp_height
    cfg["model"]["out_height"] = a.inp_height
cfg["detector"]["postprocessor"]["score_threshold"] = a.score_threshold

frames_in = cfg["model"]["frames_in"]
inp_wh = (cfg["model"]["inp_width"], cfg["model"]["inp_height"])
out_wh = (cfg["model"]["out_width"], cfg["model"]["out_height"])

device = "cuda" if torch.cuda.is_available() else "cpu"
model = build_model(cfg)
ckpt = torch.load(a.weights, map_location="cpu")
sd = ckpt["model_state_dict"] if "model_state_dict" in ckpt else ckpt
sd = {k.replace("module.", ""): v for k, v in sd.items()}
model.load_state_dict(sd)
model = model.to(device).eval()
post = TracknetV2Postprocessor(cfg)
tracker = build_tracker(cfg)

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

cap = cv2.VideoCapture(a.video)
W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
FPS = cap.get(cv2.CAP_PROP_FPS)
N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
if a.max_frames:
    N = min(N, a.max_frames)

# одно и то же преобразование для всех кадров: камера не меняет разрешение
c = np.array([W / 2., H / 2.], dtype=np.float32)
s = max(H, W) * 1.0
trans_in = get_affine_transform(c, s, 0, list(inp_wh))
trans_out_inv = get_affine_transform(c, s, 0, list(out_wh), inv=1)
# постпроцессор ждёт матрицы в форме (batch, scale, 2, 3)
trans_t = torch.tensor(trans_out_inv, dtype=torch.float32)

def prep(frame):
    im = cv2.warpAffine(frame, trans_in, inp_wh, flags=cv2.INTER_LINEAR)
    im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.
    im = (im - MEAN) / STD
    return torch.from_numpy(im.transpose(2, 0, 1))

t0 = time.time()
cands = defaultdict(list)          # номер кадра -> кандидаты
buf, fids = [], []
windows, win_start = [], []
read = 0
frames_cache = {} if a.vis else None

def flush(windows, win_start):
    if not windows:
        return
    x = torch.stack(windows).to(device)
    with torch.no_grad():
        preds = model(x)
    mats = trans_t.repeat(x.shape[0], 1, 1)   # (b,2,3) — постпроцессор берёт mats[i]
    res = post.run(preds, {0: mats})
    for bi in res.keys():
        for ei in res[bi].keys():
            fid = win_start[bi] + ei
            for scale in res[bi][ei].keys():
                for xy, sc in zip(res[bi][ei][scale]["xys"], res[bi][ei][scale]["scores"]):
                    cands[fid].append({"xy": np.array(xy), "score": float(sc), "scale": scale})

while True:
    ok, frame = cap.read()
    if not ok or (a.max_frames and read >= a.max_frames):
        break
    if frames_cache is not None:
        frames_cache[read] = frame
    buf.append(prep(frame)); fids.append(read)
    if len(buf) > frames_in:
        buf.pop(0); fids.pop(0)
    if len(buf) == frames_in and (fids[0] % a.step == 0):
        windows.append(torch.cat(buf, dim=0)); win_start.append(fids[0])
        if len(windows) == a.batch:
            flush(windows, win_start); windows, win_start = [], []
    read += 1
    if read % 500 == 0:
        el = time.time() - t0
        print("%d кадров, %.1f кадр/с" % (read, read / max(el, .01)), flush=True)
flush(windows, win_start)
cap.release()

tracker.refresh()
out = {"fps": FPS, "width": W, "height": H, "frames": []}
found = 0
for fid in range(read):
    r = tracker.update(cands.get(fid, []))
    visible = bool(r["visi"])
    if visible:
        found += 1
    out["frames"].append([round(float(r["x"]), 1), round(float(r["y"]), 1),
                          round(float(r["score"]), 3)] if visible else None)
out["ball_found_pct"] = round(100 * found / max(read, 1), 1)
out["elapsed_sec"] = round(time.time() - t0, 1)
json.dump(out, open(a.out, "w"))
if a.dump_candidates:
    raw = {"fps": FPS, "width": W, "height": H, "num_frames": read,
           "candidates": {str(fid): [[round(float(c["xy"][0]), 1), round(float(c["xy"][1]), 1),
                                      round(float(c["score"]), 3)] for c in cs]
                          for fid, cs in cands.items()}}
    json.dump(raw, open(a.dump_candidates, "w"))
    print("кандидаты:", a.dump_candidates)
print("кадров %d, мяч найден в %.1f %% кадров, %.0f с" % (read, out["ball_found_pct"], out["elapsed_sec"]))

if a.vis and frames_cache:
    vw = cv2.VideoWriter(a.vis, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for fid in range(read):
        fr = frames_cache[fid]
        p = out["frames"][fid]
        if p:
            cv2.circle(fr, (int(p[0]), int(p[1])), 14, (0, 215, 255), 3)
        vw.write(fr)
    vw.release()
    print("видео:", a.vis)
