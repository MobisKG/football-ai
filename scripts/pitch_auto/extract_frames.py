"""Кадры каждые N с из локального видео → results/pitch/<slug>/frames_<slug>_full/t<сек>.jpg (1920×1080).
    python extract_frames.py <slug> [--every 15]"""
import os, sys, cv2
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
slug = sys.argv[1]; every = float(sys.argv[sys.argv.index("--every") + 1]) if "--every" in sys.argv else 15.0
v = next((x for x in (os.path.join(ROOT, "results/pitch/videos", slug + ".mp4"), os.path.join(ROOT, "videos", slug + ".mp4")) if os.path.exists(x)), None)
if not v: sys.exit("нет видео для " + slug)
o = os.path.join(ROOT, "results/pitch", slug, "frames_%s_full" % slug); os.makedirs(o, exist_ok=True)
c = cv2.VideoCapture(v); f = c.get(5); n = c.get(7); t = 0.0; k = 0
while t < n / f:
    c.set(cv2.CAP_PROP_POS_MSEC, t * 1000); ok, fr = c.read()
    if not ok: break
    if fr.shape[1] != 1920: fr = cv2.resize(fr, (1920, 1080), interpolation=cv2.INTER_LINEAR)
    cv2.imwrite("%s/t%04d.jpg" % (o, int(t)), fr, [cv2.IMWRITE_JPEG_QUALITY, 92]); t += every; k += 1
print("кадров", k, "в", o)
