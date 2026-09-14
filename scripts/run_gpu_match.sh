#!/bin/bash
# GPU-часть разбора одной игры на арендованной машине. Всё, что не требует
# видеокарты (траектория мяча, команды, вратари, склейка, статистика),
# считается потом на маке бесплатно.
#
#   ./run_gpu_match.sh видео.mp4 имя
#
# На выходе в out/<имя>/: pos.json, ball_cands.json, crops/, numbers.json,
# osnet.json. Каждый шаг пропускается, если его результат уже есть, — после
# обрыва можно просто запустить заново.
set -e
cd "$(dirname "$0")"
VIDEO="$1"; NAME="${2:-match}"
OUT="out/$NAME"; mkdir -p "$OUT"
W=weights/best_7matches.pt
export PYTHONPATH=/root/deep-person-reid:$PYTHONPATH
t0=$(date +%s)
step() { echo ">>> $1  ($(( ($(date +%s) - t0) / 60 )) мин с начала)"; }

step "игроки: детекция и трекинг"
[ -s "$OUT/pos.json" ] || python analyze_match.py --weights $W --source "$VIDEO" \
    --out "$OUT/pos.json" --device cuda --stride 2

step "мяч: YOLO 1920"
[ -s "$OUT/ball_cands.json" ] || python ball_yolo.py --video "$VIDEO" --weights $W \
    --out "$OUT/ball_cands.json" --step 2 --imgsz 1920 --conf 0.12

step "вырезки игроков"
[ -s "$OUT/crops/tracks.json" ] || python extract_crops2.py "$OUT/pos.json" "$VIDEO" "$OUT/crops"

step "номера и цвет формы"
[ -s "$OUT/numbers.json" ] || python read_numbers2.py "$OUT/crops" "$OUT/numbers.json" --batch 16

step "векторы внешности OSNet"
[ -s "$OUT/osnet.json" ] || python reid_osnet.py "$OUT/crops" "$OUT/osnet.json"

step "готово"
du -sh "$OUT"/*
