#!/bin/bash
# Ожидание строки в логе с проверкой сети: netwait.sh <лог> '<regex>' [макс_сек]
# Раз в минуту проверяет DNS/HTTPS; при пропаже пишет «НЕТ СЕТИ», при возврате — «сеть вернулась».
LOG="$1"; PAT="$2"; MAX="${3:-3600}"; t0=$(date +%s); net=1
while true; do
  grep -qE "$PAT" "$LOG" 2>/dev/null && { echo "дождались: $(grep -E "$PAT" "$LOG" | tail -1)"; exit 0; }
  if curl -s -m 10 -o /dev/null https://rest.runpod.io/ 2>/dev/null; then [ $net = 0 ] && echo "$(date +%H:%M:%S) сеть вернулась"; net=1
  else [ $net = 1 ] && echo "$(date +%H:%M:%S) НЕТ СЕТИ"; net=0; fi
  [ $(( $(date +%s) - t0 )) -gt "$MAX" ] && { echo "предел ожидания $MAX с"; exit 1; }
  sleep 30
done
