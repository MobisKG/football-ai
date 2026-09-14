"""Fine-tuned detector + ByteTrack + SigLIP/KMeans team assignment, voted per track."""
import argparse, json, os, time, sys
from collections import defaultdict, Counter
import numpy as np, supervision as sv
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
ap.add_argument("--fit-stride", type=int, default=20, help="frame stride for gathering fit crops")
ap.add_argument("--fit-max", type=int, default=600, help="max crops used to fit the clusterer")
a = ap.parse_args()

model = YOLO(a.weights)
info = sv.VideoInfo.from_video_path(a.source)


def crops_of(frame, det):
    return [sv.crop_image(frame, xyxy) for xyxy in det.xyxy]


# ---- pass 1: gather crops and fit the two-team clusterer -------------------
print("pass 1: gathering crops", flush=True)
fit_crops = []
for i, frame in enumerate(sv.get_video_frames_generator(a.source, stride=a.fit_stride)):
    det = sv.Detections.from_ultralytics(
        model.predict(frame, device=a.device, verbose=False, conf=0.35, imgsz=a.imgsz)[0])
    fit_crops += crops_of(frame, det[det.class_id == PLAYER])
    if len(fit_crops) >= a.fit_max:
        break
print("crops for fit:", len(fit_crops), flush=True)

clf = TeamClassifier(device=a.device, batch_size=16)
clf.fit(fit_crops)
print("clusterer fitted", flush=True)

# ---- pass 2: track, predict team per detection, vote per track ------------
print("pass 2: tracking", flush=True)
tracker = sv.ByteTrack(minimum_consecutive_frames=3)
tracker.reset()
smoother = sv.DetectionsSmoother()

frames_data = []      # (people_detections, ball_detections) kept lightweight
votes = defaultdict(list)
stats = {"frames": 0, "ball_frames": 0, "players_per_frame": [], "ball_xy": [], "ball_conf": []}
t0 = time.time()

for i, frame in enumerate(sv.get_video_frames_generator(a.source)):
    det = sv.Detections.from_ultralytics(
        model.predict(frame, device=a.device, verbose=False, conf=0.25, imgsz=a.imgsz)[0])

    people = det[det.class_id == PLAYER].with_nms(threshold=0.5, class_agnostic=True)
    people = smoother.update_with_detections(tracker.update_with_detections(people))

    team = np.full(len(people), -1, dtype=int)
    if len(people):
        team = clf.predict(crops_of(frame, people)).astype(int)
        if people.tracker_id is not None:
            for tid, tm in zip(people.tracker_id, team):
                votes[int(tid)].append(int(tm))

    ball = det[det.class_id == BALL]
    if len(ball):
        k = int(np.argmax(ball.confidence))
        ball = ball[k:k + 1]
        x1, y1, x2, y2 = ball.xyxy[0]
        stats["ball_frames"] += 1
        stats["ball_xy"].append([round(float((x1 + x2) / 2), 1), round(float((y1 + y2) / 2), 1)])
        stats["ball_conf"].append(round(float(ball.confidence[0]), 3))
    else:
        stats["ball_xy"].append(None)
        stats["ball_conf"].append(None)

    stats["frames"] += 1
    stats["players_per_frame"].append(int(len(people)))
    frames_data.append((people, ball, team))

    if i % 100 == 0:
        el = time.time() - t0
        print("frame %d %.0fs %.2f fps" % (i, el, (i + 1) / max(el, .01)), flush=True)

# majority vote: one team per track for the whole clip
final = {tid: Counter(v).most_common(1)[0][0] for tid, v in votes.items()}
flip = sum(1 for tid, v in votes.items() if len(set(v)) > 1)
stats["tracks"] = len(final)
stats["tracks_with_disagreement"] = flip
stats["team_sizes"] = dict(Counter(final.values()))
stats["vote_stability"] = round(
    float(np.mean([Counter(v).most_common(1)[0][1] / len(v) for v in votes.values()])), 3) if votes else 0.0

# ---- pass 3: render -------------------------------------------------------
print("pass 3: rendering", flush=True)
PAL = sv.ColorPalette.from_hex(['#FF4D4D', '#3BA3FF', '#BBBBBB'])
ell = sv.EllipseAnnotator(color=PAL, thickness=3)
lab = sv.LabelAnnotator(color=PAL, text_color=sv.Color.from_hex('#000000'),
                        text_position=sv.Position.BOTTOM_CENTER)
tri = sv.TriangleAnnotator(color=sv.Color.from_hex('#FFD700'), base=22, height=18, outline_thickness=1)

with sv.VideoSink(a.target, info) as sink:
    for (people, ball, _), frame in zip(frames_data, sv.get_video_frames_generator(a.source)):
        out = frame.copy()
        if len(people) and people.tracker_id is not None:
            cid = np.array([final.get(int(t), 2) for t in people.tracker_id])
            people.class_id = cid
            out = ell.annotate(out, people)
            out = lab.annotate(out, people,
                               labels=[f"#{t}" for t in people.tracker_id])
        if len(ball):
            b = ball
            b.xyxy = sv.pad_boxes(xyxy=b.xyxy, px=8)
            out = tri.annotate(out, b)
        sink.write_frame(out)

stats["elapsed_sec"] = round(time.time() - t0, 1)
json.dump(stats, open(a.stats, "w"))
print("DONE tracks=%d teams=%s vote_stability=%.3f disagreeing_tracks=%d" % (
    stats["tracks"], stats["team_sizes"], stats["vote_stability"], flip))
