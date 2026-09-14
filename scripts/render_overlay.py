"""Рисует размеченное видео: эллипсы, номера игроков, мяч и ленту событий.

Нейросети здесь нет и не нужно: команды, номера, траектория мяча и события уже
посчитаны и лежат в файлах. Раньше рендер шёл 1,5 кадра в секунду, потому что во
время отрисовки заново вызывался классификатор команд на каждом кадре. Теперь
это чистая обработка видео.

    python render_overlay.py --video клип.mp4 --pos позиции.json \
        --ball мяч.json --events события.json --out out.mp4 [--stride 2]
"""
import argparse, json, math, os.path as osp
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Кириллица. cv2.putText рисует только латиницу и выводит вместо русских букв
# знаки вопроса, поэтому весь текст кладём через PIL нормальным шрифтом.
FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

def load_font(size):
    for p in FONT_CANDIDATES:
        if osp.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

F_BIG, F_MID, F_SMALL = load_font(30), load_font(24), load_font(20)

def draw_text(img, items):
    """items: список (текст, x, y, цвет BGR, шрифт). Рисуем все надписи разом:
    перевод кадра в PIL и обратно стоит времени, поэтому за один проход."""
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    for text, x, y, col, font in items:
        d.text((x, y), text, font=font, fill=(col[2], col[1], col[0]))
    img[:, :, :] = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

ap = argparse.ArgumentParser()
ap.add_argument("--video", required=True)
ap.add_argument("--pos", required=True, help="позиции игроков, команды, номера")
ap.add_argument("--ball", required=True, help="траектория мяча из ball_track.py")
ap.add_argument("--events", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--stride", type=int, default=1, help="писать каждый N-й кадр исходника")
ap.add_argument("--limit-sec", type=float, default=0)
ap.add_argument("--metres", default="", help="позиции в метрах: показать скорость игроков")
ap.add_argument("--event-hold", type=float, default=2.5, help="сколько секунд держать надпись о событии")
a = ap.parse_args()

pos = json.load(open(a.pos))
ball = json.load(open(a.ball))
ev = json.load(open(a.events))["events"]
team = {int(k): v for k, v in pos["team_of"].items()}
NAMES = {int(k): v for k, v in (pos.get("team_names") or {}).items()}
NUM = {int(k): (v or {}).get("number") for k, v in (pos.get("numbers") or {}).items()}
GK = set(int(k) for k in (pos.get("goalkeepers") or {}))
PLAYER = {int(k): v for k, v in (pos.get("player_of") or {}).items()}   # трек -> игрок после склейки

# Скорость под игроком: позиции в метрах (to_metres.py), сглаживание 0,5 с,
# смещение за 0,5 с. Точки вне поля и скачки быстрее 10 м/с не показываются.
SPEED = {}
if a.metres:
    mt = json.load(open(a.metres)); mfps = mt["fps"]; Wm = int(0.5 * mfps)
    tracks = {}
    for k, f in enumerate(mt["frames"]):
        for tid, X, Y, inside in f["players"]:
            if inside:
                tracks.setdefault(int(tid), []).append((k, X, Y))
    ker = np.ones(Wm) / Wm
    for tid, pts in tracks.items():
        arr = np.array(pts, float)
        cuts = np.where(np.diff(arr[:, 0]) > 0.2 * mfps)[0] + 1
        for seg in np.split(arr, cuts):
            if len(seg) < 2 * Wm:
                continue
            sx = np.convolve(seg[:, 1], ker, "valid"); sy = np.convolve(seg[:, 2], ker, "valid")
            v = np.hypot(sx[Wm:] - sx[:-Wm], sy[Wm:] - sy[:-Wm]) / 0.5
            for j, vv in enumerate(v):
                if vv < 10:
                    # середина окна сглаживания и окна смещения
                    SPEED[(int(seg[j + Wm // 2 + Wm // 2, 0]), tid)] = vv * 3.6

COL = {0:(219, 126, 28), 1: (49, 49, 224), -1: (150, 150, 150)}   # BGR: синие, красные, прочие
name = lambda t: NAMES.get(t, "?")

cap = cv2.VideoCapture(a.video)
W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
SRC_FPS = cap.get(cv2.CAP_PROP_FPS)
N_SRC = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
pos_step = round(SRC_FPS / pos["fps"])          # позиции считались реже исходника
ball_step = round(SRC_FPS / ball["fps"])
out_fps = SRC_FPS / a.stride
vw = cv2.VideoWriter(a.out, cv2.VideoWriter_fourcc(*"mp4v"), out_fps, (W, H))

def panel(img, t, owner, poss):
    """Табло: время, у кого мяч сейчас и накопленное владение каждой команды."""
    w, h = 620, 140
    sub = img[0:h, 0:w]
    img[0:h, 0:w] = cv2.addWeighted(np.zeros_like(sub), 0.58, sub, 0.42, 0)
    mm, ss = divmod(t, 60)
    items = [("%02d:%04.1f" % (int(mm), ss), 20, 10, (210, 210, 210), F_MID)]
    tot = poss[0] + poss[1]
    if tot > 0:
        items.append(("владение:", 20, 44, (230, 230, 230), F_MID))
        items.append(("синие %d%%" % round(100 * poss[0] / tot), 150, 44, (255, 170, 80), F_MID))
        items.append(("красные %d%%" % round(100 * poss[1] / tot), 330, 44, (90, 90, 255), F_MID))
    if owner in (0, 1):
        items.append(("мяч у: %s" % ("синих" if owner == 0 else "красных"), 20, 84,
                      (255, 170, 80) if owner == 0 else (90, 90, 255), F_BIG))
    else:
        items.append(("мяч: ничей", 20, 84, (200, 200, 200), F_BIG))
    draw_text(img, items)

# Мини-схема поля внизу справа: длина поля по горизонтали, ближняя к камере
# бровка (x = 19) снизу. Игрок, которого сейчас не видно, остаётся бледной точкой
# на месте, где его видели последний раз (до GHOST секунд).
PL = 43.08; PW = 19.0; MS = 11            # пикселей на метр
MW, MH = int(PL * MS) + 20, int(PW * MS) + 20
GHOST = 15.0
last_seen = {}                             # игрок -> (X, Y, команда, номер, t)

def draw_minimap(img, k, t):
    x0, y0 = W - MW - 20, H - MH - 20
    p = lambda X, Y: (int(x0 + 10 + Y * MS), int(y0 + 10 + X * MS))
    cv2.rectangle(img, (x0, y0), (x0 + MW, y0 + MH), (50, 110, 60), -1)
    wc = (235, 235, 235)
    cv2.rectangle(img, p(0, 0), p(PW, PL), wc, 2)
    cv2.line(img, p(0, PL / 2), p(PW, PL / 2), wc, 2)
    cv2.circle(img, p(PW / 2, PL / 2), int(3 * MS), wc, 2)
    for yy, s in ((0, 1), (PL, -1)):     # штрафные 9 × 6,4
        cv2.rectangle(img, p(PW / 2 - 4.5, yy), p(PW / 2 + 4.5, yy + s * 6.4), wc, 2)
    if not a.metres:
        return
    f = mt["frames"][min(k, len(mt["frames"]) - 1)]
    now = set()
    for tid, X, Y, inside in f["players"]:
        tid = int(tid); tm = team.get(tid, -1)
        if not inside or tm not in (0, 1):
            continue
        key = PLAYER.get(tid) or ((tm, NUM[tid]) if NUM.get(tid) else ("t", tid))
        last_seen[key] = (X, Y, tm, NUM.get(tid), t); now.add(key)
    labels = []
    for key, (X, Y, tm, n, ts) in list(last_seen.items()):
        if t - ts > GHOST:
            del last_seen[key]; continue
        c = COL[tm]
        if key not in now:
            c = tuple(int(0.45 * v + 0.55 * 110) for v in c)   # бледнее: давно не видели
        cv2.circle(img, p(X, Y), 7, c, -1, cv2.LINE_AA)
        if n:
            q = p(X, Y); labels.append((str(n), q[0] + 8, q[1] - 22, (255, 255, 255), F_SMALL))
    b = f.get("ball")
    if b:
        cv2.circle(img, p(b[0], b[1]), 5, (0, 215, 255), -1, cv2.LINE_AA)
    if labels:
        draw_text(img, labels)

# Владение: мяч принадлежит команде ближайшего игрока, если он рядом (в пикселях,
# порог растёт к низу кадра, как эллипс). Когда мяч не виден или рядом никого,
# владение остаётся за последней командой — так принято в статистике матчей.
def nearest_team(players, b):
    best = None
    for tid, x, y in players:
        tm = team.get(int(tid), -1)
        if tm not in (0, 1):
            continue
        d = math.hypot(x - b[0], y - b[1])
        lim = 2.2 * (26 + 34 * (y / H) ** 2)
        if d < lim and (best is None or d < best[0]):
            best = (d, tm)
    return best[1] if best else None

def draw_player(img, tid, x, y, labels, k=0):
    t = team.get(tid, -1)
    # Зрители и запасные у бровки попадают в детекцию, но командой не помечены.
    # Рисовать их незачем: на проверке у забора висели номера №23 и №9.
    if t not in (0, 1) and tid not in GK:
        return
    col = COL.get(t, COL[-1])
    # размер эллипса растёт к низу кадра: дальше от камеры — мельче
    rx = int(26 + 34 * (y / H) ** 2)
    ry = max(6, int(rx * 0.38))
    cv2.ellipse(img, (int(x), int(y)), (rx, ry), 0, -30, 210, col, 3, cv2.LINE_AA)
    lab = ""
    if tid in GK:
        lab = "ВР"                      # вратарь
    elif NUM.get(tid):
        lab = "№%s" % NUM[tid]
    if lab:
        tw = 16 + 13 * len(lab)
        bx, by = int(x) - tw // 2, int(y) + ry + 4
        cv2.rectangle(img, (bx, by), (bx + tw, by + 28), col, -1)
        labels.append((lab, bx + 8, by + 2, (255, 255, 255), F_SMALL))
    sp = SPEED.get((k, tid))
    if sp is not None:
        s = "%d км/ч" % round(sp)
        sx_, sy_ = int(x) - 38, int(y) + ry + (36 if lab else 4)
        cv2.rectangle(img, (sx_, sy_), (sx_ + 84, sy_ + 26), (30, 30, 30), -1)
        labels.append((s, sx_ + 6, sy_ + 1, (255, 255, 255), F_SMALL))

i = 0
written = 0
owner = None; poss = [0.0, 0.0]; cand = None; cand_n = 0
while True:
    ok, frame = cap.read()
    if not ok:
        break
    if a.limit_sec and i / SRC_FPS > a.limit_sec:
        break
    # владение считаем на каждом кадре исходника, даже если он не пишется
    k = min(i // pos_step, len(pos["frames"]) - 1)
    kb = min(i // ball_step, len(ball["frames"]) - 1)
    if i % pos_step == 0:
        b = ball["frames"][kb]
        tm = nearest_team(pos["frames"][k]["players"], b) if b else None
        # смена владения только если новая команда рядом с мячом 0,3 с подряд
        if tm is not None and tm != owner:
            cand_n = cand_n + 1 if tm == cand else 1; cand = tm
            if cand_n >= int(0.3 * pos["fps"]):
                owner = tm; cand_n = 0
        else:
            cand_n = 0
        if owner in (0, 1):
            poss[owner] += 1 / pos["fps"]
    if i % a.stride == 0:
        t = i / SRC_FPS
        labels = []
        for tid, x, y in pos["frames"][k]["players"]:
            draw_player(frame, int(tid), x, y, labels, k)
        b = ball["frames"][kb]
        if b:
            cv2.circle(frame, (int(b[0]), int(b[1])), 13, (0, 215, 255), 3, cv2.LINE_AA)
        if labels:
            draw_text(frame, labels)
        panel(frame, t, owner, poss)
        draw_minimap(frame, k, t)
        vw.write(frame)
        written += 1
        if written % 600 == 0:
            print("%d кадров записано" % written, flush=True)
    i += 1
cap.release(); vw.release()
tot = sum(poss) or 1
print("владение: синие %.0f с (%.0f%%), красные %.0f с (%.0f%%)" % (poss[0], 100 * poss[0] / tot, poss[1], 100 * poss[1] / tot))
print("готово: %s, кадров %d, %.1f кадр/с в записи" % (a.out, written, out_fps))
