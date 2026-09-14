"""Читает номер на футболке по фотографиям трека визуально-языковой моделью.

Зачем не OCR: замер по проекту дал 2,7 % уверенных прочтений на этом матче,
потому что OCR запускали на каждом кадре подряд, включая дальнюю зону, где рост
игрока 60 пикселей. Здесь другой подход, тот же, что у победителей SoccerNet
2025: смотрим не каждый кадр, а несколько лучших кадров трека, и спрашиваем
модель сразу про номер, цвет формы и роль.

ТРЕБОВАНИЯ К ОКРУЖЕНИЮ. Свежие transformers (5.x) отказываются работать с
torch ниже 2.5: пишут «PyTorch was not found» и молча отключают модели. В образе
runpod/pytorch:2.4.0 стоит torch 2.4.1, поэтому либо ставить torch 2.5+, либо
закреплять transformers==4.49.0, где Qwen2.5-VL уже поддержан, а требование к
torch мягче. Проверено на прогоне 12 сентября.

    pip install "transformers==4.49.0" accelerate qwen-vl-utils      # при torch 2.4
    pip install --upgrade torch transformers accelerate qwen-vl-utils  # правильнее

    python read_numbers.py crops_dir out.json [--model Qwen/Qwen2.5-VL-7B-Instruct]
"""
import importlib.metadata as _md
import json, os, os.path as osp, re, sys, time
import torch
from PIL import Image
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

def _check_env():
    """Ругаемся сразу, а не после получаса загрузки модели."""
    tv = tuple(int(x) for x in torch.__version__.split("+")[0].split(".")[:2])
    try:
        trv = tuple(int(x) for x in _md.version("transformers").split(".")[:2])
    except Exception:
        trv = (0, 0)
    if trv >= (5, 0) and tv < (2, 5):
        raise SystemExit(
            "transformers %s требует torch >= 2.5, а стоит torch %s.\n"
            "Либо: pip install --upgrade torch\n"
            "Либо: pip install 'transformers==4.49.0'"
            % (".".join(map(str, trv)), torch.__version__))

crops_dir, out_path = sys.argv[1], sys.argv[2]
model_id = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "Qwen/Qwen2.5-VL-7B-Instruct"
limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0

_check_env()
meta = json.load(open(osp.join(crops_dir, "tracks.json")))
tracks = sorted(meta.items(), key=lambda kv: -kv[1]["frames_seen"])
if limit:
    tracks = tracks[:limit]
print("треков к разбору: %d" % len(tracks), flush=True)

model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
    model_id, torch_dtype=torch.bfloat16, device_map="auto")
proc = AutoProcessor.from_pretrained(model_id)

PROMPT = (
    "На фотографиях один и тот же футболист-любитель, снятый несколько раз. "
    "Ответь СТРОГО в формате JSON без пояснений: "
    '{"number": <номер на футболке цифрами или null, если не виден>, '
    '"confidence": <0..1>, "kit": "<цвет формы двумя словами>", '
    '"role": "<игрок|вратарь|судья|не игрок>"}'
)

res = {}
t0 = time.time()
for n, (tid, m) in enumerate(tracks):
    paths = [osp.join(crops_dir, s) for s in m["shots"] if osp.exists(osp.join(crops_dir, s))]
    if not paths:
        continue
    imgs = [Image.open(p).convert("RGB") for p in paths]
    content = [{"type": "image", "image": im} for im in imgs]
    content.append({"type": "text", "text": PROMPT})
    msgs = [{"role": "user", "content": content}]
    text = proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    inputs = proc(text=[text], images=imgs, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=220, do_sample=False)
    ans = proc.batch_decode(out[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0].strip()
    ans = ans.replace("```json", "").replace("```", "").strip()
    def _parse(t):
        try:
            return json.loads(t)
        except Exception:
            pass
        # Модель отвечает массивом по объекту на каждую фотографию, и при обрыве
        # по лимиту длины получается битый JSON. Вытаскиваем первый целый объект.
        m = re.search(r"\{[^{}]*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None
    parsed = _parse(ans)
    if parsed is None:
        parsed = {"raw": ans}
    # Модель иногда отвечает массивом по одному элементу на фотографию, хотя
    # просили один объект: берём первый разумный, остальное кладём в raw.
    if isinstance(parsed, list):
        first = next((p for p in parsed if isinstance(p, dict)), None)
        parsed = dict(first) if first else {"raw": ans}
    if not isinstance(parsed, dict):
        parsed = {"raw": ans}
    # номер может прийти строкой — приводим к числу, иначе он не попадёт в счёт
    if isinstance(parsed.get("number"), str) and parsed["number"].strip().isdigit():
        parsed["number"] = int(parsed["number"].strip())
    parsed["team_cluster"] = m["team"]
    parsed["frames_seen"] = m["frames_seen"]
    res[tid] = parsed
    if (n + 1) % 20 == 0:
        el = time.time() - t0
        print("%d треков, %.1f с на трек" % (n + 1, el / (n + 1)), flush=True)
        json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)

json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)
got = sum(1 for v in res.values() if isinstance(v.get("number"), int))
print("готово: %d треков, номер прочитан у %d (%.0f %%)" % (len(res), got, 100 * got / max(len(res), 1)))
