"""Full-match analysis: detect, track, assign teams, dump positions for stats.

Built for long video:
  * processes every Nth frame instead of all 60 per second
  * classifies a team once per track from a handful of crops, not every frame
  * tracks with BoT-SORT global motion compensation, because the camera pans
  * renders no video
"""
import argparse, json, os, sys, time
from collections import defaultdict, Counter
import numpy as np, cv2, supervision as sv
from ultralytics import YOLO

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "sports"))
from sports.common.team import TeamClassifier

PLAYER, BALL = 0, 1

ap = argparse.ArgumentParser()
ap.add_argument("--weights", required=True)
ap.add_argument("--source", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--device", default="cuda")
ap.add_argument("--imgsz", type=int, default=1280)
ap.add_argument("--stride", type=int, default=2, help="process every Nth frame")
ap.add_argument("--team-sample", type=int, default=8, help="crops kept per track for team voting")
ap.add_argument("--fit-max", type=int, default=900)
ap.add_argument("--clusters", type=int, default=4,
                help="на сколько групп делить внешний вид; команды — две самые многочисленные на поле")
ap.add_argument("--min-motion", type=float, default=12.0,
                help="px/с: кто шевелится меньше, тот не игрок, а зритель или тренер")
ap.add_argument("--tracker", default=os.path.join(HERE, "botsort_football.yaml"))
a = ap.parse_args()

model = YOLO(a.weights)
info = sv.VideoInfo.from_video_path(a.source)
eff_fps = info.fps / a.stride

samples = defaultdict(list)      # track id -> small crops, capped at team_sample
frames = []
t0 = time.time()
n_seen = 0

for i, frame in enumerate(sv.get_video_frames_generator(a.source, stride=a.stride)):
    r = model.track(frame, persist=True, tracker=a.tracker, device=a.device,
                    conf=0.25, imgsz=a.imgsz, verbose=False)[0]
    det = sv.Detections.from_ultralytics(r)

    rec = {"ball": None, "players": []}
    ball = det[det.class_id == BALL]
    if len(ball):
        k = int(np.argmax(ball.confidence))
        x1, y1, x2, y2 = ball.xyxy[k]
        rec["ball"] = [round(float((x1 + x2) / 2), 1), round(float((y1 + y2) / 2), 1)]

    people = det[det.class_id == PLAYER]
    if len(people) and people.tracker_id is not None:
        for (x1, y1, x2, y2), tid in zip(people.xyxy, people.tracker_id):
            tid = int(tid)
            # Храним не только точку под ногами, но и рамку. Без неё вырезки
            # игроков строились по оценке роста, и в центр кадра попадал сосед:
            # вратарь синих в голубой куртке был описан моделью как «оранжевый
            # костюм» — она видела сидящего рядом человека, а не его.
            rec["players"].append([tid, round(float((x1 + x2) / 2), 1), round(float(y2), 1)])
            rec.setdefault("boxes", {})[str(tid)] = [round(float(x1), 1), round(float(y1), 1),
                                                     round(float(x2), 1), round(float(y2), 1)]
            if len(samples[tid]) < a.team_sample:
                # Берём только футболку, а не фигуру целиком. В полной вырезке
                # больше половины площади — газон, ноги и тень, они одинаковы у
                # обеих команд и забивают собой единственный полезный признак,
                # цвет формы. Верхние 45 % по высоте и средние 80 % по ширине.
                bh, bw = int(y2) - int(y1), int(x2) - int(x1)
                if bh > 8 and bw > 4:
                    ty1 = max(int(y1), 0)
                    ty2 = ty1 + max(1, int(bh * 0.45))
                    tx1 = max(int(x1) + int(bw * 0.10), 0)
                    tx2 = max(tx1 + 1, int(x2) - int(bw * 0.10))
                    c = frame[ty1:ty2, tx1:tx2]
                    if c.size:
                        samples[tid].append(cv2.resize(c, (96, 96)))
    frames.append(rec)
    n_seen += 1
    if n_seen % 500 == 0:
        el = time.time() - t0
        print("%d кадров, %.1f fps, %.0f%% готово" %
              (n_seen, n_seen / el, 100 * n_seen * a.stride / max(info.total_frames, 1)), flush=True)

print("детекция за %.0f с, треков %d" % (time.time() - t0, len(samples)), flush=True)

flat, owner = [], []
for tid, cs in samples.items():
    for c in cs:
        flat.append(c)
        owner.append(tid)

# --- кто вообще игрок -------------------------------------------------------
# На этой площадке тренеры, запасные и зрители стоят прямо на газоне у сетки и
# попадают в детекцию наравне с игроками. Их одежда случайная, и они разваливают
# деление по форме: в одном эпизоде «пас» был засчитан между мужчиной в чёрном
# у забора и игроком в бело-красном. Отсеиваем их по двум признакам.
track_xy = defaultdict(list)
for k, rec in enumerate(frames):
    for tid, x, y in rec["players"]:
        track_xy[tid].append((k, x, y))

def motion(tid):
    """Медианная скорость трека в пикселях в секунду."""
    pts = track_xy[tid]
    if len(pts) < 8:
        # по короткому треку скорость не измерить; отсеивать его как «стоячего»
        # нельзя, иначе выпадут все игроки, которых трекер потерял через секунду
        return float("inf")
    sp = []
    for (k1, x1, y1), (k2, x2, y2) in zip(pts, pts[1:]):
        dt = (k2 - k1) / eff_fps
        if dt > 0:
            sp.append((((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5) / dt)
    sp.sort()
    return sp[len(sp) // 2] if sp else 0.0

def turf_mask_of(frame):
    """Маска газона: цвет как в autolabel.py, плюс выпуклая оболочка."""
    h, w = frame.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(frame, (7, 7), 0), cv2.COLOR_BGR2LAB).astype(np.int16)
    seed = lab[int(h * 0.75):int(h * 0.98), int(w * 0.25):int(w * 0.75)].reshape(-1, 3)
    med = np.median(seed, axis=0)
    d = np.sqrt(((lab - med) ** 2).sum(axis=2))
    m = (d < 26).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((31, 31), np.uint8))
    n, lbl, st, _ = cv2.connectedComponentsWithStats(m, 8)
    if n > 1:
        k = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        m = (lbl == k).astype(np.uint8) * 255
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cnts:
        hull = cv2.convexHull(max(cnts, key=cv2.contourArea))
        m = np.zeros_like(m)
        cv2.fillConvexPoly(m, hull, 255)
    return m

cap = cv2.VideoCapture(a.source)
masks = []
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
for q in (0.15, 0.4, 0.65, 0.9):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * q))
    ok, fr = cap.read()
    if ok:
        masks.append(turf_mask_of(fr))
cap.release()
pitch = (np.mean(masks, axis=0) > 127).astype(np.uint8) if masks else None
Hh, Ww = (pitch.shape if pitch is not None else (info.height, info.width))

def on_pitch(tid):
    """Доля кадров, где ноги трека стоят на газоне."""
    if pitch is None:
        return 1.0
    pts = track_xy[tid]
    if not pts:
        return 0.0
    inside = sum(1 for _, x, y in pts
                 if 0 <= int(y) < Hh and 0 <= int(x) < Ww and pitch[int(y), int(x)])
    return inside / len(pts)

players = {t for t in samples
           if on_pitch(t) >= 0.5 and motion(t) >= a.min_motion}
print("треков всего %d, из них признаны игроками %d" % (len(samples), len(players)), flush=True)

# --- деление по форме -------------------------------------------------------
# Групп берём больше двух: на площадке есть форма двух команд, вратари в своей
# одежде и посторонние. Втискивание всего этого в две группы и давало деление
# «светлые против тёмных» вместо деления по командам.
import umap
from sklearn.cluster import KMeans

fit_idx = [k for k, t in enumerate(owner) if t in players]
if len(fit_idx) > a.fit_max:
    step = len(fit_idx) / a.fit_max
    fit_idx = [fit_idx[int(k * step)] for k in range(a.fit_max)]

clf = TeamClassifier(device=a.device, batch_size=64)
feats_fit = clf.extract_features([flat[k] for k in fit_idx])
reducer = umap.UMAP(n_components=3)
proj_fit = reducer.fit_transform(feats_fit)
km = KMeans(n_clusters=a.clusters, n_init=10).fit(proj_fit)

all_idx = [k for k, t in enumerate(owner) if t in players]
feats_all = clf.extract_features([flat[k] for k in all_idx])
lab_all = km.predict(reducer.transform(feats_all))

votes = defaultdict(list)
for k, p in zip(all_idx, lab_all):
    votes[owner[k]].append(int(p))
cluster_of = {t: Counter(v).most_common(1)[0][0] for t, v in votes.items()}

# сколько кадров каждая группа реально проводит на поле — по этому и выбираем
# две настоящие команды, а не по числу треков: треки дробятся неравномерно
weight = Counter()
for t, c in cluster_of.items():
    weight[c] += len(track_xy[t])
main = [c for c, _ in weight.most_common(2)]
print("группы по кадрам: %s, команды: %s" % (dict(weight), main), flush=True)

team_of = {t: (0 if c == main[0] else 1 if len(main) > 1 and c == main[1] else -1)
           for t, c in cluster_of.items()}

# --- как называть команды ---------------------------------------------------
# Раньше группы назывались «красные» и «синие» просто по номеру, и подпись могла
# оказаться перевёрнутой. Берём средний цвет футболок группы и подбираем имя.
PALETTE = [("белые", (235, 235, 235)), ("чёрные", (35, 35, 35)), ("серые", (128, 128, 128)),
           ("красные", (40, 40, 200)), ("синие", (200, 60, 40)), ("голубые", (230, 170, 90)),
           ("зелёные", (60, 170, 60)), ("жёлтые", (60, 210, 230)), ("оранжевые", (40, 140, 235))]

def name_of(team_id):
    cs = [flat[k] for k, t in enumerate(owner) if team_of.get(t, -1) == team_id]
    if not cs:
        return "команда %d" % team_id
    mean = np.mean([c.reshape(-1, 3).mean(axis=0) for c in cs], axis=0)
    return min(PALETTE, key=lambda p: sum((mean[i] - p[1][i]) ** 2 for i in range(3)))[0]

team_names = {0: name_of(0), 1: name_of(1)}
print("названия команд: %s" % team_names, flush=True)

stability = float(np.mean([Counter(v).most_common(1)[0][1] / len(v)
                           for v in votes.values()])) if votes else 0.0

json.dump({"fps": eff_fps, "frames": frames,
           "team_of": {str(k): v for k, v in team_of.items()},
           "team_names": team_names,
           "vote_stability": stability},
          open(a.out, "w"))
per_frame = Counter()
for rec in frames[::30]:
    for tid, _, _ in rec["players"]:
        per_frame[team_of.get(tid, -1)] += 1
nf = max(1, len(frames[::30]))
bal = {k: round(v / nf, 1) for k, v in sorted(per_frame.items())}
print("игроков на кадр по командам: %s  (должно быть примерно поровну)" % bal)
print("треков по командам: %s" % dict(Counter(team_of.values())))
print("DONE %d кадров, %d треков, стабильность %.3f, всего %.0f с"
      % (len(frames), len(team_of), stability, time.time() - t0))
