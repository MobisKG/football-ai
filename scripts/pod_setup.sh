#!/bin/bash
# Установка окружения на арендованной машине RunPod для пакетного прогона.
# Образ runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04, torch 2.4.1.
# Без apt (на community-хостах он висит), всё через pip и статический ffmpeg.
# Повторный запуск безопасен: готовые шаги пропускаются.
set -e
cd "$(dirname "$0")"
export YOLO_CONFIG_DIR=/root/.yolo; mkdir -p "$YOLO_CONFIG_DIR"
PIP="pip install -q --no-input --root-user-action=ignore"

echo ">>> ffmpeg"
if ! command -v ffmpeg >/dev/null; then
  curl -sL -o /tmp/ff.tar.xz https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz
  mkdir -p /tmp/ff && tar xJf /tmp/ff.tar.xz -C /tmp/ff --strip-components=1
  install -m755 /tmp/ff/bin/ffmpeg /tmp/ff/bin/ffprobe /usr/local/bin/
fi
ffmpeg -version | head -1

echo ">>> python-пакеты"
# transformers новее 4.49 требуют torch >= 2.5 и молча отключают модели; закрепляем
$PIP ultralytics supervision opencv-python-headless umap-learn scikit-learn \
     more-itertools tqdm yt-dlp gdown pyyaml sentencepiece protobuf accelerate qwen-vl-utils \
     Cython scipy tensorboard yacs h5py imageio future six 2>&1 | grep -v -iE 'notice|warning' || true
$PIP 'transformers==4.49.0' 2>&1 | grep -v -iE 'notice|warning' || true

echo ">>> roboflow/sports и torchreid (из клона, через PYTHONPATH)"
[ -d sports ] || git clone -q https://github.com/roboflow/sports.git
[ -d /root/deep-person-reid ] || git clone -q https://github.com/KaiyangZhou/deep-person-reid.git /root/deep-person-reid

echo ">>> веса OSNet"
[ -s osnet_x1_0_msmt17.pt ] || python -m gdown 112EMUfBPYeYg70w-syK6V6Mx8-Qb9Q1M -O osnet_x1_0_msmt17.pt -q
ls -la osnet_x1_0_msmt17.pt

echo ">>> модель Qwen2.5-VL-7B (кэш HF), чтобы первое видео не ждало"
python - <<'PY'
from huggingface_hub import snapshot_download
p = snapshot_download("Qwen/Qwen2.5-VL-7B-Instruct")
print("qwen:", p)
# SigLIP классификатора команд (roboflow/sports) — тоже заранее: разбор идёт без сети
print("siglip:", snapshot_download("google/siglip-base-patch16-224"))
PY

echo ">>> проверка"
PYTHONPATH=/root/deep-person-reid python - <<'PY'
import torch, ultralytics, supervision, transformers, torchreid
print("torch", torch.__version__, "| cuda:", torch.cuda.is_available(), "|", torch.cuda.get_device_name(0))
print("transformers", transformers.__version__, "| torchreid ok")
PY
ls -lh weights/best_7matches.pt
echo "SETUP_OK"
