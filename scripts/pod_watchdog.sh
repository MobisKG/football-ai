#!/bin/bash
# Сторож на арендованной машине: если управляющий узел не трогал
# /root/fb/heartbeat дольше LIMIT секунд (ноутбук выключен, сеть упала) —
# машина удаляет себя через API, чтобы не крутиться и не жечь деньги.
# Ключ API в /root/.runpod_key, id машины — в переменной RUNPOD_POD_ID.
LIMIT=${1:-2700}
touch /root/fb/heartbeat
while true; do
  sleep 60
  age=$(( $(date +%s) - $(stat -c %Y /root/fb/heartbeat) ))
  if [ "$age" -gt "$LIMIT" ]; then
    echo "$(date) нет сигнала $age с, удаляю машину $RUNPOD_POD_ID" >> /root/fb/watchdog.log
    curl -s -X DELETE -H "Authorization: Bearer $(cat /root/.runpod_key)" "https://rest.runpod.io/v1/pods/$RUNPOD_POD_ID" >> /root/fb/watchdog.log 2>&1
    sleep 120
  fi
done
