#!/bin/bash
# Ставит управляющий узел на сервер. Запускать НА СЕРВЕРЕ из распакованной папки:
#   bash install.sh
# Требуется docker и docker compose (на сервере sportx они уже есть).
set -e
BASE=${BASE:-/opt/football-runner}
mkdir -p "$BASE"/{results,ssh,cache}
[ -d "$BASE/repo" ] || { echo "положите код проекта в $BASE/repo (rsync с ноутбука или git clone)"; exit 1; }
if [ ! -f "$BASE/env" ]; then
  cat > "$BASE/env" <<'ENVEOF'
# Ключи. Файл не в репозитории, доступ только root.
RUNPOD_KEY=
SPORTX_USER=
SPORTX_PASS=
# Автопоиск новых видео раз в сутки (пусто — выключен), время по Бишкеку:
FB_SCAN_AT=
FB_RUN_ARGS=--per-pod 2 --max-usd 30
ENVEOF
  chmod 600 "$BASE/env"; echo "заполните $BASE/env и запустите install.sh снова"; exit 1
fi
[ -s "$BASE/ssh/id_ed25519" ] || { ssh-keygen -q -t ed25519 -N "" -C "football-runner" -f "$BASE/ssh/id_ed25519"; echo "создан ключ для машин RunPod"; }
chmod 600 "$BASE/ssh/id_ed25519"; chmod 700 "$BASE/ssh"
cp "$BASE/repo/server/docker-compose.yml" "$BASE/docker-compose.yml"
cd "$BASE" && docker compose up -d --build
sleep 3; docker ps --filter name=football-runner --format "{{.Names}} {{.Status}}"
echo "готово. Запросить прогон:  echo '--per-pod 2' > $BASE/results/RUN_REQUEST"
echo "смотреть:                 tail -f $BASE/results/watch.log"
