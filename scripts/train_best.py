"""Final training. Picks model size and batch from the GPU actually present.

Small-object detection is driven far more by input resolution than by model
capacity, so imgsz is kept high and batch is traded down to fit.
"""
import argparse, os, subprocess, torch
from ultralytics import YOLO

ap = argparse.ArgumentParser()
ap.add_argument("--data", default="data/yolo/data.yaml")
ap.add_argument("--model", default="")
ap.add_argument("--imgsz", type=int, default=0)
ap.add_argument("--batch", type=int, default=0)
ap.add_argument("--epochs", type=int, default=60)
ap.add_argument("--name", default="football_final")
a = ap.parse_args()

assert torch.cuda.is_available(), "CUDA не найдена"
gb = torch.cuda.get_device_properties(0).total_memory / 1024**3
print("GPU: %s, %.0f ГБ" % (torch.cuda.get_device_name(0), gb))

# (min VRAM, model, imgsz, batch)
PROFILES = [
    (40, "yolo11x.pt", 1536, 16),
    (22, "yolo11l.pt", 1536, 8),
    (14, "yolo11l.pt", 1280, 8),
    (10, "yolo11m.pt", 1280, 8),
    (0,  "yolo11s.pt", 1280, 4),
]
model, imgsz, batch = next((m, s, b) for lim, m, s, b in PROFILES if gb >= lim)
model = a.model or model
imgsz = a.imgsz or imgsz
batch = a.batch or batch
print("выбрано: %s, imgsz %d, batch %d, эпох %d" % (model, imgsz, batch, a.epochs))

YOLO(model).train(
    data=os.path.abspath(a.data),
    epochs=a.epochs, imgsz=imgsz, batch=batch, device=0, workers=8,
    project="runs", name=a.name, exist_ok=True,
    patience=20, seed=0, cache=False, amp=True,
    # augmentation tuned for one venue filmed from a moving camera:
    # colour and scale vary a lot, geometry does not flip vertically
    hsv_h=0.015, hsv_s=0.7, hsv_v=0.5,
    degrees=0.0, translate=0.1, scale=0.5, shear=0.0, perspective=0.0,
    fliplr=0.5, flipud=0.0, mosaic=1.0, close_mosaic=10, erasing=0.4,
)
print("готово: runs/%s/weights/best.pt" % a.name)
