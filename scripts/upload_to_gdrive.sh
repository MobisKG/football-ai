#!/bin/bash
# Upload the packed dataset to Google Drive. Runs on the Mac mini, off the clock:
# the rented GPU is not started until this has finished.
set -euo pipefail
export PATH=/opt/homebrew/bin:$PATH
REMOTE="${REMOTE:-gdrive}"
FILE="${1:-football-dataset.tar.gz}"
[ -f "$FILE" ] || { echo "нет файла $FILE"; exit 1; }
rclone copy -P --transfers 4 "$FILE" "${REMOTE}:football-ai/"
echo "загружено: ${REMOTE}:football-ai/$(basename "$FILE")"
echo
echo "На арендованной машине:"
echo "    ./fetch_gdrive.sh $(basename "$FILE")"
