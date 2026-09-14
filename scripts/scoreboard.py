"""Голы по табло трансляции: счёт и время тайма с экранной плашки.

У записей SportCam в левом верхнем углу табло: названия команд, счёт, номер
тайма и часы. Удары и голы по самому мячу мы пока не находим, а табло
показывает счёт готовым. Читаем его раз в STEP секунд моделью Qwen2.5-VL
(пачками) и восстанавливаем, когда какой счёт появился.

Счёт в игре только растёт: одиночное чтение, которое уменьшает счёт или
прыгает сразу на несколько голов, — ошибка чтения. Гол засчитывается, когда
новый счёт подтверждён двумя чтениями подряд.

    python scoreboard.py видео.mp4 out.json [--step 5] [--box 0,0,320,220] [--batch 16]
Координаты box — в пикселях кадра 1920×1080.
"""
import json, re, sys, time
import cv2, torch
from PIL import Image
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

video, out_path = sys.argv[1], sys.argv[2]
opt = lambda k, d: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
STEP = float(opt("--step", 5)); BATCH = int(opt("--batch", 16))
X1, Y1, X2, Y2 = [int(v) for v in opt("--box", "0,0,320,220").split(",")]

cap = cv2.VideoCapture(video)
fps = cap.get(cv2.CAP_PROP_FPS); n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
sx, sy = cap.get(3) / 1920.0, cap.get(4) / 1080.0
shots = []
for sec in [i * STEP for i in range(int(n / fps / STEP) + 1)]:
    cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
    ok, f = cap.read()
    if not ok: break
    crop = f[int(Y1 * sy):int(Y2 * sy), int(X1 * sx):int(X2 * sx)]
    crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)   # мелкие цифры крупнее
    shots.append((sec, Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))))
cap.release()
print("кадров табло: %d (раз в %g с)" % (len(shots), STEP), flush=True)

model_id = "Qwen/Qwen2.5-VL-7B-Instruct"
model = Qwen2_5_VLForConditionalGeneration.from_pretrained(model_id, torch_dtype=torch.bfloat16, device_map="auto")
proc = AutoProcessor.from_pretrained(model_id); proc.tokenizer.padding_side = "left"
PROMPT = ("Это табло футбольной трансляции. Если табло на картинке нет, ответь {\"visible\": false}. "
          "Иначе ответь СТРОГО JSON: {\"visible\": true, \"team1\": \"<верхняя команда>\", \"team2\": \"<нижняя команда>\", "
          "\"score1\": <голы верхней>, \"score2\": <голы нижней>, \"half\": <номер тайма>, \"clock\": \"<мм:сс>\"}")

def parse(t):
    m = re.search(r"\{[^{}]*\}", t, re.S)
    try: return json.loads(m.group(0)) if m else {}
    except Exception: return {}

reads = []; t0 = time.time()
for b in range(0, len(shots), BATCH):
    part = shots[b:b + BATCH]
    msgs = [{"role": "user", "content": [{"type": "image", "image": part[0][1]}, {"type": "text", "text": PROMPT}]}]
    text = proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    inputs = proc(text=[text] * len(part), images=[im for _, im in part], padding=True, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=80, do_sample=False)
    for (sec, _), a in zip(part, proc.batch_decode(out[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)):
        r = parse(a); r["t"] = sec; reads.append(r)
print("прочитано за %.0f с" % (time.time() - t0), flush=True)

# --- счёт только растёт, новый счёт — после двух одинаковых чтений подряд --------
def score(r):
    try: return int(r["score1"]), int(r["score2"])
    except Exception: return None
cur = (0, 0); goals = []; pending = None
names = {}
for r in reads:
    if not r.get("visible"): continue
    for k in ("team1", "team2"):
        if r.get(k): names.setdefault(k, {}).setdefault(str(r[k]).strip(), 0); names[k][str(r[k]).strip()] += 1
    s = score(r)
    if not s or s == cur: pending = None; continue
    d1, d2 = s[0] - cur[0], s[1] - cur[1]
    if d1 < 0 or d2 < 0 or d1 + d2 > 2: pending = None; continue       # ошибка чтения
    if pending and pending[0] == s:
        for who, d in ((1, d1), (2, d2)):
            for _ in range(d):
                goals.append({"team": who, "video_t": pending[1], "half": pending[2].get("half"),
                              "clock": pending[2].get("clock"), "score_after": list(s)})
        cur = s; pending = None
    else:
        pending = (s, r["t"], r)
team_names = {k: max(v, key=v.get) for k, v in names.items()}
res = {"teams": [team_names.get("team1"), team_names.get("team2")], "final_score": list(cur),
       "goals": goals, "reads": reads, "step_s": STEP}
json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)
print("команды: %s, счёт %d:%d, голов %d" % (res["teams"], cur[0], cur[1], len(goals)))
for g in goals:
    print("  гол команды %d на %d:%02d видео (тайм %s, %s), счёт %s" % (g["team"], g["video_t"] // 60, g["video_t"] % 60, g["half"], g["clock"], g["score_after"]))
