#!/bin/bash
# Сторож на сервере: раз в минуту смотрит, не пора ли начать прогон и не
# оборвался ли идущий. Переживает перезапуск контейнера и выключенный ноутбук.
#
# Как попросить прогон:  echo "--per-pod 2" > results/RUN_REQUEST
# Автоматически:         FB_SCAN_AT=03:30 в env — раз в сутки искать новые видео.
cd /app
R=/app/results; mkdir -p "$R"
say() { echo "[$(date '+%d.%m %H:%M:%S')] $*" | tee -a "$R/watch.log"; }
running() { pgrep -f "[f]ull_run.run" >/dev/null; }

start_run() {                       # $1 — дополнительные аргументы
  local dir="$R/run_$(date +%F)"
  say "старт прогона: $dir $1"
  mkdir -p "$dir"
  nohup python -m full_run.run --outdir "$dir" $1 >> "$dir/orchestrator.out" 2>&1 &
  sleep 5
}

say "сторож запущен; прогон по файлу RUN_REQUEST, автопоиск в ${FB_SCAN_AT:-выключен}"
while true; do
  if [ -f "$R/RUN_REQUEST" ] && ! running; then
    args=$(cat "$R/RUN_REQUEST"); mv "$R/RUN_REQUEST" "$R/RUN_REQUEST.taken"
    start_run "$args"
  fi
  # прогон оборвался (перезапуск контейнера, сбой): продолжаем с того же места
  if ! running; then
    for d in "$R"/run_*/; do
      [ -f "$d/state.json" ] || continue
      stage=$(python -c "import json,sys;print(json.load(open('$d/state.json')).get('stage'))" 2>/dev/null)
      if [ -n "$stage" ] && [ "$stage" != "done" ]; then
        say "прогон $d прерван на этапе $stage — продолжаю"
        nohup python -m full_run.run --outdir "${d%/}" --stage collect >> "$d/orchestrator.out" 2>&1 &
        break
      fi
    done
  fi
  # раз в сутки: посмотреть канал и, если есть новые видео, начать прогон
  if [ -n "$FB_SCAN_AT" ] && [ "$(date +%H:%M)" = "$FB_SCAN_AT" ] && ! running; then
    n=$(python -m full_run.channel --limit "${FB_SCAN_LIMIT:-60}" 2>/dev/null | head -1)
    say "автопоиск: $n"
    case "$n" in "новых видео: 0"*) : ;; *) start_run "${FB_RUN_ARGS:---per-pod 2}" ;; esac
    sleep 60
  fi
  sleep 60
done
