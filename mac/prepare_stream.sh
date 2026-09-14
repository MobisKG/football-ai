#!/bin/bash
# Готовит матчи по мере их скачивания и сразу отправляет на Диск.
#
# Смысл в том, чтобы не ждать конца всех загрузок: пока качается очередной
# матч, уже скачанные нарезаются, размечаются и уезжают на Google Drive
# отдельными архивами. К моменту, когда dl5.sh допишет ALLDONE, почти вся
# подготовка уже сделана. Загрузка упирается в сеть, разметка — в GPU, они
# друг другу не мешают.
#
# На Диск кладём по архиву на матч, а не один общий: там всего 5 ГБ, и
# частями безопаснее. Локальный архив удаляем сразу после отправки.
set -uo pipefail
export PATH=/opt/homebrew/bin:$PATH
cd ~/football-ai
PY=./.venv/bin/python
SEED_W=runs/all_football_s/weights/best.pt
REMOTE="${REMOTE:-gdrive}"
mkdir -p data parts

prepare_one() {
  local v="$1" id
  id=$(basename "$v" .mp4)
  [ -f "parts/.done_$id" ] && return 0
  echo "=== $id ==="
  if [ ! -f "data/$id/.frames_ok" ]; then
    rm -rf "data/$id/frames"; mkdir -p "data/$id/frames"
    nice -n 10 ffmpeg -v error -i "$v" -vf "fps=1/4" -q:v 2 "data/$id/frames/f_%05d.jpg" -y || return 1
    touch "data/$id/.frames_ok"
  fi
  echo "  кадров: $(ls "data/$id/frames" | wc -l | tr -d " ")"
  if [ ! -f "data/$id/.labels_ok" ]; then
    rm -rf "data/$id/labels"
    nice -n 10 $PY autolabel.py --frames "data/$id/frames" --out "data/$id/labels" \
        --device mps --slice 512 --extra-ball-weights "$SEED_W" || return 1
    touch "data/$id/.labels_ok"
  fi
  echo "  размечено: $(ls "data/$id/labels" | wc -l | tr -d " ")"
  tar czf "parts/$id.tar.gz" "data/$id/frames" "data/$id/labels" || return 1
  echo "  архив: $(du -h "parts/$id.tar.gz" | cut -f1)"
  rclone copy --transfers 4 "parts/$id.tar.gz" "${REMOTE}:football-ai/" || return 1
  rm -f "parts/$id.tar.gz"
  touch "parts/.done_$id"
  echo "  УЕХАЛ НА ДИСК $id"
}

# Матчи с рабочего стола можно готовить прямо сейчас, они уже скачаны.
[ -f videos/RtFMFQXVwOU.mp4 ] || cp ~/Desktop/*RtFMFQXVwOU*.mp4 videos/RtFMFQXVwOU.mp4
[ -f videos/qE8fVwIRQxA.mp4 ] || cp ~/Desktop/*qE8fVwIRQxA*.mp4 videos/qE8fVwIRQxA.mp4

while true; do
  for v in videos/*.mp4; do
    [ -e "$v" ] || continue
    prepare_one "$v"
  done
  if grep -q ALLDONE dl5.log 2>/dev/null; then
    pending=0
    for v in videos/*.mp4; do
      id=$(basename "$v" .mp4)
      [ -f "parts/.done_$id" ] || pending=1
    done
    [ $pending -eq 0 ] && break
  fi
  sleep 60
done
echo "PREPDONE"
rclone ls "${REMOTE}:football-ai/"
