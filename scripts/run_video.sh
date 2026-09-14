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

# оборванный прошлым запуском файл (нет заголовка mp4) удаляем и качаем заново
if [ -s "$V" ] && ! python -c "import cv2,sys;c=cv2.VideoCapture(sys.argv[1]);n=c.get(7);c.set(1,max(0,n-60));ok,_=c.read();sys.exit(0 if ok and n>1000 else 1)" "$V"; then
  log "файл $V неполный, удаляю"; rm -f "$V"
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
python - "$V" <<'PY' || { touch "out/$NAME.FAILED"; exit 1; }
import cv2, sys
c = cv2.VideoCapture(sys.argv[1]); n = c.get(7); f = c.get(5) or 1
c.set(1, max(0, n - 60)); ok, _ = c.read()
print("видео: %d кадров, %.1f к/с, %.1f мин, конец читается: %s" % (n, f, n / f / 60, ok))
sys.exit(0 if ok and n > 1000 else 1)
PY

log "разбор"
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
