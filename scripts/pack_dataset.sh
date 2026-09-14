#!/bin/bash
# Pack the auto-labelled dataset for upload to Google Drive.
#
# Videos are NOT included: they are only needed to cut frames, which happens
# here. The pod only ever needs the labelled frames, which are several times
# smaller than the source video.
set -euo pipefail
cd "$(dirname "$0")"
SRC="${1:-data}"
OUT="${2:-football-dataset.tar.gz}"
tar czf "$OUT" "$SRC"/*/frames "$SRC"/*/labels 2>/dev/null || tar czf "$OUT" "$SRC"
ls -lh "$OUT"
echo "Загрузи этот файл на Google Drive, открой доступ по ссылке,"
echo "и на арендованной машине выполни:  ./fetch_gdrive.sh '<ссылка>' $OUT"
