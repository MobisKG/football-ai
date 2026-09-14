#!/bin/bash
set -u
export PATH=/opt/homebrew/bin:$PATH
H=~/football-ai
PY=$H/.venv/bin/python
LOG=$H/m960.log
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

throttle() {
  local pid=$1
  while kill -0 "$pid" 2>/dev/null; do
    sleep 240
    kill -0 "$pid" 2>/dev/null && kill -STOP "$pid" && sleep 45 && kill -CONT "$pid"
  done
}
memguard() {
  local pid=$1
  while kill -0 "$pid" 2>/dev/null; do
    sleep 120
    local sw
    sw=$(sysctl -n vm.swapusage | sed -n 's/.*used = \([0-9.]*\)M.*/\1/p')
    echo "[$(date '+%F %T')] swap_used=${sw}M" >> "$H/memwatch960.log"
    if [ "${sw%%.*}" -gt 3000 ] 2>/dev/null; then
      say "ABORT: swap ${sw}M, thrashing"; kill -9 "$pid"; return
    fi
  done
}

say "=== yolo11m @960, 40 epochs, both matches ==="
"$PY" "$H/train_m960.py" >>"$LOG" 2>&1 &
TP=$!; throttle "$TP" & THR=$!; memguard "$TP" & MG=$!
wait "$TP"; RC=$?
kill "$THR" "$MG" 2>/dev/null
[ "$RC" -ne 0 ] && { say "FATAL: training exited $RC"; exit 1; }

W=$H/runs/all_football_m960/weights/best.pt
say "training done: $W"
# Inference stays at 1280 regardless of the training size, so the ball keeps its pixels.
for C in short20 m2_short20; do
  say "analysing $C"
  "$PY" "$H/analyze3.py" --weights "$W" --source "$H/clips/$C.mp4" \
    --target "$H/clips/m960_$C.mp4" --stats "$H/clips/m960_$C.json" \
    --device mps --imgsz 1280 >>"$LOG" 2>&1 || say "WARN: analysis of $C failed"
done
say "=== finished ==="
