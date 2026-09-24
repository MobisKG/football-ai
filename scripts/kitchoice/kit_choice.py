"""Команда трека выбором из двух форм, заданных человеком (для команд в похожей форме).

Свободное описание формы («синий и белый») путает голубую и сиреневую, тёмно-синюю
и синюю. Здесь модель выбирает букву: A — форма хозяев, B — форма гостей,
C — не игрок этих команд (судья, вратарь в другой форме, человек в манишке, зритель).

    python kit_choice.py crops_dir out.json "<форма A>" "<форма B>" [--batch 16]
Итог: {трек: {"A": n, "B": n, "C": n}} по снимкам трека.
"""
import json, os.path as osp, re, sys, time
import torch
from PIL import Image
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration
crops_dir, out_path, KIT_A, KIT_B = sys.argv[1:5]
BATCH = int(sys.argv[sys.argv.index("--batch") + 1]) if "--batch" in sys.argv else 16
mid = "Qwen/Qwen2.5-VL-7B-Instruct"
model = Qwen2_5_VLForConditionalGeneration.from_pretrained(mid, torch_dtype=torch.bfloat16, device_map="auto")
proc = AutoProcessor.from_pretrained(mid); proc.tokenizer.padding_side = "left"
PROMPT = ("Фото с любительского футбольного матча. Смотри только на человека в центре кадра, самого крупного. "
          "В какой он форме?\nA — %s\nB — %s\nC — другое: судья, вратарь в отличающейся форме, человек в манишке поверх формы, зритель\n"
          "Ответь одной буквой: A, B или C." % (KIT_A, KIT_B))
tracks = json.load(open(osp.join(crops_dir, "tracks.json")))
queue = [(t, s) for t, m in tracks.items() for s in m["shots"] if osp.exists(osp.join(crops_dir, s))]
res = {}; t0 = time.time()
msgs = [{"role": "user", "content": [{"type": "image", "image": None}, {"type": "text", "text": PROMPT}]}]
text = proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
for b in range(0, len(queue), BATCH):
    part = queue[b:b + BATCH]
    imgs = [Image.open(osp.join(crops_dir, s)).convert("RGB") for _, s in part]
    inp = proc(text=[text] * len(imgs), images=imgs, padding=True, return_tensors="pt").to(model.device)
    with torch.no_grad(): out = model.generate(**inp, max_new_tokens=3, do_sample=False)
    for (t, s), a in zip(part, proc.batch_decode(out[:, inp.input_ids.shape[1]:], skip_special_tokens=True)):
        m = re.search(r"[ABCАВС]", a.upper()); k = {"А": "A", "В": "B", "С": "C"}.get(m.group(0), m.group(0)) if m else "C"
        res.setdefault(t, {"A": 0, "B": 0, "C": 0})[k] += 1
    if (b // BATCH) % 20 == 0:
        el = time.time() - t0; print("%d/%d снимков, осталось ~%.0f мин" % (b + len(part), len(queue), el / (b + len(part)) * (len(queue) - b - len(part)) / 60), flush=True)
json.dump(res, open(out_path, "w"))
print("готово:", len(res), "треков")
