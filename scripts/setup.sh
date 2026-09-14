#!/bin/bash
# Setup on a fresh rented CUDA machine.
#
# Deliberately avoids apt: on community hosts archive.ubuntu.com is often
# unreachable and `apt-get update` hangs for many minutes while the GPU is
# billed. Everything needed comes from pip and a static ffmpeg build instead.
set -e
cd "$(dirname "$0")"
export YOLO_CONFIG_DIR=/root/.yolo; mkdir -p "$YOLO_CONFIG_DIR"

echo ">>> ffmpeg (статическая сборка, без apt)"
if ! command -v ffmpeg >/dev/null; then
  curl -sL -o /tmp/ff.tar.xz \
    https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz
  mkdir -p /tmp/ff && tar xJf /tmp/ff.tar.xz -C /tmp/ff --strip-components=1
  install -m755 /tmp/ff/bin/ffmpeg /tmp/ff/bin/ffprobe /usr/local/bin/
fi
ffmpeg -version | head -1

echo ">>> python-пакеты"
pip install -q --no-input --root-user-action=ignore \
    ultralytics supervision transformers umap-learn scikit-learn \
    more-itertools tqdm yt-dlp gdown pyyaml

echo ">>> roboflow/sports"
[ -d sports ] || git clone -q https://github.com/roboflow/sports.git

echo ">>> проверка"
python - <<'PY'
import torch, ultralytics, supervision
print("torch", torch.__version__, "| cuda:", torch.cuda.is_available())
if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    print("GPU:", p.name, "| VRAM %.0f ГБ" % (p.total_memory/1024**3))
print("ultralytics", ultralytics.__version__, "| supervision", supervision.__version__)
PY
echo ">>> модель:"; ls -lh weights/*.pt 2>/dev/null || echo "  ВНИМАНИЕ: нет weights/best.pt"
