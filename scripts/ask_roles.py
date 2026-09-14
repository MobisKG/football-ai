"""Переспрашивает модель про роль и одежду ЦЕНТРАЛЬНОГО человека на вырезке.

Первый проход спрашивал «кто на фотографиях», и модель отвечала про любого, кто
попал в кадр. На вырезке вратаря синих рядом сидел человек в оранжевом, и форма
была названа «красный зелёный»: вратарь ушёл в красную команду, а в ленте
появился «пас красных номеру 9», хотя девятка — вратарь соперника.

Здесь вопрос задан жёстко про человека в центре вырезки и прямо про вратаря:
он одет иначе всех, стоит у ворот, может быть в перчатках и в куртке.

    python ask_roles.py crops_dir out.json [--limit N]
"""
import json, os.path as osp, re, sys, time
import torch
from PIL import Image
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

crops_dir, out_path = sys.argv[1], sys.argv[2]
limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
model_id = "Qwen/Qwen2.5-VL-7B-Instruct"

meta = json.load(open(osp.join(crops_dir, "tracks.json")))
tracks = sorted(meta.items(), key=lambda kv: -kv[1].get("frames_seen", 0))
if limit:
    tracks = tracks[:limit]
print("треков: %d" % len(tracks), flush=True)

model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
    model_id, torch_dtype=torch.bfloat16, device_map="auto")
proc = AutoProcessor.from_pretrained(model_id)

PROMPT = (
    "Это кадры любительского мини-футбола. Смотри ТОЛЬКО на человека в центре кадра, "
    "остальных игнорируй. Ответь одним JSON-объектом без пояснений:\n"
    '{"role": "<вратарь|полевой|судья|зритель>", '
    '"clothes": "<цвет одежды центрального человека, два слова>", '
    '"number": <номер на спине цифрами или null>, "confidence": <0..1>}\n'
    "Вратарь одет не так, как обе команды: часто куртка, спортивный костюм или "
    "яркая накидка, бывает в перчатках, держится у ворот. "
    "Зритель или запасной стоит или сидит у ограждения и не бежит."
)

res = {}
t0 = time.time()
for n, (tid, m) in enumerate(tracks):
    paths = [osp.join(crops_dir, s) for s in m.get("shots", []) if osp.exists(osp.join(crops_dir, s))]
    if not paths:
        continue
    imgs = [Image.open(p).convert("RGB") for p in paths]
    content = [{"type": "image", "image": im} for im in imgs] + [{"type": "text", "text": PROMPT}]
    text = proc.apply_chat_template([{"role": "user", "content": content}],
                                    tokenize=False, add_generation_prompt=True)
    inputs = proc(text=[text], images=imgs, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=220, do_sample=False)
    ans = proc.batch_decode(out[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0]
    ans = ans.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(ans)
    except Exception:
        mm = re.search(r"\{[^{}]*\}", ans, re.S)
        parsed = None
        if mm:
            try:
                parsed = json.loads(mm.group(0))
            except Exception:
                parsed = None
        if parsed is None:
            parsed = {"raw": ans}
    if isinstance(parsed, list):
        parsed = next((p for p in parsed if isinstance(p, dict)), {"raw": ans})
    if isinstance(parsed.get("number"), str) and parsed["number"].strip().isdigit():
        parsed["number"] = int(parsed["number"].strip())
    parsed["frames_seen"] = m.get("frames_seen", 0)
    res[tid] = parsed
    if (n + 1) % 25 == 0:
        print("%d треков, %.1f с на трек" % (n + 1, (time.time() - t0) / (n + 1)), flush=True)
        json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)

json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)
from collections import Counter
c = Counter(str(v.get("role")) for v in res.values())
print("готово: %d треков, роли: %s" % (len(res), dict(c)))
