"""Кадр и вид сверху рядом: проверка перевода в метры глазами.

    python minimap.py видео.mp4 pos_metres.json pos_pixels.json out_dir 30 170 229.5
"""
import json, sys, os
import numpy as np, cv2

video, m_path, px_path, out_dir = sys.argv[1:5]; times = [float(t) for t in sys.argv[5:]]
os.makedirs(out_dir, exist_ok=True)
M = json.load(open(m_path)); PXP = json.load(open(px_path)); PP = json.load(open("/Users/doni/Documents/sport/football/docs/pitch.json"))
W_P, L_P = M["plan"]["width"], M["plan"]["length"]; BW, BD, HALF = PP["penalty_area"]["width"], PP["penalty_area"]["depth"], PP["half_length"]
team = {int(k): v for k, v in M["team_of"].items()}
num = {int(k): (v or {}).get("number") for k, v in (M.get("numbers") or {}).items()}
COL = {0: (219, 126, 28), 1: (49, 49, 224), -1: (160, 160, 160)}
cap = cv2.VideoCapture(video); FPS = cap.get(cv2.CAP_PROP_FPS)
S = 24; PAD = 30
MW, MH = int(L_P * S) + 2 * PAD, int(W_P * S) + 2 * PAD
P2 = lambda X, Y: (int(PAD + Y * S), int(PAD + (W_P - X) * S))   # поле лёжа: ворота y=0 слева

def pitch_canvas():
    c = np.full((MH, MW, 3), (60, 120, 60), np.uint8)
    L = lambda a, b: cv2.line(c, P2(*a), P2(*b), (255, 255, 255), 2, cv2.LINE_AA)
    L((0, 0), (W_P, 0)); L((W_P, 0), (W_P, L_P)); L((W_P, L_P), (0, L_P)); L((0, L_P), (0, 0))
    L((0, HALF), (W_P, HALF))
    for yb, sg in ((0, 1), (L_P, -1)):
        a, b = W_P / 2 - BW / 2, W_P / 2 + BW / 2
        L((a, yb), (a, yb + sg * BD)); L((a, yb + sg * BD), (b, yb + sg * BD)); L((b, yb + sg * BD), (b, yb))
    cv2.circle(c, P2(W_P / 2, HALF), 3 * S, (255, 255, 255), 2, cv2.LINE_AA)
    return c

for t in times:
    k = int(round(t * M.get("fps", 30)))
    if k >= len(M["frames"]): continue
    fm, fp = M["frames"][k], PXP["frames"][k]
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * FPS))); ok, fr = cap.read()
    if not ok: continue
    mm = pitch_canvas()
    for (tid, X, Y, inside), (_, x, y) in zip(fm["players"], fp["players"]):
        col = COL.get(team.get(tid, -1), COL[-1])
        if not inside: col = (0, 0, 0)
        cv2.circle(fr, (int(x), int(y)), 16, col, 4)
        if inside:
            cv2.circle(mm, P2(X, Y), 9, col, -1); cv2.circle(mm, P2(X, Y), 9, (255, 255, 255), 1)
            if num.get(tid): cv2.putText(mm, str(num[tid]), (P2(X, Y)[0] + 10, P2(X, Y)[1] - 6), 0, 0.5, (255, 255, 255), 1)
    if fm["ball"]:
        cv2.circle(mm, P2(*fm["ball"]), 7, (0, 230, 255), -1)
        cv2.circle(fr, (int(fp["ball"][0]), int(fp["ball"][1])), 14, (0, 230, 255), 3)
    top = cv2.resize(fr, (MW, int(MW * fr.shape[0] / fr.shape[1])))
    cv2.putText(top, "t=%.1f c   чёрный круг = вне поля" % t if False else "t=%.1f s  black ring = off pitch" % t, (15, 40), 0, 1.0, (255, 255, 255), 3)
    cv2.imwrite(os.path.join(out_dir, "minimap_%05.1f.jpg" % t), np.vstack([top, mm]), [cv2.IMWRITE_JPEG_QUALITY, 88])
print("ok")
