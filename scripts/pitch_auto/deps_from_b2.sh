#!/bin/bash
# Файлы для арендованной машины из B2 (deps/): только то, что неоткуда быстро и надёжно скачать —
# наши веса и OSNet (Google Drive через gdown срывается). Qwen, SigLIP и ffmpeg качаются сами
# с HuggingFace и GitHub так же быстро, их в B2 не держим (удалены 16.09).
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
echo DEPS_OK
