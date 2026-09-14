"""Detection + tracking + teams + jersey-number OCR, voted per track, rendered."""
import argparse, json, os, sys, time
from collections import defaultdict, Counter
import numpy as np, cv2, supervision as sv, easyocr
from ultralytics import YOLO

sys.path.insert(0, os.path.expanduser("~/football-ai/sports"))
from sports.common.team import TeamClassifier

PLAYER, BALL = 0, 1

ap = argparse.ArgumentParser()
ap.add_argument("--weights", required=True)
ap.add_argument("--source", required=True)
ap.add_argument("--target", required=True)
ap.add_argument("--stats", required=True)
ap.add_argument("--device", default="mps")
ap.add_argument("--imgsz", type=int, default=1280)
ap.add_argument("--ocr-stride", type=int, default=3)
ap.add_argument("--ocr-min-h", type=int, default=100, help="below this nothing reads anyway")
a = ap.parse_args()

model = YOLO(a.weights)
info = sv.VideoInfo.from_video_path(a.source)
reader = easyocr.Reader(["en"], gpu=False)

# pass 1: team clusterer
crops = []
for frame in sv.get_video_frames_generator(a.source, stride=20):
    d = sv.Detections.from_ultralytics(
        model.predict(frame, device=a.device, verbose=False, conf=0.35, imgsz=a.imgsz)[0])
    crops += [sv.crop_image(frame, x) for x in d[d.class_id == PLAYER].xyxy]
    if len(crops) >= 600:
        break
clf = TeamClassifier(device=a.device, batch_size=16)
clf.fit(crops)
print("teams fitted", flush=True)

def read_number(frame, box):
    x1, y1, x2, y2 = box
    h = y2 - y1
    ty1, ty2 = int(y1 + 0.18 * h), int(y1 + 0.55 * h)
    c = frame[max(ty1, 0):ty2, max(int(x1), 0):int(x2)]
    if c.size == 0:
        return None
    c = cv2.resize(c, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    c = cv2.cvtColor(cv2.cvtColor(c, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    try:
        res = reader.readtext(c, allowlist="0123456789", detail=1,
                              text_threshold=0.6, low_text=0.3)
    except Exception:
        return None
    for _, txt, conf in res:
        t = txt.strip()
        if t.isdigit() and 1 <= len(t) <= 2 and conf >= 0.5:
            return t, float(conf)
    return None

# pass 2: track, vote team and number per track
tracker = sv.ByteTrack(minimum_consecutive_frames=3)
tracker.reset()
smoother = sv.DetectionsSmoother()
tvotes, nvotes = defaultdict(list), defaultdict(list)
kept = []
t0 = time.time()

for i, frame in enumerate(sv.get_video_frames_generator(a.source)):
    det = sv.Detections.from_ultralytics(
        model.predict(frame, device=a.device, verbose=False, conf=0.25, imgsz=a.imgsz)[0])
    people = det[det.class_id == PLAYER].with_nms(threshold=0.5, class_agnostic=True)
    people = smoother.update_with_detections(tracker.update_with_detections(people))
    ball = det[det.class_id == BALL]
    if len(ball):
        k = int(np.argmax(ball.confidence))
        ball = ball[k:k + 1]

    if len(people) and people.tracker_id is not None:
        teams = clf.predict([sv.crop_image(frame, x) for x in people.xyxy]).astype(int)
        for box, tid, tm in zip(people.xyxy, people.tracker_id, teams):
            tvotes[int(tid)].append(int(tm))
            if i % a.ocr_stride == 0 and (box[3] - box[1]) >= a.ocr_min_h:
                r = read_number(frame, box)
                if r:
                    nvotes[int(tid)].append(r)
    kept.append((people, ball))
    if i % 100 == 0:
        print("frame %d %.2f fps, numbers so far %d" %
              (i, (i + 1) / max(time.time() - t0, .01),
               sum(len(v) for v in nvotes.values())), flush=True)

team_of = {t: Counter(v).most_common(1)[0][0] for t, v in tvotes.items()}
# a track earns a number only on repeated agreeing reads
num_of = {}
for t, v in nvotes.items():
    c = Counter(n for n, _ in v)
    n, k = c.most_common(1)[0]
    if k >= 2:
        num_of[t] = n
print("tracks with a number:", len(num_of), "of", len(team_of), flush=True)

# pass 3: render
PAL = sv.ColorPalette.from_hex(['#FF4D4D', '#3BA3FF', '#BBBBBB'])
ell = sv.EllipseAnnotator(color=PAL, thickness=3)
lab = sv.LabelAnnotator(color=PAL, text_color=sv.Color.from_hex('#000000'),
                        text_position=sv.Position.BOTTOM_CENTER, text_scale=0.7)
tri = sv.TriangleAnnotator(color=sv.Color.from_hex('#FFD700'), base=22, height=18,
                           outline_thickness=1)
with sv.VideoSink(a.target, info) as sink:
    for (people, ball), frame in zip(kept, sv.get_video_frames_generator(a.source)):
        out = frame.copy()
        if len(people) and people.tracker_id is not None:
            people.class_id = np.array([team_of.get(int(t), 2) for t in people.tracker_id])
            labels = [("N" + num_of[int(t)]) if int(t) in num_of else ("#%d" % int(t))
                      for t in people.tracker_id]
            out = lab.annotate(ell.annotate(out, people), people, labels=labels)
        if len(ball):
            b = ball
            b.xyxy = sv.pad_boxes(xyxy=b.xyxy, px=8)
            out = tri.annotate(out, b)
        sink.write_frame(out)

json.dump({"tracks": len(team_of), "with_number": len(num_of),
           "numbers": {str(k): v for k, v in num_of.items()},
           "reads": {str(k): [n for n, _ in v] for k, v in nvotes.items()}},
          open(a.stats, "w"))
print("DONE numbers:", num_of)
