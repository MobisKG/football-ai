#!/bin/bash
# Connect rclone to the Google Drive of adminroomskg@gmail.com.
#
# Run this on the Mac mini. It needs a browser once, on YOUR laptop, so the
# flow is: this machine prints a URL, you approve it in the browser, you paste
# the token back.
#
# Scope is drive.file on purpose: rclone can then only touch files it created
# itself, not the rest of that Drive. If the token ever leaks (it is copied to
# the rented machine), the blast radius stays small.
set -euo pipefail
export PATH=/opt/homebrew/bin:$PATH
REMOTE="${REMOTE:-gdrive}"

if rclone listremotes | grep -q "^${REMOTE}:"; then
  echo "Удалённый диск '$REMOTE' уже настроен."
  rclone about "${REMOTE}:" || true
  exit 0
fi

cat <<'TXT'
=========================================================
Настройка доступа к Google Drive (adminroomskg@gmail.com)
=========================================================

Шаг 1. На СВОЁМ ноутбуке выполни:

    brew install rclone            # если ещё не стоит
    rclone authorize "drive" --drive-scope drive.file

   Откроется браузер. Войди как adminroomskg@gmail.com и разреши доступ.
   В терминале появится длинная строка вида  {"access_token":...}

Шаг 2. Скопируй эту строку и вставь её сюда, затем нажми Enter:
TXT
read -r TOKEN
[ -n "$TOKEN" ] || { echo "пустой токен"; exit 1; }

rclone config create "$REMOTE" drive \
  scope=drive.file \
  token="$TOKEN" \
  config_is_local=false >/dev/null

echo "готово. проверяю:"
rclone about "${REMOTE}:"
rclone mkdir "${REMOTE}:football-ai"
echo "папка football-ai создана на Диске"
