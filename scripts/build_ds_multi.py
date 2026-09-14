"""Merge auto-labelled frames from any number of matches into one YOLO dataset."""
import os, glob, random, shutil, sys, yaml

ROOT = sys.argv[1] if len(sys.argv) > 1 else "data"
OUT = os.path.join(ROOT, "yolo")

pairs = []
for d in sorted(glob.glob(os.path.join(ROOT, "*", "labels"))):
    tag = os.path.basename(os.path.dirname(d))
    for lp in sorted(glob.glob(os.path.join(d, "*.txt"))):
        img = os.path.join(os.path.dirname(d), "frames", os.path.basename(lp)[:-4] + ".jpg")
        if os.path.exists(img):
            pairs.append((tag, img, lp))
assert pairs, "no labelled frames under %s/*/labels" % ROOT

random.seed(1337)
random.shuffle(pairs)
n_val = max(1, int(len(pairs) * 0.15))
splits = {"val": pairs[:n_val], "train": pairs[n_val:]}

for sp, items in splits.items():
    for sub in ("images", "labels"):
        p = os.path.join(OUT, sub, sp)
        shutil.rmtree(p, ignore_errors=True)
        os.makedirs(p, exist_ok=True)
    for tag, img, lp in items:
        stem = "%s_%s" % (tag, os.path.basename(lp)[:-4])
        shutil.copy(img, os.path.join(OUT, "images", sp, stem + ".jpg"))
        shutil.copy(lp, os.path.join(OUT, "labels", sp, stem + ".txt"))

yaml.safe_dump({"path": os.path.abspath(OUT), "train": "images/train", "val": "images/val",
                "names": {0: "player", 1: "ball"}},
               open(os.path.join(OUT, "data.yaml"), "w"), sort_keys=False)

for sp in ("train", "val"):
    p = b = 0
    per = {}
    for lp in glob.glob(os.path.join(OUT, "labels", sp, "*.txt")):
        per[os.path.basename(lp).split("_")[0]] = per.get(os.path.basename(lp).split("_")[0], 0) + 1
        for ln in open(lp):
            if ln.strip():
                if ln[0] == "0": p += 1
                else: b += 1
    print("%s: %d кадров %s, боксов игроков %d, боксов мяча %d" % (sp, len(splits[sp]), per, p, b))
print("data.yaml:", os.path.join(OUT, "data.yaml"))
