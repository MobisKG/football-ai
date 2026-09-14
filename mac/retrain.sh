#!/bin/bash
# Retrain on both matches with yolo11s, then re-evaluate both test clips.
set -u
export PATH=/opt/homebrew/bin:$PATH
H=~/football-ai
PY=$H/.venv/bin/python
LOG=$H/retrain.log
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

throttle() {
  local pid=$1
  while kill -0 "$pid" 2>/dev/null; do
    sleep 240
    kill -0 "$pid" 2>/dev/null && kill -STOP "$pid" && sleep 45 && kill -CONT "$pid"
  done
}

# Guard against the failure that wasted last night: if the machine starts
# swapping hard, the run is thrashing and is worth killing rather than waiting out.
memguard() {
  local pid=$1
  while kill -0 "$pid" 2>/dev/null; do
    sleep 120
    local sw
    sw=$(sysctl -n vm.swapusage | sed -n 's/.*used = \([0-9.]*\)M.*/\1/p')
    echo "[$(date '+%F %T')] swap_used=${sw}M" >> "$H/memwatch.log"
    if [ "${sw%%.*}" -gt 3000 ] 2>/dev/null; then
      say "ABORT: swap ${sw}M, training is thrashing"
      kill -9 "$pid"; return
    fi
  done
}

say "=== retrain start: yolo11s, imgsz 1280, 60 epochs, both matches ==="
"$PY" "$H/train_all_s.py" >>"$LOG" 2>&1 &
TP=$!
throttle "$TP" & THR=$!
memguard "$TP" & MG=$!
wait "$TP"; RC=$?
kill "$THR" "$MG" 2>/dev/null
[ "$RC" -ne 0 ] && { say "FATAL: training exited $RC"; exit 1; }

W=$H/runs/all_football_s/weights/best.pt
say "training done, weights at $W"

for C in short20 m2_short20; do
  say "analysing $C"
  "$PY" "$H/analyze3.py" --weights "$W" --source "$H/clips/$C.mp4" \
    --target "$H/clips/s_$C.mp4" --stats "$H/clips/s_$C.json" \
    --device mps >>"$LOG" 2>&1 || say "WARN: analysis of $C failed"
done
say "=== retrain finished ==="
