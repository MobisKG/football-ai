#!/bin/bash
# Ночная работа на мак мини: выбор модели, второй круг самообучения, целая игра разметкой
# каждого кадра, утренний отчёт. Этапы отмечаются файлами night/<этап>.done, перезапуск
# продолжает с первого несделанного. Этап не начинается, если не успевает к DEADLINE.
cd ~/football-ai/fb
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin OMP_NUM_THREADS=4 PYTORCH_ENABLE_MPS_FALLBACK=1
LOCK=night/.lock; mkdir -p night
if [ -f $LOCK ] && kill -0 "$(cat $LOCK)" 2>/dev/null; then exit 0; fi
[ -f night/ALL.done ] && exit 0
echo $$ > $LOCK; trap "rm -f $LOCK" EXIT
caffeinate -i -w $$ &
PY=../.venv/bin/python; D=night
DEADLINE=$(date -j -f "%Y-%m-%d %H:%M" "2026-09-16 20:30" +%s)   # 09:30 по Бишкеку
log() { echo "[$(date +%H:%M:%S)] $*"; }
left() { echo $(( DEADLINE - $(date +%s) )); }
need() { [ "$(left)" -gt "$1" ] || { log "не успеваем этап «$2» (нужно $(( $1/60 )) мин, осталось $(( $(left)/60 )) мин) — пропуск"; return 1; }; }
done_() { touch "$D/$1.done"; }
is() { [ -f "$D/$1.done" ]; }
F12="dolon,birimdik_abai,tennisi_booterek,akbars_icon,akbulut_yntymak88,batken91_medix,baikut_kaganat,nomad_interclub,atscargo_ruor,manastalas_ironmix,mihailovka_bashkarasuu,untitled_3735,avtoreis_talasgrand,gz_real91"
F3="amigo_dosluk,dosatletik,matai_afm,mix_beshkungoi,talas91_361"

log "ночь: старт, до конца $(( $(left)/60 )) мин"
# 0. дождаться дообучения v3 и его замеров
while ! grep -q V3_DONE run_v3.log 2>/dev/null; do
  pgrep -f "run_v3[.]sh" >/dev/null || { log "run_v3.sh не работает и не закончился — запускаю заново"; (nohup ./run_v3.sh >> run_v3.log 2>&1 &); }
  sleep 120
done
# 1. замеры v2 и v3 одинаковым способом (с отбросом выбросов)
if ! is measure23; then
  for m in v2 v3; do
    $PY scripts/pitch_auto/eval_manual.py weights/pitch_lines_$m.pt elkat_ailin --gallery 2>&1 | grep -v Warning > $D/elkat_$m.txt
  done
  $PY scripts/pitch_auto/per_frame_calib.py manastalas_ironmix --from-frames results/pitch/manastalas_ironmix/perframe_600_900/frames.json --out $D/mt_v2 2>&1 | grep ИТОГ
  $PY scripts/pitch_auto/per_frame_calib.py manastalas_ironmix --from-frames results/pitch/manastalas_ironmix/perframe_v3/frames.json --out $D/mt_v3 2>&1 | grep ИТОГ
  $PY scripts/pitch_auto/pick_model.py $D v2 v3 | tail -3; done_ measure23
fi
BEST=$(python3 -c "import json;print(json.load(open('$D/choice.json'))['best'])"); log "лучшая после первого круга: $BEST"
# 2. второй круг: больше уверенных кадров лучшей моделью (все поля), дообучение v4
if ! is harvest2 && need 7200 "сбор кадров, круг 2"; then
  nice -n 10 $PY scripts/pitch_auto/harvest_pseudo.py --weights weights/pitch_lines_$BEST.pt --out results/pitch/pseudo_r2.json \
    --every 10 --cap 80 --every-f3 30 --cap-f3 30 --skip manastalas_ironmix:590-910 --games $F12,$F3 2>&1 | grep -v Warning | grep -E "уверенных|ГОТОВО|Error|Traceback"
  done_ harvest2
fi
if is harvest2 && ! is train4 && need 4200 "дообучение v4"; then
  nice -n 10 $PY scripts/pitch_auto/lines_net.py weights/pitch_lines_v4.pt --init weights/pitch_lines_$BEST.pt --extra results/pitch/pseudo_r2.json --extra-repeat 1 \
    --exclude elkat_ailin --epochs 30 --iters 40 --lr 5e-4 2>&1 | grep -v Warning | grep -E "устройство|эпоха (1|10|20|30)/|Error|Traceback"
  [ -s weights/pitch_lines_v4.pt ] && done_ train4
fi
if is train4 && ! is measure4 && need 2400 "замеры v4"; then
  $PY scripts/pitch_auto/eval_manual.py weights/pitch_lines_v4.pt elkat_ailin --gallery 2>&1 | grep -v Warning > $D/elkat_v4.txt
  nice -n 10 $PY scripts/pitch_auto/per_frame_calib.py manastalas_ironmix --start 600 --dur 300 --fps 5 --weights weights/pitch_lines_v4.pt --out $D/mt_v4 2>&1 | grep ИТОГ
  $PY scripts/pitch_auto/pick_model.py $D $BEST v4 | tail -3; done_ measure4
  BEST=$(python3 -c "import json;print(json.load(open('$D/choice.json'))['best'])"); log "лучшая после второго круга: $BEST"
fi
# 3. целая игра разметкой каждого кадра лучшей моделью, пробег и скорость (без загрузки)
if ! is full && need 3600 "целая игра"; then
  SPF=$(python3 -c "import json;print(json.load(open('$D/mt_$BEST/result.json'))['sec_per_frame'])" 2>/dev/null || echo 1.2)
  DUR=$(python3 -c "print(max(300, int(($(left) - 2700) / ($SPF * 5 + 0.4))))")
  log "целая игра: модель $BEST, успеваем $(( DUR/60 )) мин видео"
  nice -n 10 $PY scripts/pitch_auto/per_frame_calib.py manastalas_ironmix --start 0 --dur $DUR --fps 5 --weights weights/pitch_lines_$BEST.pt --out $D/full_mt --save-track 2>&1 | grep -v Warning | grep -E "ИТОГ|кадров [0-9]+000/|Error|Traceback"
  $PY scripts/pitch_auto/finish_game.py manastalas_ironmix --track results/pitch/manastalas_ironmix/track_perframe.json 2>&1 | grep -E "правдоподобие|Error|Traceback" | cut -c1-600
  cp results/pitch/manastalas_ironmix/speeds_auto/plausibility.json $D/full_mt_plausibility.json 2>/dev/null
  done_ full
fi
# 4. утренний отчёт
python3 - <<'PY'
import json, os, datetime
D = "night"; L = ["# Ночь на мак мини: разметка поля", "", "Отчёт собран %s (время мак мини)." % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), ""]
ch = json.load(open(D + "/choice.json")) if os.path.exists(D + "/choice.json") else {}
L += ["## Модели", "", "| Модель | Элкат: принято опор | Элкат: ошибка ср./макс., м | Манас Талас: удачных кадров | покрыто | отложенные: медиана / 90 %, м | с/кадр |", "|---|---|---|---|---|---|---|"]
for m, r in (ch.get("rows") or {}).items():
    L.append("| %s | %s | %s / %s | %s | %s | %s / %s | %s |" % (m, r.get("elkat_accepted"), r.get("elkat_mean_m"), r.get("elkat_max_m"), r.get("mt_ok"), r.get("mt_covered"), r.get("mt_median"), r.get("mt_p90"), r.get("mt_sec_per_frame")))
L += ["", "Лучшая: **%s**" % ch.get("best"), ""]
if os.path.exists(D + "/full_mt/result.json"):
    f = json.load(open(D + "/full_mt/result.json")); L += ["## Манас Талас целиком (разметка каждого кадра)", "", "```", json.dumps(f, ensure_ascii=False, indent=1)[:1500], "```", ""]
if os.path.exists(D + "/full_mt_plausibility.json"):
    p = json.load(open(D + "/full_mt_plausibility.json")); L += ["Пробег и скорость:", "", "```", json.dumps({k: p.get(k) for k in ("ok", "reason", "players", "dist_per_min_median", "top_speed_median", "top_speed_max", "standing_speed_median_mps")}, ensure_ascii=False), "```"]
open(D + "/night_report.md", "w").write("\n".join(L) + "\n"); print("\n".join(L))
PY
log "НОЧЬ ГОТОВО"; touch night/ALL.done
