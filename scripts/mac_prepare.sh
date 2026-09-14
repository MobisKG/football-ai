#!/bin/bash
# Runs on the Mac mini. Turns downloaded matches into a packed training dataset.
#
# This is deliberately the slow, free half of the pipeline: frame extraction and
# auto-labelling happen here, off the rented clock. Only the packed dataset, a
# few times smaller than the video, ever travels to the GPU machine.
set -euo pipefail
export PATH=/opt/homebrew/bin:$PATH
cd ~/football-ai
PY=./.venv/bin/python
SEED_W=runs/all_football_s/weights/best.pt     # in-domain ball proposer
mkdir -p data

for v in videos/*.mp4; do
  id=$(basename "$v" .mp4)
  echo "=== $id ==="
  if [ ! -d "data/$id/frames" ]; then
    mkdir -p "data/$id/frames"
    nice -n 10 ffmpeg -v error -i "$v" -vf "fps=1/4" -q:v 2 "data/$id/frames/f_%05d.jpg" -y
  fi
  echo "  кадров: $(ls "data/$id/frames" | wc -l | tr -d ' ')"
  if [ ! -d "data/$id/labels" ]; then
    $PY autolabel.py --frames "data/$id/frames" --out "data/$id/labels" \
        --device mps --slice 512 --extra-ball-weights "$SEED_W"
  fi
  echo "  размечено: $(ls "data/$id/labels" | wc -l | tr -d ' ')"
done

echo "=== упаковка ==="
tar czf football-dataset.tar.gz data/*/frames data/*/labels
ls -lh football-dataset.tar.gz
echo
echo "Дальше:  ./upload_to_gdrive.sh football-dataset.tar.gz"
