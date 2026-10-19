#!/bin/bash
# Видео канала → B2 source/<slug>.mp4, строго по одному:
# скачать на этот компьютер → проверить → залить в B2 → сверить размер в B2 → удалить у себя.
# Ноутбук не засыпает, пока скрипт работает (caffeinate). Повторный запуск пропускает то, что уже в B2.
#
#   bash scripts/pitch_auto/ingest_b2.sh results/ingest_2026-09-20.json
#
# Нужны: ключ B2 с правом записи в ~/Documents/sport/.env (KEY_ID, APPLICATION_KEY),
# yt-dlp и b2sdk в ~/.cache/fb-ytdlp/venv, ffprobe. Журнал: results/ingest.log.
set -u
cd "$(dirname "$0")/../.."
LIST=${1:?список видео json}
PY=~/.cache/fb-ytdlp/venv/bin/python
TMP=${TMPDIR:-/tmp}/fb_ingest; mkdir -p "$TMP"
LOG=results/ingest.log
# YT_COOKIES=файл cookies (если YouTube просит подтвердить, что не бот)
COOK=(); [ -n "${YT_COOKIES:-}" ] && COOK=(--cookies "$YT_COOKIES")
FMT='bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]'
log() { echo "[$(date '+%d.%m %H:%M:%S')] $*" | tee -a "$LOG"; }

caffeinate -dimsu -w $$ &          # не спать, пока жив этот скрипт
log "начало: $(python3 -c "import json;print(len(json.load(open('$LIST'))))" ) видео, по одному"

python3 -c "import json;[print(v['slug'],v['yt'],v['duration_s']) for v in json.load(open('$LIST'))]" |
while read -r SLUG YT DUR; do
  if $PY scripts/pitch_auto/b2.py have "source/$SLUG.mp4" 2>/dev/null; then
    F="$TMP/$SLUG.mp4"
    if [ -f "$F" ] && [ "$($PY scripts/pitch_auto/b2.py size "source/$SLUG.mp4")" = "$(stat -f %z "$F")" ]; then rm -f "$F"; log "$SLUG: уже в B2, копия с компьютера удалена"
    else log "$SLUG: уже в B2, пропускаю"; fi
    continue
  fi
  F="$TMP/$SLUG.mp4"; rm -f "$F".part
  ok=0
  # уже скачанное целиком (прошлый запуск оборвался на заливке) не качаем заново
  D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$F" 2>/dev/null)
  if [ -n "$D" ] && python3 -c "import sys;sys.exit(0 if $D >= 0.95*$DUR else 1)"; then ok=1; log "$SLUG: уже скачано, сразу в B2"; fi
  for try in 1 2 3; do
    [ $ok = 1 ] && break
    log "$SLUG: скачиваю ($YT, попытка $try)"
    $PY -m yt_dlp -q --no-warnings --socket-timeout 30 --retries 10 -f "$FMT" --merge-output-format mp4 ${COOK[@]+"${COOK[@]}"} \
        -o "$F" "https://www.youtube.com/watch?v=$YT" </dev/null >>"$LOG" 2>&1
    D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$F" 2>/dev/null)
    # целое видео: длительность не меньше 95 % заявленной каналом
    if [ -n "$D" ] && python3 -c "import sys;sys.exit(0 if $D >= 0.95*$DUR else 1)"; then ok=1; break; fi
    log "$SLUG: файл неполный (${D:-нет} с из $DUR с)"; rm -f "$F"; sleep 30
  done
  [ $ok = 1 ] || { log "$SLUG: НЕ СКАЧАЛОСЬ, иду дальше"; continue; }
  SZ=$(stat -f %z "$F")
  log "$SLUG: скачано $((SZ/1000000)) МБ, заливаю в B2"
  up=0
  for try in 1 2 3; do
    $PY scripts/pitch_auto/b2.py put "$F" "source/$SLUG.mp4" </dev/null >>"$LOG" 2>&1
    R=$($PY scripts/pitch_auto/b2.py size "source/$SLUG.mp4" 2>/dev/null)
    if [ "$R" = "$SZ" ]; then up=1; break; fi     # байт в байт
    log "$SLUG: заливка не подтвердилась (попытка $try)"; sleep 30
  done
  if [ $up = 1 ]; then rm -f "$F"; log "$SLUG: в B2, с компьютера удалено"
  else log "$SLUG: НЕ ЗАЛИЛОСЬ (лимит места в B2?), файл оставлен: $F. Останавливаюсь, чтобы не копить видео на диске"; exit 1; fi
done
log "конец. В B2 source/: $($PY scripts/pitch_auto/b2.py ls source/ | wc -l | tr -d ' ') файлов"
