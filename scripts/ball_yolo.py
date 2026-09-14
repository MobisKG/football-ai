"""Поиск мяча нашим детектором с полным разрешением и выгрузкой всех кандидатов.

Зачем отдельно от analyze_match.py: там кадр сжимается до 1280 и порог
уверенности высокий, мяч находился в 27–32 % кадров. Замер на клипе:

    целиком 1280 → 52 %, целиком 1920 → 60 %, нарезка 640 → 63 %

При этом точность не страдает: там, где находил прежний разбор, новый способ
согласен с ним во всех случаях. Взято 1920: почти как нарезка, но вдвое быстрее.

Выгружаются ВСЕ кандидаты, а не лучший: связную траекторию потом строит
ball_track.py, и ему нужен выбор. Формат совпадает с выгрузкой WASB.

    python ball_yolo.py --video клип.mp4 --weights best.pt --out cands.json \
        [--step 2] [--imgsz 1920] [--conf 0.12] [--slice 0]
"""
import argparse, json, time
import numpy as np, cv2, torch
from ultralytics import YOLO

BALL = 1

ap = argparse.ArgumentParser()
ap.add_argument("--video", required=True)
ap.add_argument("--weights", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--step", type=int, default=2, help="брать каждый N-й кадр исходника")
ap.add_argument("--imgsz", type=int, default=1920)
ap.add_argument("--conf", type=float, default=0.12)
ap.add_argument("--slice", type=int, default=0, help="если >0 — резать кадр на куски такого размера")
ap.add_argument("--device", default="")
ap.add_argument("--max-frames", type=int, default=0)
a = ap.parse_args()

dev = a.device or ("cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu"))
model = YOLO(a.weights)
cap = cv2.VideoCapture(a.video)
W = int(cap.get(3)); H = int(cap.get(4)); FPS = cap.get(cv2.CAP_PROP_FPS)
N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
print("видео %dx%d, %.1f кадр/с, кадров %d, устройство %s" % (W, H, FPS, N, dev), flush=True)

def detect(frame):
    if a.slice:
        tile, out = a.slice, []
        step = int(tile * 0.75)
        for y0 in range(0, H - 1, step):
            for x0 in range(0, W - 1, step):
                x1, y1 = min(x0 + tile, W), min(y0 + tile, H)
                if x1 - x0 < 64 or y1 - y0 < 64:
                    continue
                r = model.predict(frame[y0:y1, x0:x1], device=dev, verbose=False,
                                  conf=a.conf, imgsz=tile)[0]
                for b, c, cf in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.cls.cpu().numpy(),
                                    r.boxes.conf.cpu().numpy()):
                    if int(c) == BALL:
                        out.append([x0 + float(b[0] + b[2]) / 2, y0 + float(b[1] + b[3]) / 2, float(cf)])
        return out
    r = model.predict(frame, device=dev, verbose=False, conf=a.conf, imgsz=a.imgsz)[0]
    return [[float(b[0] + b[2]) / 2, float(b[1] + b[3]) / 2, float(cf)]
            for b, c, cf in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.cls.cpu().numpy(),
                                r.boxes.conf.cpu().numpy()) if int(c) == BALL]

cands, i, done, t0 = {}, 0, 0, time.time()
while True:
    ok, frame = cap.read()
    if not ok or (a.max_frames and done >= a.max_frames):
        break
    if i % a.step == 0:
        res = detect(frame)
        if res:
            cands[str(i)] = [[round(x, 1), round(y, 1), round(c, 3)] for x, y, c in res]
        done += 1
        if done % 200 == 0:
            el = time.time() - t0
            print("%d кадров, %.2f кадр/с, мяч в %.0f %%" %
                  (done, done / max(el, .01), 100 * len(cands) / done), flush=True)
    i += 1
cap.release()

out = {"fps": FPS / a.step, "width": W, "height": H, "num_frames": (i + a.step - 1) // a.step,
       "step": a.step, "candidates": {str(int(k) // a.step): v for k, v in cands.items()}}
json.dump(out, open(a.out, "w"))
print("готово: кадров %d, мяч хотя бы в одном кандидате в %.1f %% кадров, %.0f с"
      % (done, 100 * len(cands) / max(done, 1), time.time() - t0))
