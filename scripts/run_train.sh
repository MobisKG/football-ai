#!/bin/bash
# Train on a dataset that is already present, then check it on a test clip.
#
# The dataset arrives from Google Drive, not from YouTube: datacenter IPs get
# "Sign in to confirm you're not a bot", and frames are far smaller than video.
#
#   ./fetch_gdrive.sh "<ссылка на архив>" football-dataset.tar.gz
#   EPOCHS=60 ./run_train.sh
set -euo pipefail
cd "$(dirname "$0")"
export YOLO_CONFIG_DIR=/root/.yolo; mkdir -p "$YOLO_CONFIG_DIR"
EPOCHS="${EPOCHS:-60}"
log() { echo "[$(date '+%F %T')] $*" | tee -a run_train.log; }

[ -d data ] || { echo "нет папки data/ — сначала ./fetch_gdrive.sh"; exit 1; }

log "=== 1/2 сборка датасета ==="
python build_ds_multi.py data 2>&1 | tee -a run_train.log

log "=== 2/2 обучение, $EPOCHS эпох ==="
python train_best.py --data data/yolo/data.yaml --epochs "$EPOCHS" --name football_final 2>&1 | tee -a run_train.log

W=runs/football_final/weights/best.pt
log "готово: $W"
if [ -f out/test.mp4 ]; then
  python analyze_match.py --weights "$W" --source out/test.mp4 --out out/test_pos.json --device cuda --stride 2
  python stats.py out/test_pos.json "тест" | tee out/test_stats.txt
fi
log "=== всё ==="
