"""Measure how often a jersey number is legible, as a function of player box height.

Nothing is trained here: this only answers whether the footage carries enough
detail for number-based player identification to be worth building.
"""
import argparse, json, os
from collections import Counter, defaultdict
import numpy as np, cv2, supervision as sv, easyocr
from ultralytics import YOLO

ap = argparse.ArgumentParser()
ap.add_argument("--weights", required=True)
ap.add_argument("--source", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--stride", type=int, default=10)
ap.add_argument("--device", default="mps")
a = ap.parse_args()

model = YOLO(a.weights)
reader = easyocr.Reader(["en"], gpu=False)   # CPU keeps the GPU free for detection

crops_total = 0
reads = []          # (box_height, digits, confidence)
for i, frame in enumerate(sv.get_video_frames_generator(a.source, stride=a.stride)):
    det = sv.Detections.from_ultralytics(
        model.predict(frame, device=a.device, verbose=False, conf=0.35, imgsz=1280)[0])
    for x1, y1, x2, y2 in det[det.class_id == 0].xyxy:
        h = y2 - y1
        if h < 25:
            continue
        crops_total += 1
        # torso strip: below the head, above the shorts, where the number sits
        ty1, ty2 = int(y1 + 0.18 * h), int(y1 + 0.55 * h)
        c = frame[max(ty1, 0):ty2, max(int(x1), 0):int(x2)]
        if c.size == 0:
            continue
        c = cv2.resize(c, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        c = cv2.cvtColor(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        try:
            res = reader.readtext(c, allowlist="0123456789", detail=1,
                                  text_threshold=0.6, low_text=0.3)
        except Exception:
            continue
        for _, txt, conf in res:
            t = txt.strip()
            if t.isdigit() and 1 <= len(t) <= 2:
                reads.append((float(h), t, float(conf)))
    if i % 10 == 0:
        print("frame group %d, crops %d, reads %d" % (i, crops_total, len(reads)), flush=True)

json.dump({"crops": crops_total,
           "reads": [[h, t, c] for h, t, c in reads]}, open(a.out, "w"))

conf50 = [r for r in reads if r[2] >= 0.5]
print("\n=== RESULT ===")
print("crops examined      %d" % crops_total)
print("any digit read      %d  (%.1f %%)" % (len(reads), 100 * len(reads) / max(crops_total, 1)))
print("confident (>=0.5)   %d  (%.1f %%)" % (len(conf50), 100 * len(conf50) / max(crops_total, 1)))
bands = [(25, 60), (60, 100), (100, 160), (160, 10000)]
print("\nчитаемость по росту игрока в кадре:")
for lo, hi in bands:
    tot = sum(1 for h, _, _ in reads if lo <= h < hi)
    okc = sum(1 for h, _, c in reads if lo <= h < hi and c >= 0.5)
    print("  %4d-%-5s px: прочтений %4d, уверенных %4d" % (lo, hi if hi < 9999 else "+", tot, okc))
print("\nчаще всего распознанные числа:")
for t, c in Counter(t for _, t, cf in reads if cf >= 0.5).most_common(10):
    print("  %-4s %d" % (t, c))
