#!/bin/bash
# Overnight pipeline: finish current run -> label match 2 -> merge -> retrain -> evaluate.
# Detached from ssh; keeps the Mac awake; duty-cycles every training run.
set -u
export PATH=/opt/homebrew/bin:$PATH
H=~/football-ai
PY=$H/.venv/bin/python
LOG=$H/pipeline.log
M2=~/Desktop/"match2 [qE8fVwIRQxA].mp4"
export TRAIN_HOURS=${TRAIN_HOURS:-9}

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# Duty-cycle a pid so the passively cooled Mac is not pinned at 100%.
throttle() {
  local pid=$1 run=${2:-240} pause=${3:-45}
  while kill -0 "$pid" 2>/dev/null; do
    sleep "$run"
    kill -0 "$pid" 2>/dev/null && kill -STOP "$pid" && sleep "$pause" && kill -CONT "$pid"
  done
}

say "=== pipeline start (train budget ${TRAIN_HOURS}h) ==="

# --- step 1: wait out the run that is already going -------------------------
if pgrep -f "[t]rain.py" >/dev/null; then
  say "step 1: waiting for the match-1 training already in flight"
  while pgrep -f "[t]rain.py" >/dev/null; do sleep 60; done
fi
say "step 1: done. best weights: $H/runs/mini_football/weights/best.pt"

# --- step 2: sample frames from match 2 -------------------------------------
if [ ! -d "$H/ds2/frames" ] || [ "$(ls "$H/ds2/frames" 2>/dev/null | wc -l)" -lt 100 ]; then
  say "step 2: extracting frames from match 2"
  mkdir -p "$H/ds2/frames"
  nice -n 10 ffmpeg -v error -i "$M2" -vf "fps=1/4" -q:v 2 "$H/ds2/frames/g_%05d.jpg" -y \
    >>"$LOG" 2>&1 || { say "FATAL: ffmpeg failed"; exit 1; }
fi
say "step 2: done, $(ls "$H/ds2/frames" | wc -l | tr -d ' ') frames"

# --- step 3: auto-label match 2 ---------------------------------------------
if [ ! -d "$H/ds2/labels" ] || [ "$(ls "$H/ds2/labels" 2>/dev/null | wc -l)" -lt 50 ]; then
  say "step 3: auto-labelling match 2 (finer ball tiles + in-domain proposer)"
  "$PY" "$H/autolabel.py" --frames "$H/ds2/frames" --out "$H/ds2/labels" \
      --device mps --slice 512 \
      --extra-ball-weights "$H/runs/mini_football/weights/best.pt" >>"$LOG" 2>&1 \
    || { say "FATAL: autolabel failed"; exit 1; }
fi
say "step 3: done, $(ls "$H/ds2/labels" | wc -l | tr -d ' ') labelled frames"

# --- step 4: merge both matches --------------------------------------------
say "step 4: merging datasets"
"$PY" "$H/build_ds_all.py" >>"$LOG" 2>&1 || { say "FATAL: merge failed"; exit 1; }

# --- step 5: final training -------------------------------------------------
say "step 5: training yolo11m on both matches for ${TRAIN_HOURS}h"
"$PY" "$H/train_all.py" >>"$LOG" 2>&1 &
TP=$!
throttle "$TP" 240 45 &
THR=$!
wait "$TP"; RC=$?
kill "$THR" 2>/dev/null
[ "$RC" -ne 0 ] && { say "FATAL: training exited $RC"; exit 1; }
W=$H/runs/all_football/weights/best.pt
say "step 5: done, weights at $W"

# --- step 6: evaluate on both test clips ------------------------------------
for C in short20 m2_short20; do
  say "step 6: analysing $C"
  "$PY" "$H/analyze3.py" --weights "$W" --source "$H/clips/$C.mp4" \
      --target "$H/clips/final_$C.mp4" --stats "$H/clips/final_$C.json" \
      --device mps >>"$LOG" 2>&1 || say "WARN: analysis of $C failed"
done

say "=== pipeline finished ==="
