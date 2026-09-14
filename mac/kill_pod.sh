#!/bin/bash
# Забирает результат с Диска и удаляет арендованную машину.
#
# Ключ лежит рядом в .runpod_key с правами 600 и положен туда с разрешения
# владельца. Удаление идёт только после того, как веса реально оказались на
# мак мини: пока файла нет, машина не трогается.
set -uo pipefail
export PATH=/opt/homebrew/bin:$PATH
cd ~/football-ai
POD="${POD:-r6fe4cno9xl5ap}"
KEY=$(cat .runpod_key)

echo "жду RESULT.tar.gz на Диске"
while ! rclone ls gdrive:football-ai/ 2>/dev/null | grep -q RESULT.tar.gz; do sleep 60; done
rclone copy -P gdrive:football-ai/RESULT.tar.gz .
mkdir -p result && tar xzf RESULT.tar.gz -C result

W=$(find result -name best.pt | head -1)
if [ -z "$W" ]; then
  echo "весов в архиве нет, машину НЕ удаляю — разбираться руками"
  exit 1
fi
echo "веса на месте: $(ls -lh "$W" | awk "{print \$5}")"

code=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
        -H "Authorization: Bearer $KEY" "https://rest.runpod.io/v1/pods/$POD")
echo "удаление пода $POD: HTTP $code"
sleep 10
left=$(curl -s -H "Authorization: Bearer $KEY" https://rest.runpod.io/v1/pods | grep -c "\"id\"")
echo "PODS-LEFT $left"
echo DONE
