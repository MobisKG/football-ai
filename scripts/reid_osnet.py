"""Векторы внешности моделью для опознания людей (OSNet, обучена на MSMT17).

Общий DINOv2 не различал игроков одной формы (см. STATE.md). OSNet учили
именно отличать людей друг от друга в похожей одежде. Веса из релизов
boxmot (формат torchreid). Вектор трека — среднее по снимкам, нормировано.

    python reid_osnet.py crops_dir out.json
"""
import json, os, os.path as osp, sys, urllib.request
import numpy as np, torch
from PIL import Image
import torchvision.transforms as T
import torchreid

crops_dir, out_path = sys.argv[1], sys.argv[2]
W = "osnet_x1_0_msmt17.pt"
if not osp.exists(W):
    urllib.request.urlretrieve("https://github.com/mikel-brostrom/yolo_tracking/releases/download/v9.0/" + W, W)
dev = "cuda" if torch.cuda.is_available() else "cpu"
model = torchreid.models.build_model("osnet_x1_0", num_classes=1, pretrained=False)
torchreid.utils.load_pretrained_weights(model, W)
model = model.to(dev).eval()
tf = T.Compose([T.Resize((256, 128)), T.ToTensor(), T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
meta = json.load(open(osp.join(crops_dir, "tracks.json")))
out = {}
with torch.no_grad():
    for n, (tid, m) in enumerate(meta.items()):
        vs = []
        for s in m["shots"]:
            p = osp.join(crops_dir, s)
            if not osp.exists(p): continue
            f = model(tf(Image.open(p).convert("RGB")).unsqueeze(0).to(dev))[0].float().cpu().numpy()
            vs.append(f / (np.linalg.norm(f) + 1e-6))
        if vs:
            v = np.mean(vs, 0); v /= np.linalg.norm(v) + 1e-6
            out[tid] = {"vec": v.round(5).tolist(), "n": len(vs), "team": m["team"], "frames_seen": m["frames_seen"],
                        "shots": [x.round(4).tolist() for x in vs]}
        if (n + 1) % 50 == 0: print(n + 1, "треков", flush=True)
json.dump(out, open(out_path, "w"))
print("готово:", len(out), "треков")
