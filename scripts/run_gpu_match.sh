#!/bin/bash
# GPU-часть разбора одной игры на арендованной машине. Всё, что не требует
# видеокарты (траектория мяча, команды, вратари, склейка, статистика),
# считается потом на маке бесплатно.
#
#   ./run_gpu_match.sh видео.mp4 имя [шаг]
#
# На выходе в out/<имя>/: pos.json, ball_cands.json, scoreboard.json, crops/,
# numbers.json, osnet.json. Каждый шаг пропускается, если его результат уже
# есть, — после обрыва можно просто запустить заново.
#
# Шаг по кадрам: записи 60 кадров/с, шаг 4 даёт 15 кадров/с — хватает для
# трекинга, владения и скоростей. На A40 при шаге 2 один только поиск игроков
# шёл 22 кадра/с (~57 мин на 43-минутную игру), упор был в процессор, видеокарта
# занята на 40 %. Поэтому игроки, мяч и табло считаются одновременно.
set -e
cd "$(dirname "$0")"
VIDEO="$1"; NAME="${2:-match}"; STEP="${3:-4}"
OUT="out/$NAME"; mkdir -p "$OUT"
W=weights/best_7matches.pt
export PYTHONPATH=/root/deep-person-reid:$PYTHONPATH
export YOLO_CONFIG_DIR=/root/.yolo; mkdir -p $YOLO_CONFIG_DIR
# Модели HuggingFace (SigLIP, Qwen) скачаны в pod_setup.sh. Без этого from_pretrained
# ходит на сервер при каждом разборе; ночью 14–15.09 поиск игроков трижды падал
# ровно на загрузке SigLIP в конце прохода и проходил при повторе.
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# На машинах RunPod по 96 ядер, а OpenBLAS собран на 64 потока: печатает «precompiled
# NUM_THREADS exceeded, adding auxiliary array» и изредка роняет процесс без traceback
# на UMAP/KMeans в конце analyze_match.py (пойман лог Уч тонтос vs Мин Куш, 15.09).
export OPENBLAS_NUM_THREADS=16 OMP_NUM_THREADS=16 MKL_NUM_THREADS=16 NUMBA_NUM_THREADS=16
t0=$(date +%s)
step() { echo ">>> $1  ($(( ($(date +%s) - t0) / 60 )) мин с начала)"; }

# логи прошлой попытки не затираем: при повторе после падения иначе пропадает причина
for f in "$OUT"/log_*.txt; do [ -s "$f" ] && mv "$f" "$f.prev.$(date +%H%M%S)"; done
step "параллельно: игроки и мяч"
pids=()
if [ ! -s "$OUT/pos.json" ]; then
  python analyze_match.py --weights $W --source "$VIDEO" --out "$OUT/pos.json" \
      --device cuda --stride $STEP > "$OUT/log_players.txt" 2>&1 & pids+=($!)
fi
if [ ! -s "$OUT/ball_cands.json" ]; then
  python ball_yolo.py --video "$VIDEO" --weights $W --out "$OUT/ball_cands.json" \
      --step $STEP --imgsz 1920 --conf 0.12 > "$OUT/log_ball.txt" 2>&1 & pids+=($!)
fi
fail=0
for p in "${pids[@]}"; do wait $p || fail=1; done
for f in "$OUT"/log_*.txt; do tail -n 1 "$f"; done   # tail -2 с несколькими файлами в Linux — ошибка, и set -e ронял цепочку
[ $fail = 0 ] || { echo "!!! один из параллельных шагов упал, см. $OUT/log_*.txt"; exit 1; }

step "вырезки игроков"
[ -s "$OUT/crops/tracks.json" ] || python extract_crops2.py "$OUT/pos.json" "$VIDEO" "$OUT/crops"

step "номера и цвет формы"
[ -s "$OUT/numbers.json" ] || python read_numbers2.py "$OUT/crops" "$OUT/numbers.json" --batch 16

step "векторы внешности OSNet"
[ -s "$OUT/osnet.json" ] || python reid_osnet.py "$OUT/crops" "$OUT/osnet.json"

# Табло — последним и небольшими пачками: 16 увеличенных вырезок вместе с двумя
# YOLO заняли 35 ГБ и упали с нехваткой видеопамяти на A40 (48 ГБ).
step "табло: счёт и голы"
# табло на этом канале почти не читается (за 4 тура — один раз), а время видеокарты тратит: только при SCOREBOARD=1
[ "${SCOREBOARD:-0}" != 1 ] || [ -s "$OUT/scoreboard.json" ] || python scoreboard.py "$VIDEO" "$OUT/scoreboard.json" --step 10 --batch 4 \
    > "$OUT/log_scoreboard.txt" 2>&1 || echo "ВНИМАНИЕ: табло не прочитано, см. $OUT/log_scoreboard.txt (остальное готово)"

step "готово"
du -sh "$OUT"/*
