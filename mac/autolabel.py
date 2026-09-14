import argparse, os, glob, json
import numpy as np, cv2, supervision as sv
from ultralytics import YOLO

def turf_mask(img, thr=20):
    h, w = img.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(img, (7, 7), 0), cv2.COLOR_BGR2LAB).astype(np.int16)
    seed = lab[int(h * 0.75):int(h * 0.98), int(w * 0.25):int(w * 0.75)].reshape(-1, 3)
    med = np.median(seed, axis=0)
    d = np.sqrt(((lab - med) ** 2).sum(axis=2))
    m = (d < thr).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((31, 31), np.uint8))
    n, lbl, st, _ = cv2.connectedComponentsWithStats(m, 8)
    if n > 1:
        k = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        m = (lbl == k).astype(np.uint8) * 255
    # The pitch is a convex quad in image space. Taking the convex hull closes the
    # holes that shadows and worn patches punch in the colour mask, which would
    # otherwise drag the per-column horizon down into the middle of the pitch.
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cnts:
        hull = cv2.convexHull(max(cnts, key=cv2.contourArea))
        m = np.zeros_like(m)
        cv2.fillConvexPoly(m, hull, 255)
    return m


def horizon(m):
    """Per-column topmost turf row; h+1 where the column holds no turf at all."""
    h, w = m.shape
    idx = np.argmax(m > 0, axis=0).astype(np.int32)
    idx[m.max(axis=0) == 0] = h + 1
    return cv2.blur(idx.astype(np.float32).reshape(1, -1), (51, 1)).ravel()

ap = argparse.ArgumentParser()
ap.add_argument("--frames", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--device", default="mps")
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--debug-dir", default="")
ap.add_argument("--extra-ball-weights", default="",
                help="additional in-domain detector used as a ball proposer")
ap.add_argument("--slice", type=int, default=640)
a = ap.parse_args()

D = os.path.expanduser("~/football-ai/sports/examples/soccer/data")
coco = YOLO("yolo11x.pt")
ballm = YOLO(f"{D}/football-ball-detection.pt")
extram = YOLO(a.extra_ball_weights) if a.extra_ball_weights else None

files = sorted(glob.glob(os.path.join(a.frames, "*.jpg")))
if a.limit:
    files = files[:: max(1, len(files) // a.limit)][: a.limit]

os.makedirs(a.out, exist_ok=True)
if a.debug_dir:
    os.makedirs(a.debug_dir, exist_ok=True)

slicer = sv.InferenceSlicer(
    callback=lambda p: sv.Detections.from_ultralytics(
        ballm.predict(p, device=a.device, verbose=False, conf=0.25, imgsz=640)[0]),
    slice_wh=(a.slice, a.slice), overlap_wh=(a.slice // 5, a.slice // 5),
)

report = {"frames": 0, "with_ball": 0, "players_total": 0, "dropped_offpitch": 0, "per_frame": []}

for idx, fp in enumerate(files):
    img = cv2.imread(fp)
    h, w = img.shape[:2]
    tm = turf_mask(img)
    hz = horizon(tm)
    turf_frac = float(tm.mean() / 255)

    r = coco.predict(img, device=a.device, verbose=False, conf=0.30, imgsz=1280, classes=[0, 32])[0]
    det = sv.Detections.from_ultralytics(r)

    persons = det[det.class_id == 0]
    lines = []
    kept = 0
    for x1, y1, x2, y2 in persons.xyxy:
        fx, fy = int((x1 + x2) / 2), int(min(y2, h - 1))
        if fy < hz[min(max(fx, 0), w - 1)] + 8:
            report["dropped_offpitch"] += 1
            continue
        bw, bh = (x2 - x1) / w, (y2 - y1) / h
        if bw <= 0 or bh <= 0:
            continue
        lines.append("0 %.6f %.6f %.6f %.6f" % ((x1 + x2) / 2 / w, (y1 + y2) / 2 / h, bw, bh))
        kept += 1

    # ball: tiled specialist model, backed up by COCO sports-ball
    cands = []
    bd = slicer(img)
    for bb, cf in zip(bd.xyxy, bd.confidence):
        cands.append((float(cf), bb))
    sb = det[det.class_id == 32]
    for bb, cf in zip(sb.xyxy, sb.confidence):
        cands.append((float(cf) * 0.9, bb))
    if extram is not None:
        # An in-domain detector trained on the sibling match is a far stronger ball
        # proposer here than the generic Bundesliga model, so weight it highest.
        er = extram.predict(img, device=a.device, verbose=False, conf=0.25, imgsz=1280)[0]
        ed = sv.Detections.from_ultralytics(er)
        eb = ed[ed.class_id == 1]
        for bb, cf in zip(eb.xyxy, eb.confidence):
            cands.append((float(cf), bb))
    cands = [(c, b) for c, b in cands
             if c >= 0.40 and min(b[3], h - 1) > hz[min(max(int((b[0] + b[2]) / 2), 0), w - 1)] + 8]
    has_ball = 0
    if cands:
        c, b = max(cands, key=lambda t: t[0])
        x1, y1, x2, y2 = b
        bw, bh = max(x2 - x1, 6) / w, max(y2 - y1, 6) / h
        lines.append("1 %.6f %.6f %.6f %.6f" % ((x1 + x2) / 2 / w, (y1 + y2) / 2 / h, bw, bh))
        has_ball = 1
        report["with_ball"] += 1

    if turf_frac < 0.20 or kept < 4 or kept > 16:
        report["skipped_frames"] = report.get("skipped_frames", 0) + 1
        continue

    with open(os.path.join(a.out, os.path.basename(fp).replace(".jpg", ".txt")), "w") as f:
        f.write("\n".join(lines))

    report["frames"] += 1
    report["players_total"] += kept
    report["per_frame"].append({"f": os.path.basename(fp), "players": kept, "ball": has_ball})

    if a.debug_dir and idx < 12:
        ov = img.copy()
        ov[tm == 0] = (ov[tm == 0] * 0.35).astype(np.uint8)
        for ln in lines:
            p = ln.split()
            cid = int(p[0]); cx, cy, bw2, bh2 = [float(v) for v in p[1:]]
            X1, Y1 = int((cx - bw2 / 2) * w), int((cy - bh2 / 2) * h)
            X2, Y2 = int((cx + bw2 / 2) * w), int((cy + bh2 / 2) * h)
            col = (0, 255, 0) if cid == 0 else (0, 215, 255)
            cv2.rectangle(ov, (X1, Y1), (X2, Y2), col, 2 if cid == 0 else 3)
        cv2.imwrite(os.path.join(a.debug_dir, os.path.basename(fp)), ov)

    if idx % 25 == 0:
        print("frame %d/%d players=%d ball=%d" % (idx, len(files), kept, has_ball), flush=True)

json.dump(report, open(os.path.join(a.out, "..", "autolabel_report.json"), "w"))
print("DONE kept_frames=%d skipped=%d players=%d with_ball=%d (%.1f%%) dropped_offpitch=%d" % (
    report["frames"], report.get("skipped_frames", 0), report["players_total"], report["with_ball"],
    100 * report["with_ball"] / max(report["frames"], 1), report["dropped_offpitch"]))
