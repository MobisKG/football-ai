#!/bin/bash
# Команды по выбору формы: apply_choice.sh <slug> "<имя A>" "<имя B>"
# Трек → A или B, если за него не меньше 2/3 снимков трека; иначе без команды.
set -e
S=/Users/doni/Documents/sport/football/scripts; P=~/.cache/fb-ytdlp/venv/bin/python
D=/Users/doni/Documents/sport/football/results/run_2026-09-21/kitfix/$1
cd $D
python3 - "$S/kitchoice/$1.choice.json" <<'PY'
import json,sys
ch=json.load(open(sys.argv[1])); n=json.load(open("numbers.json")); a=b=u=0
for t,v in n.items():
    c=ch.get(t); v["kit"]=""
    if c:
        tot=sum(c.values())
        if c["A"]>=2/3*tot: v["kit"]="альфа"; a+=1
        elif c["B"]>=2/3*tot: v["kit"]="бета"; b+=1
        else: u+=1
json.dump(n,open("numbers_choice.json","w"),ensure_ascii=False)
print("треков A %d, B %d, спорных %d, без снимков %d"%(a,b,u,len(n)-a-b-u))
PY
rm -f pos_final.json
$P $S/team_from_kit.py pos.json numbers_choice.json pos_final.json --kit-map "альф:$2;бета:$3" >/dev/null
$P -c "
import json;pos=json.load(open('pos.json'));json.dump({'fps':pos['fps'],'team_of':pos['team_of'],'numbers':{},'player_of':{},'frames':[{'players':[[int(t),x,y,True] for t,x,y in f['players']]} for f in pos['frames']]},open('met_dummy.json','w'))"
$P $S/apply_numbers.py pos_final.json met_dummy.json numbers.json --keep-teams >/dev/null 2>&1
$P $S/reid_cluster.py pos_final.json met_dummy.json osnet.json >/dev/null 2>&1
$P $S/match_stats.py pos_final.json ball.json stats.json --name "$1" > stats.txt 2>&1
sed -n 2,3p stats.txt
