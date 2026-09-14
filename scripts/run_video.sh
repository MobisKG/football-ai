#!/bin/bash
# Одно видео на арендованной машине: скачать с YouTube → run_gpu_match.sh →
# упаковать результаты. Все шаги пропускаются, если результат уже есть.
#   ./run_video.sh <youtube_id> <имя> [шаг]
# Итог: out/<имя>.tar.gz и файл out/<имя>.DONE; при ошибке out/<имя>.FAILED.
cd "$(dirname "$0")"
YT="$1"; NAME="$2"; STEP="${3:-4}"
mkdir -p out; rm -f "out/$NAME.DONE" "out/$NAME.FAILED"
V="out/$NAME.mp4"
log() { echo "[$(date +%H:%M:%S)] $*"; }

# Целостность mp4 проверяем по последнему видеопакету, а не по заголовку: у обрезанной
# передачи «АТС Карго vs РУОР» (1,08 ГБ из ~1,5) заголовок обещал 45,3 мин, OpenCV
# «читал конец», а данных было на 34 мин — разбор молча взял 75 % игры.
video_ok() {
  local f="$1" d last
  [ -s "$f" ] || return 1
  d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null) || return 1
  [ -n "$d" ] || return 1
  last=$(ffprobe -v error -select_streams v:0 -read_intervals "$(python3 -c "print(max(0,float('$d')-60))")%" \
         -show_entries packet=pts_time -of csv=p=0 "$f" 2>/dev/null | grep -E '^[0-9.]+$' | tail -1)
  [ -n "$last" ] || return 1
  python3 -c "import sys; sys.exit(0 if float('$last') >= float('$d') - 10 else 1)"
}
if [ -s "$V" ] && ! video_ok "$V"; then
  log "файл $V неполный (последний пакет раньше конца по заголовку), удаляю"; rm -f "$V"
fi
if [ ! -s "$V" ] || [ -e "$V.part" ]; then
  log "скачивание $YT"
  rm -f "$V" "$V.part"
  ok=0
  # YouTube с части адресов RunPod отвечает «Sign in to confirm you're not a bot»:
  # другие клиенты (android, ios) часто проходят; если нет — машину заменит batch_run.py
  for client in "" "android" "ios" "mweb"; do
    for fmt in "299" "137" "bv[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]"; do
      ea=""; [ -n "$client" ] && ea="--extractor-args youtube:player_client=$client"
      if yt-dlp -q --no-warnings $ea -f "$fmt" --no-part --merge-output-format mp4 -o "$V" "https://www.youtube.com/watch?v=$YT" 2> "out/$NAME.dl.err"; then ok=1; break 2; fi
      log "клиент '${client:-default}', формат $fmt: $(tail -n 1 out/$NAME.dl.err | cut -c1-120)"; rm -f "$V"
    done
  done
  [ $ok = 1 ] || { log "!!! видео не скачалось"; touch "out/$NAME.FAILED"; exit 1; }
fi
# текст содержит «видео не скачалось»: по нему batch_run.py просит соседнюю машину передать видео
video_ok "$V" || { log "!!! видео не скачалось целиком (последний пакет раньше конца)"; rm -f "$V"; touch "out/$NAME.FAILED"; exit 1; }
log "видео: $(ffprobe -v error -show_entries format=duration:stream=avg_frame_rate -select_streams v:0 -of csv=p=0 "$V" | tr '\n' ' ') (длительность, к/с), целое"

# шаг по кадрам под частоту видео: цель — 15 кадров в секунду. Для части игр YouTube
# отдаёт только 1080p30 (Tennisi vs Boo-Terek), и шаг 4 давал 7,5 к/с вместо 15.
FPS=$(python -c "import cv2,sys;print(cv2.VideoCapture(sys.argv[1]).get(5))" "$V")
STEP=$(python -c "import sys;f=float(sys.argv[1] or 0);print(max(1,round(f/15)) if f>0 else int(sys.argv[2]))" "$FPS" "$STEP")
log "частота видео $FPS к/с, шаг $STEP"
log "разбор"
[ -s "out/$NAME.run.log" ] && mv "out/$NAME.run.log" "out/$NAME.run.log.prev.$(date +%H%M%S)"
./run_gpu_match.sh "$V" "$NAME" "$STEP" > "out/$NAME.run.log" 2>&1
rc=$?
tail -3 "out/$NAME.run.log"
O="out/$NAME"
if [ $rc != 0 ] || [ ! -s "$O/pos.json" ] || [ ! -s "$O/ball_cands.json" ] || [ ! -s "$O/numbers.json" ] || [ ! -s "$O/osnet.json" ]; then
  log "!!! цепочка не завершилась (rc=$rc)"; touch "out/$NAME.FAILED"; exit 1
fi
[ -s "$O/scoreboard.json" ] || log "ВНИМАНИЕ: табло не прочитано"
log "упаковка"
tar czf "out/$NAME.tar.gz" -C out "$NAME/pos.json" "$NAME/ball_cands.json" "$NAME/numbers.json" "$NAME/osnet.json" \
    $( [ -s "$O/scoreboard.json" ] && echo "$NAME/scoreboard.json" ) "$NAME/crops/tracks.json" \
    $(cd out && ls "$NAME"/log_*.txt 2>/dev/null) && touch "out/$NAME.DONE"
# несколько снимков на команду для утренней проверки цветов (не все вырезки)
python - "$O" <<'PY'
import json, os, sys, tarfile
o = sys.argv[1]; n = json.load(open(os.path.join(o, "numbers.json")))
from collections import defaultdict
by = defaultdict(list)
for t, r in n.items():
    if r.get("kit") and r.get("number"): by[r["kit"].split()[0]].append(os.path.join(o, "crops", r["per_shot"][0]["shot"]) if r.get("per_shot") else None)
with tarfile.open(os.path.join(o + "_kits.tar.gz"), "w:gz") as tf:
    for k, lst in by.items():
        for p in [p for p in lst if p and os.path.exists(p)][:6]:
            tf.add(p, arcname=k + "_" + os.path.basename(p))
PY
log "готово: $(du -h out/$NAME.tar.gz | cut -f1)"
