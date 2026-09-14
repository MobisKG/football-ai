"""Собирает одностраничный просмотрщик: видео плюс лента событий рядом.

Видео никуда не копируется и не пережимается — страница открывает локальный
файл через выбор в браузере, поэтому смотреть можно оригинал в полном качестве.
События вшиты прямо в HTML, чтобы файл был один и работал без сервера.

    python make_viewer.py out/events.json viewer.html
"""
import json, sys

ev_path, out_path = sys.argv[1], sys.argv[2]
# третий аргумент — папка с фотографиями треков (рядом с готовой страницей),
# четвёртый — опись tracks.json из extract_crops.py
crops_dir = sys.argv[3] if len(sys.argv) > 3 else ""
tracks = json.load(open(sys.argv[4])) if len(sys.argv) > 4 else {}
data = json.load(open(ev_path))

HTML = """<title>%(title)s — сверка событий</title>
<style>
  :root{--bg:#f7f7f5;--fg:#1a1a1a;--mut:#6b6b6b;--line:#e0e0dc;--card:#fff;
        --red:#e03131;--blue:#1c7ed6;--hi:#fff3bf}
  @media (prefers-color-scheme:dark){:root:not([data-theme=light]){
        --bg:#16161a;--fg:#ececec;--mut:#9a9a9a;--line:#2c2c33;--card:#1e1e24;--hi:#3b3416}}
  :root[data-theme=dark]{--bg:#16161a;--fg:#ececec;--mut:#9a9a9a;--line:#2c2c33;--card:#1e1e24;--hi:#3b3416}
  body{background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,system-ui,sans-serif;margin:0}
  header{padding:14px 18px;border-bottom:1px solid var(--line)}
  h1{font-size:17px;margin:0 0 4px}
  .sum{color:var(--mut);font-size:13px}
  .wrap{display:flex;gap:14px;padding:14px;align-items:flex-start;flex-wrap:wrap}
  .left{flex:1 1 560px;min-width:320px}
  video{width:100%%;background:#000;border-radius:8px}
  .pick{padding:10px;border:1px dashed var(--line);border-radius:8px;margin-bottom:10px}
  .now{margin-top:10px;padding:12px;background:var(--card);border:1px solid var(--line);border-radius:8px}
  .now b{font-size:19px}
  .bar{display:flex;height:12px;border-radius:6px;overflow:hidden;margin-top:8px}
  .bar i{display:block}
  .bar{background:#888}
  aside{flex:0 1 380px;min-width:280px;max-height:78vh;overflow:auto;
        background:var(--card);border:1px solid var(--line);border-radius:8px}
  table{border-collapse:collapse;width:100%%}
  td{padding:6px 10px;border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap}
  tr:hover td{background:var(--hi)}
  tr.on td{background:var(--hi);font-weight:600}
  .t{color:var(--mut);font-variant-numeric:tabular-nums}
  .red{color:var(--red)} .blue{color:var(--blue)}
  .k{display:inline-block;min-width:64px}
  .tid{color:inherit;text-decoration:underline dotted;cursor:pointer}
  .tid:hover{text-decoration:underline}
</style>
<header>
  <h1>%(title)s</h1>
  <div class=sum>длительность %(dur)s · мяч найден %(ball)s %% кадров ·
    пасов %(passes)d · потерь %(losses)d · смен владельца %(changes)d</div>
</header>
<div class=wrap>
  <div class=left>
    <div class=pick>Выбери файл матча на диске — он никуда не загружается, читается прямо с него:
      <input type=file id=f accept="video/*"></div>
    <video id=v controls></video>
    <div class=now>
      <div class=t id=clock>00:00</div>
      <b id=cur>—</b>
      <div class=bar id=bar><i style="width:50%%;background:var(--red)"></i><i style="flex:1;background:var(--blue)"></i></div>
      <div class=t id=pos>владение —</div>
    </div>
  </div>
  <aside><table id=tb></table></aside>
</div>
<script>
const DATA = %(json)s;
const TRACKS = %(tracks)s, CROPS = %(crops)s, TEAMCOL = %(teamcol)s;
const colOf = t => TEAMCOL[t] || '#9a9a9a';
const v=document.getElementById('v'), tb=document.getElementById('tb');
const NAMES = %(names)s;
// В ленте показываем номер на футболке, если он прочитан: номер трека ничего
// не говорит человеку, а номер игрока узнаётся с первого взгляда.
const tidLink = t => {
  const m = TRACKS[t];
  const lab = (m && m.number!==undefined && m.number!==null) ? ('№'+m.number) : ('#'+t);
  return '<span class=tid data-t="'+t+'" title="трек #'+t+'">'+lab+'</span>';
};
// Фотографии открываем в отдельном окне: в ленте им места нет, а опознавать
// игрока удобнее на нескольких кадрах сразу. Пути строим от адреса этой
// страницы, иначе пустое окно не найдёт файлы рядом с ней.
function showTrack(t){
  const m=TRACKS[t];
  const w=window.open('','_t'+t,'width=900,height=560');
  if(!m){w.document.write('<p style="font:14px sans-serif">Для трека #'+t+' снимков нет.</p>');return}
  const col=colOf(m.team), name=NAMES[m.team]||'не игрок';
  const imgs=m.shots.map(f=>'<img src="'+new URL(CROPS+'/'+f, location.href).href+
      '" style="height:300px;border-radius:6px;border:3px solid '+col+'">').join(' ');
  const num = (m.number!==undefined && m.number!==null) ? (' &middot; номер <b>'+m.number+'</b>') : '';
  w.document.write('<title>Трек #'+t+'</title>'+
    '<body style="font:15px -apple-system,sans-serif;background:#111;color:#eee;padding:16px">'+
    '<h2 style="margin:0 0 4px">Трек #'+t+num+' &middot; <span style="color:'+col+'">'+name+'</span></h2>'+
    '<div style="color:#999;margin-bottom:12px">в кадре '+m.frames_seen+' кадров &middot; '+
    'снимки выбраны там, где игрок ближе всего к камере</div>'+
    '<div style="display:flex;gap:10px;flex-wrap:wrap">'+imgs+'</div>');
}
document.addEventListener('click',ev=>{
  const el=ev.target.closest('.tid'); if(!el) return;
  ev.stopPropagation(); showTrack(el.dataset.t);
});
document.getElementById('f').onchange=e=>{v.src=URL.createObjectURL(e.target.files[0]);v.play()};
const mmss=s=>String(Math.floor(s/60)).padStart(2,'0')+':'+String(Math.floor(s%%60)).padStart(2,'0');
DATA.events.forEach((e,i)=>{
  const tr=tb.insertRow();tr.id='e'+i;
  tr.innerHTML='<td class=t>'+mmss(e.t)+'</td>'+
    '<td><span class=k>'+e.kind+'</span> <span style="color:'+colOf(e.team_id)+'">'+e.team+'</span></td>'+
    '<td class=t>'+tidLink(e.from_track)+'&rarr;'+tidLink(e.to_track)+'</td>';
  tr.onclick=()=>{v.currentTime=e.t};
});
let last=-1;
v.ontimeupdate=()=>{
  const t=v.currentTime;
  document.getElementById('clock').textContent=mmss(t);
  let i=-1; for(let k=0;k<DATA.events.length;k++){ if(DATA.events[k].t<=t) i=k; else break; }
  if(i===last) return; last=i;
  document.querySelectorAll('tr.on').forEach(r=>r.classList.remove('on'));
  const cur=document.getElementById('cur'), pos=document.getElementById('pos');
  if(i<0){cur.textContent='—';pos.textContent='владение —';return}
  const e=DATA.events[i], row=document.getElementById('e'+i);
  row.classList.add('on'); row.scrollIntoView({block:'nearest'});
  cur.innerHTML=e.kind+' &middot; мяч у <span style="color:'+colOf(e.team_id)+'">'+e.team+'</span>'+
    ' &middot; пасов '+e.passes_total+', потерь '+e.losses_total;
  pos.textContent='владение: '+NAMES[0]+' '+e.possession.A+' %%, '+NAMES[1]+' '+e.possession.B+' %%';
  document.getElementById('bar').innerHTML='<i style="width:'+e.possession.A+'%%;background:'+colOf(0)+'"></i>'+
    '<i style="flex:1;background:'+colOf(1)+'"></i>';
};
</script>"""

# Цвет подписи берём из названия команды. Раньше он зависел от номера группы,
# и после перехода на имена по форме «синие» оказались написаны красным.
COLOURS = {"красные": "#e03131", "бордовые": "#c2255c", "синие": "#1c7ed6",
           "тёмно-синие": "#1864ab", "голубые": "#4dabf7", "зелёные": "#2f9e44",
           "жёлтые": "#e8b105", "оранжевые": "#e8590c", "белые": "#dee2e6",
           "чёрные": "#495057", "серые": "#868e96", "фиолетовые": "#7048e8"}
_ev_names = {}
for _e in data["events"]:
    _ev_names[_e["team_id"]] = _e["team"]
NAMES_JS = {str(k): v for k, v in _ev_names.items()}
NAMES_JS.setdefault("0", "команда A"); NAMES_JS.setdefault("1", "команда B")
NAMES_JS["-1"] = "не игрок"
TEAM_COLOURS = {k: COLOURS.get(v.split(" (")[0], "#868e96") for k, v in NAMES_JS.items()}

s = data["summary"]
open(out_path, "w").write(HTML % {
    "title": data["title"],
    "dur": "%d:%02d" % divmod(int(data["duration_sec"]), 60),
    "ball": s["ball_found_pct"],
    "passes": s["passes"], "losses": s["losses"], "changes": s["owner_changes"],
    "json": json.dumps(data, ensure_ascii=False),
    "tracks": json.dumps(tracks, ensure_ascii=False),
    "teamcol": json.dumps(TEAM_COLOURS, ensure_ascii=False),
    "names": json.dumps(NAMES_JS, ensure_ascii=False),
    "crops": json.dumps(crops_dir),
})
print("готово:", out_path)
