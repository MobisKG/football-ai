#!/bin/bash
# Process one match end to end. Accepts a local file or a YouTube URL.
#   ./run_match.sh https://www.youtube.com/watch?v=XXXX  match1
#   ./run_match.sh /workspace/game.mp4                   match1
set -e
cd "$(dirname "$0")"
SRC="$1"; NAME="${2:-match}"
W=weights/best.pt
mkdir -p out

if [[ "$SRC" == http* ]]; then
  # Downloading on the rented machine is far faster than uploading from home.
  echo ">>> downloading $SRC"
  yt-dlp -f "bv[height<=1080]+ba/b" --merge-output-format mp4 -o "out/$NAME.mp4" "$SRC"
  SRC="out/$NAME.mp4"
fi

echo ">>> analysing $SRC"
time python analyze_match.py --weights "$W" --source "$SRC" \
     --out "out/${NAME}_pos.json" --device cuda --stride 2

echo ">>> statistics"
python stats.py "out/${NAME}_pos.json" "$NAME" | tee "out/${NAME}_stats.txt"
echo ">>> results in out/"
ls -lh out/
