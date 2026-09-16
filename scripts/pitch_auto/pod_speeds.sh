#!/bin/bash
# Пакет для арендованной машины. Два этапа: сначала все видео скачиваются по очереди и
# проверяются на целостность (ffprobe + чтение кадра из конца через cv2), затем игры
# считаются параллельно: кадры каждые 15 с → автоопоры (сеть линий) → протягивание привязки.
#   bash pod_speeds.sh "slug:ytid slug:ytid ..." [одновременно]
cd /root/fb
GAMES="$1"; PAR="${2:-4}"
export OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 NUMBA_NUM_THREADS=4
log() { echo "[$(date +%H:%M:%S)] $*"; }
video_ok() {
  python - "$1" <<'PY'
import sys, cv2
c = cv2.VideoCapture(sys.argv[1]); n = c.get(7); f = c.get(5)
if not c.isOpened() or n < 100 or f < 1: sys.exit(1)
ok, _ = c.read()
c.set(cv2.CAP_PROP_POS_FRAMES, int(n) - 30); ok2, _ = c.read()
sys.exit(0 if (ok and ok2) else 1)
PY
}
log "этап 1: скачивание"
for g in $GAMES; do
  s="${g%%:*}"; yt="${g##*:}"; V=videos/$s.mp4
  if [ -s "$V" ] && video_ok "$V"; then log "$s: видео уже есть"; continue; fi
  ok=0
  for try in 1 2 3; do
    for fmt in 298 "bv[height<=720][ext=mp4][vcodec^=avc1]" "bv[height<=720][ext=mp4]"; do
      rm -f "$V"
      yt-dlp -q --no-warnings --socket-timeout 30 -f "$fmt" --no-part -o "$V" "https://www.youtube.com/watch?v=$yt" >/dev/null 2>&1
      if [ -s "$V" ] && video_ok "$V"; then ok=1; break 2; fi
      log "$s: формат $fmt не вышел"
    done
    sleep 30
  done
  [ $ok = 1 ] && log "$s: скачано $(du -m $V|cut -f1) МБ" || log "$s: !!! видео не скачалось"
done
log "этап 2: разбор, одновременно $PAR"
one() {
  local s="$1" out=out/$s.log
  {
    [ -s videos/$s.mp4 ] || { log "$s: нет видео"; exit 1; }
    log "$s: кадры"; python scripts/pitch_auto/extract_frames.py "$s" --every 15 || exit 1
    log "$s: опоры"; python scripts/pitch_auto/run_games.py weights/pitch_lines_v2.pt "$s" || exit 1
    log "$s: протягивание"; python scripts/pitch_auto/track_game.py "$s" || exit 1
    log "$s: готово"
  } >> "$out" 2>&1
}
export -f one log
for g in $GAMES; do echo "${g%%:*}"; done | xargs -P "$PAR" -I{} bash -c 'one "$@"' _ {}
log "ВСЁ ГОТОВО"
tar cf out/tracks.tar results/pitch/*/auto results/pitch/*/track_auto.json results/pitch/*/check_auto.jpg 2>/dev/null
log "архив: $(du -m out/tracks.tar|cut -f1) МБ"
