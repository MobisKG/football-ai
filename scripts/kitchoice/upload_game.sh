#!/bin/bash
# Загрузить игру прогона на сайт, если в home.json хозяева уверены: upload_game.sh <папка прогона> <slug>
cd /Users/doni/Documents/sport/football/scripts
R=../results/$1; D=$R/$2; P=~/.cache/fb-ytdlp/venv/bin/python
read -r OK COL TOP GUID < <(python3 -c "import json;h=json.load(open('$D/home.json'));print(h.get('confident'),h.get('home_colour'),h.get('home_is_top'),h.get('match_guid'))")
[ "$OK" = True ] || { echo "$2: хозяева не определены"; exit 1; }
YT=$(python3 -c "import json;print(next(v['yt'] for v in json.load(open('$R/plan.json')) if v['slug']=='$2'))")
SPORTX_TOKEN=$($P -c "from full_run import site;print(site.token(refresh=True))"|tail -1) $P upload_analytics.py $D/stats.json --match-guid $GUID --home-colour "$COL" --video-url "https://www.youtube.com/watch?v=$YT" --version $(date +%F) 2>&1 | tail -1 | cut -c1-70
