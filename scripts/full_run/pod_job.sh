#!/bin/bash
# Самодостаточная работа одной арендованной машины: свои игры от начала до конца.
#   ./pod_job.sh <шаг> <имя1> <имя2> ...
# Видео уже лежат в out/<имя>.mp4 (их принесла машина-качалка). Если какого-то
# нет, run_video.sh скачает его сам — тогда машина просто потратит лишние минуты.
#
# Запускается через nohup и переживает обрыв связи с управляющим узлом: ни один
# шаг не ждёт команд снаружи. По каждой игре в конце появляется out/<имя>.READY,
# по всем — out/ALL_DONE. Сторож забирает результаты по этим меткам.
cd "$(dirname "$0")/.."   # скрипт лежит в full_run/, работать нужно из корня (run_video.sh, out/)
STEP="${1:-4}"; shift
log() { echo "[$(date +%H:%M:%S)] $*" >> out/job.log; }
mkdir -p out; log "начало, игр: $#, шаг $STEP"
for NAME in "$@"; do
  [ -e "out/$NAME.READY" ] && { log "$NAME уже готов"; continue; }
  YT=$(python3 -c "import json;print(next((v['yt'] for v in json.load(open('plan.json')) if v['slug']=='$NAME'), ''))" 2>/dev/null)
  log "$NAME: разбор на видеокарте"
  ./run_video.sh "$YT" "$NAME" "$STEP" >> "out/$NAME.log" 2>&1
  if [ ! -e "out/$NAME.DONE" ]; then log "$NAME: разбор не дошёл до конца, пропускаю"; continue; fi
  log "$NAME: процессорные шаги"
  TITLE=$(python3 -c "import json;print(next((v.get('title','$NAME') for v in json.load(open('plan.json')) if v['slug']=='$NAME'), '$NAME'))" 2>/dev/null)
  if python3 full_run/pod_cpu_steps.py "$NAME" --matches matches.json --title "$TITLE" >> "out/$NAME.cpu.log" 2>&1; then
    tar czf "out/$NAME.stats.tar.gz" -C out "$NAME/stats.json" "$NAME/stats.txt" "$NAME/home.json" \
        $( [ -s "out/$NAME/scoreboard.json" ] && echo "$NAME/scoreboard.json" ) 2>/dev/null
    touch "out/$NAME.READY"; log "$NAME: готово"
  else
    log "$NAME: процессорные шаги упали, см. out/$NAME.cpu.log"; touch "out/$NAME.CPUFAILED"
  fi
  rm -f "out/$NAME.mp4"          # место под следующее видео: диск машины 60 ГБ
done
touch out/ALL_DONE; log "все игры пройдены"
