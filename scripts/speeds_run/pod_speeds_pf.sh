#!/bin/bash
# Машина скоростей: разметка каждого кадра одной игры частями параллельно, потом склейка и ряд.
#   bash pod_speeds_pf.sh <slug> <частей>
SLUG=$1; PARTS=${2:-8}; cd /root/fb
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
P=python
DUR=$($P -c "import cv2;c=cv2.VideoCapture('results/pitch/videos/$SLUG.mp4');print(c.get(7)/c.get(5))")
STEP=$($P -c "print($DUR/$PARTS)")
echo "длительность $DUR с, частей $PARTS"
for k in $(seq 0 $((PARTS-1))); do
  S=$($P -c "print($k*$STEP)")
  nohup $P scripts/pitch_auto/per_frame_calib.py $SLUG --start $S --dur $STEP --fps 5 --out results/pitch/$SLUG/pf_part$k > pf_part$k.log 2>&1 &
done
wait
$P - <<PY
import json,glob
fr=[];spf=[]
bad=0
for k in range($PARTS):
    try: d=json.load(open("results/pitch/$SLUG/pf_part%d/frames.json"%k)); fr+=d["frames"]; spf.append(d["sec_per_frame"])
    except Exception: bad+=1   # упавшая часть — пропуск в ряду, а не потеря всей игры
print("упавших частей",bad)
fr.sort(key=lambda f:f["t"])
json.dump({"slug":"$SLUG","start":0,"dur":$DUR,"fps":5,"frames":fr,"sec_per_frame":sum(spf)/max(1,len(spf))},open("results/pitch/$SLUG/pf_merged.json","w"))
print("кадров",len(fr))
PY
$P scripts/pitch_auto/per_frame_calib.py $SLUG --from-frames results/pitch/$SLUG/pf_merged.json --save-track --out results/pitch/$SLUG/perframe_full > pf_full.log 2>&1
touch .DONE
