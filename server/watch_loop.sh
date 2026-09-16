#!/bin/bash
# Сторож на сервере: раз в минуту смотрит, не пора ли начать прогон и не
# оборвался ли идущий. Переживает перезапуск контейнера и выключенный ноутбук.
#
# Как попросить прогон:  echo "--per-pod 2" > /opt/football-runner/results/RUN_REQUEST
# Автоматически:         FB_SCAN_AT=03:30 в env — раз в сутки искать новые видео.
cd /app
R=${FB_RESULTS:-/data/results}; mkdir -p "$R"
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
  # прогон оборвался (перезапуск контейнера, сбой): продолжаем с того же места.
  # Продолжаем ТОЛЬКО если у прогона есть свои арендованные машины: иначе сторож
  # заново запускал этап сбора у давно законченного прогона (16.09 это привело к
  # сносу чужой работавшей машины).
  if ! running; then
    for d in "$R"/run_*/; do
      [ -f "$d/state.json" ] || continue
      resume=$(python - "$d/state.json" <<'PY'
import json, sys, time
s = json.load(open(sys.argv[1]))
alive = [p for p in s.get("pods", []) if not p.get("t1")]
fresh = time.time() - s.get("started", 0) < 12 * 3600
print("yes" if s.get("stage") in ("download", "fanout", "collect") and alive and fresh else "no")
PY
)
      if [ "$resume" = "yes" ]; then
        say "прогон $d прерван на этапе сбора, машины ещё живы — продолжаю"
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
