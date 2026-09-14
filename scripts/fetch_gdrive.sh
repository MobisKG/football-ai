#!/bin/bash
# Pull the dataset from Google Drive onto the rented machine.
#
# Two ways in:
#   1. rclone with the config copied from the Mac mini (private, no sharing)
#   2. a public "anyone with the link" share, via gdown
#
#   ./fetch_gdrive.sh football-dataset.tar.gz          # rclone
#   ./fetch_gdrive.sh "https://drive.google.com/..."   # public link
set -euo pipefail
SRC="$1"
REMOTE="${REMOTE:-gdrive}"

case "$SRC" in
  *drive.google.com*|*docs.google.com*)
    ID=$(printf '%s' "$SRC" | sed -nE 's#.*/d/([A-Za-z0-9_-]+).*#\1#p; s#.*[?&]id=([A-Za-z0-9_-]+).*#\1#p' | head -1)
    [ -n "$ID" ] || { echo "не разобрал ссылку"; exit 1; }
    OUT="football-dataset.tar.gz"
    python -m gdown "$ID" -O "$OUT"
    ;;
  *)
    command -v rclone >/dev/null || curl -s https://rclone.org/install.sh | bash
    [ -f /root/.config/rclone/rclone.conf ] || { echo "нет rclone.conf — скопируй его с мак мини"; exit 1; }
    OUT="$SRC"
    rclone copy -P "${REMOTE}:football-ai/${SRC}" .
    ;;
esac

ls -lh "$OUT"
case "$OUT" in *.tar.gz|*.tgz) echo ">>> распаковываю"; tar xzf "$OUT";; esac
echo ">>> готово, данные в data/"
