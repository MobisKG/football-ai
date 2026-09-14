"""Вектор внешности по каждому треку: DINOv2 (общий вид) + цвета ног (бутсы, гетры).

Зачем: номер читается только на удачных кадрах, а внешность видна всегда.
По векторам треки одного человека склеиваются (reid_cluster.py), и номер,
прочитанный один раз, достаётся всем его кускам.

Вектор снимка — конкатенация: DINOv2 ViT-S/14 CLS (384, нормирован) и
гистограмма HSV нижней трети вырезки (обувь и гетры, 48 бинов, нормирована,
вес 0.7). Вектор трека — среднее по снимкам.

    python reid_embed.py crops_dir out.json
"""
import json, os.path as osp, sys
import numpy as np, torch
from PIL import Image
import torchvision.transforms as T

crops_dir, out_path = sys.argv[1], sys.argv[2]
meta = json.load(open(osp.join(crops_dir, "tracks.json")))
dev = "cuda" if torch.cuda.is_available() else "cpu"
model = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14").to(dev).eval()
tf = T.Compose([T.Resize((224, 112)), T.ToTensor(), T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])

def legs_hist(img):
    a = np.asarray(img.convert("HSV"), dtype=np.float32)
    h = a.shape[0]; legs = a[int(h * 0.66):]
    hist, _ = np.histogramdd(legs.reshape(-1, 3), bins=(12, 2, 2), range=((0, 256), (0, 256), (0, 256)))
    v = hist.flatten(); return v / (np.linalg.norm(v) + 1e-6)

out = {}
with torch.no_grad():
    for n, (tid, m) in enumerate(meta.items()):
        vs = []
        for s in m["shots"]:
            p = osp.join(crops_dir, s)
            if not osp.exists(p): continue
            img = Image.open(p).convert("RGB")
            f = model(tf(img).unsqueeze(0).to(dev))[0].float().cpu().numpy()
            f = f / (np.linalg.norm(f) + 1e-6)
            vs.append(np.concatenate([f, 0.7 * legs_hist(img)]))
        if vs:
            out[tid] = {"vec": np.mean(vs, 0).round(5).tolist(), "n": len(vs), "team": m["team"], "frames_seen": m["frames_seen"]}
        if (n + 1) % 50 == 0: print(n + 1, "треков", flush=True)
json.dump(out, open(out_path, "w"))
print("готово:", len(out), "треков")
