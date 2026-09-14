#!/bin/bash
# Копирует непустые логи разбора в *.last: повторная попытка обнуляет логи, копия остаётся.
cd /root/fb/out || exit 0
while true; do
  for f in */log_*.txt *.run.log *.log; do
    [ -s "$f" ] && cp -f "$f" "$f.last" 2>/dev/null
  done
  sleep 20
done
