#!/bin/bash
# Ждёт результат обучения на Диске и забирает его на мак мини.
#
# Не требует ключа RunPod: удаление арендованной машины делается отдельно,
# руками. Этот скрипт отвечает только за то, чтобы веса не потерялись.
set -uo pipefail
export PATH=/opt/homebrew/bin:$PATH
cd ~/football-ai
while ! rclone ls gdrive:football-ai/ 2>/dev/null | grep -q RESULT.tar.gz; do sleep 60; done
rclone copy -P gdrive:football-ai/RESULT.tar.gz .
mkdir -p result && tar xzf RESULT.tar.gz -C result
echo "RESULT-PULLED"
find result -name best.pt -exec ls -lh {} \;
