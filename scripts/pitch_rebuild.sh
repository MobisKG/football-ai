#!/bin/bash
# Пересобирает привязку к плану поля с учётом ручных правок из редактора.
#
# Опоры: автоматические (ближние ворота, 2 с; дальние ворота, 285 с) плюс все
# кадры, сохранённые в редакторе (results/pitch_calib/manual/frame_*.json).
# Ручная правка сильнее автоматики: сверка с опорами идёт к ближайшей по
# содержанию кадра, и чем больше ваших кадров, тем меньше ошибка между ними.
set -euo pipefail
cd /Users/doni/Documents/sport/football
PY=/private/tmp/claude-501/-Users-doni-Documents-football/7f0d34e7-ac71-4fb5-9950-2123175b3367/scratchpad/venv/bin/python
VIDEO=~/Downloads/football/clip5_qE8fVwIRQxA.mp4
ANCH=(--anchor results/pitch_calib/calib_285.0.json)
for f in results/pitch_calib/manual/frame_*.json; do
  [ -e "$f" ] && ANCH+=(--anchor "$f")
done
echo "опор из редактора: $(ls results/pitch_calib/manual/frame_*.json 2>/dev/null | wc -l | tr -d ' ')"
$PY scripts/pitch_track.py "$VIDEO" results/pitch_calib/calib_ref_t2s.json results/pitch_calib/track_clip5.json "${ANCH[@]}"
$PY scripts/to_metres.py results/clip5_pos.json results/pitch_calib/track_clip5.json results/clip5_pos_metres.json
rm -rf results/pitch_calib/check
$PY scripts/pitch_check.py "$VIDEO" results/pitch_calib/track_clip5.json results/pitch_calib/check 2 30 70 135 170 229.5 285
echo "готово: привязка и позиции в метрах пересобраны"
