"""Читает номера по одному снимку и голосует по треку.

Первая версия показывала модели три снимка сразу и просила один ответ: на
снимках лицом к камере номера нет, а модель всё равно называла цифру с
уверенностью 0,9 (синий «№2» на деле был 14, 8 и 6). Теперь каждый снимок
спрашиваем отдельно, прямо говорим «null, если цифр не видно», и номер трека
— это цифра, которую назвали хотя бы на двух снимках и чаще других.
Уверенность — доля согласных снимков среди тех, где номер прочитан.

    python read_numbers2.py crops_dir out.json [--model Qwen/Qwen2.5-VL-7B-Instruct]
"""
import json, os.path as osp, re, sys, time
from collections import Counter
import torch
from PIL import Image
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

crops_dir, out_path = sys.argv[1], sys.argv[2]
model_id = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "Qwen/Qwen2.5-VL-7B-Instruct"
limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0

meta = json.load(open(osp.join(crops_dir, "tracks.json")))
tracks = sorted(meta.items(), key=lambda kv: -kv[1]["frames_seen"])
if limit: tracks = tracks[:limit]
model = Qwen2_5_VLForConditionalGeneration.from_pretrained(model_id, torch_dtype=torch.bfloat16, device_map="auto")
proc = AutoProcessor.from_pretrained(model_id)

# Второй прогон: цвет формы спрашиваем на каждом снимке и голос за номер
# засчитываем только если цвет совпал с формой самого трека. Иначе номер
# читается с соседа на заднем плане: у красного в оранжевой манишке «7»
# пришла с синего игрока позади.
PROMPT = ("Фото футболиста-любителя: нужен человек в центре кадра, самый крупный. "
          "Других людей на фото не учитывай. Виден ли номер на его футболке (обычно на спине)? "
          "Ответь СТРОГО в формате JSON: "
          '{"number": <номер цифрами, или null если цифры не видны или видны частично>, '
          '"kit": "<два цвета формы этого человека>"}')

def parse(ans):
    m = re.search(r"\{[^{}]*\}", ans, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except Exception:
        return {}

# Снимки идут в модель пачками по BATCH: по одному 0,8 с на снимок, и на
# 40-минутный матч (~12 тыс. снимков) уходило бы ~3 часа. Паддинг слева,
# иначе генерация продолжает не тот конец строки.
BATCH = int(sys.argv[sys.argv.index("--batch") + 1]) if "--batch" in sys.argv else 16
proc.tokenizer.padding_side = "left"
def ask_many(imgs):
    msgs = [{"role": "user", "content": [{"type": "image", "image": imgs[0]}, {"type": "text", "text": PROMPT}]}]
    text = proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    inputs = proc(text=[text] * len(imgs), images=imgs, padding=True, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=60, do_sample=False)
    return [parse(a) for a in proc.batch_decode(out[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)]

# все снимки всех треков одной очередью, ответы раскладываются обратно по трекам
queue = [(tid, s) for tid, m in tracks for s in m["shots"] if osp.exists(osp.join(crops_dir, s))]
answers = {}
t_start = time.time()
for b in range(0, len(queue), BATCH):
    part = queue[b:b + BATCH]
    # размер не трогаем: вырезки уже 300 px в высоту, Qwen2.5-VL принимает в
    # одной пачке снимки разной ширины, а уменьшение съедает цифры
    imgs = [Image.open(osp.join(crops_dir, s)).convert("RGB") for _, s in part]
    for (tid, s), r in zip(part, ask_many(imgs)):
        answers[s] = r
    done = b + len(part)
    if done % (BATCH * 20) < BATCH:
        el = time.time() - t_start
        print("%d/%d снимков, %.2f с на снимок, осталось ~%.0f мин" % (done, len(queue), el / done, el / done * (len(queue) - done) / 60), flush=True)
def ask(img_path):
    return answers.get(img_path, {})

res = {}; t0 = time.time(); nq = 0
for n, (tid, m) in enumerate(tracks):
    votes = Counter(); kits = Counter(); per = []
    for s in m["shots"]:
        p = osp.join(crops_dir, s)
        if not osp.exists(p): continue
        r = ask(s); nq += 1
        num = r.get("number")
        if isinstance(num, str) and num.strip().isdigit(): num = int(num.strip())
        kit = str(r.get("kit") or "").lower()
        if kit: kits[kit] += 1
        per.append({"shot": s, "number": num if isinstance(num, int) and 0 < num < 100 else None, "kit": kit})
    # форма трека — первое цветовое слово, встретившееся чаще всего
    first = Counter(p["kit"].split()[0] for p in per if p["kit"])
    main = first.most_common(1)[0][0] if first else None
    dropped = 0
    for p in per:
        if p["number"] is None: continue
        w = p["kit"].split()[0] if p["kit"] else None
        if main and w and w != main:
            p["dropped"] = True; dropped += 1; continue
        votes[p["number"]] += 1
    number, conf = None, 0.0
    if votes:
        best, c = votes.most_common(1)[0]
        if c >= 2: number, conf = best, round(c / sum(votes.values()), 2)
    res[tid] = {"number": number, "confidence": conf, "votes": dict(votes),
                "kit": kits.most_common(1)[0][0] if kits else None, "role": "игрок", "dropped": dropped,
                "team_cluster": m["team"], "frames_seen": m["frames_seen"], "per_shot": per}
    if (n + 1) % 10 == 0:
        print("%d треков, %d запросов, %.1f с на запрос" % (n + 1, nq, (time.time() - t0) / nq), flush=True)
        json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)
json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)
got = sum(1 for v in res.values() if v["number"])
print("готово: %d треков, номер у %d" % (len(res), got))
