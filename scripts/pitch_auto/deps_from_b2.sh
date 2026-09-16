#!/bin/bash
# Большие файлы для арендованной машины из B2 (deps/) вместо HuggingFace, Google Drive и GitHub.
# Нужны /root/.b2env (KEY_ID, APPLICATION_KEY) и b2.py рядом. Замер 16.09 на cpu3c: Qwen 16,6 ГБ
# из B2 за 23 с + распаковка 8 с; из HuggingFace — 22 с. Выигрыш не в скорости, а в надёжности:
# не зависим от gdown (OSNet), GitHub (ffmpeg) и доступности HF; наши веса не тянутся с ноутбука.
#   bash deps_from_b2.sh /root/fb
set -e
DST=${1:-/root/fb}; cd "$(dirname "$0")"
P=$(head -1 $(command -v pip3) | sed 's/^#!//'); $P -c "import b2sdk" 2>/dev/null || pip3 install -q --root-user-action=ignore b2sdk
get() { $P b2.py get "deps/$1" "$2" >/dev/null; }
mkdir -p "$DST/weights" /root/.cache/huggingface/hub /tmp/deps
for w in best_7matches.pt wasb_finetuned.pth.tar pitch_lines_v2.pt; do [ -s "$DST/weights/$w" ] || get "weights/$w" "$DST/weights/$w"; done
[ -s "$DST/osnet_x1_0_msmt17.pt" ] || get osnet_x1_0_msmt17.pt "$DST/osnet_x1_0_msmt17.pt"
if ! command -v ffmpeg >/dev/null; then
  get ffmpeg-linux64-gpl.tar.xz /tmp/deps/ff.tar.xz && mkdir -p /tmp/ff && tar xJf /tmp/deps/ff.tar.xz -C /tmp/ff --strip-components=1 \
    && install -m755 /tmp/ff/bin/ffmpeg /tmp/ff/bin/ffprobe /usr/local/bin/
fi
for m in qwen2.5-vl-7b siglip-base-patch16-224; do
  get "hf_$m.tar" /tmp/deps/m.tar && tar xf /tmp/deps/m.tar -C /root/.cache/huggingface/hub && rm -f /tmp/deps/m.tar
done
echo DEPS_OK
