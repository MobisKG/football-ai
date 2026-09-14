#!/bin/bash
# Нарезает кадры по всем матчам и отправляет их на Диск по одному матчу.
#
# Разметку делает арендованная видеокарта: на маке она шла 3,75 с на кадр,
# это шесть часов на семь матчей. Объём передачи от этого не меняется —
# кадры уезжают наверх в любом случае, меняется только кто считает разметку.
set -uo pipefail
export PATH=/opt/homebrew/bin:$PATH
cd ~/football-ai
REMOTE="${REMOTE:-gdrive}"
mkdir -p data parts

for v in videos/*.mp4; do
  id=$(basename "$v" .mp4)
  [ -f "parts/.sent_$id" ] && { echo "уже отправлен $id"; continue; }
  echo "=== $id ==="
  if [ ! -f "data/$id/.frames_ok" ]; then
    rm -rf "data/$id/frames"; mkdir -p "data/$id/frames"
    nice -n 10 ffmpeg -v error -i "$v" -vf "fps=1/4" -q:v 2 "data/$id/frames/f_%05d.jpg" -y || continue
    touch "data/$id/.frames_ok"
  fi
  n=$(ls "data/$id/frames" | wc -l | tr -d " ")
  echo "  кадров: $n"
  tar czf "parts/$id.tar.gz" "data/$id/frames" || continue
  echo "  архив: $(du -h "parts/$id.tar.gz" | cut -f1)"
  rclone copy --transfers 4 "parts/$id.tar.gz" "${REMOTE}:football-ai/" || continue
  rm -f "parts/$id.tar.gz"
  touch "parts/.sent_$id"
  echo "  SENT $id"
done
echo "FRAMESDONE"
rclone ls "${REMOTE}:football-ai/"
