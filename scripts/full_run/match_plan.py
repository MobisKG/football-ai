"""Список скачанных видео → plan.json и rosters.json для full_run.

Для каждого видео ищет матч на сайте по названию (транслит + похожесть строк)
среди матчей известных команд в окне дат. Команды берутся из rosters.json
прошлых прогонов.

    python -m full_run.match_plan results/ingest_2026-11-24.json OUTDIR \
        --from 2026-11-17 --to 2026-11-24 --rosters '/opt/football-runner/results/run_*/rosters.json'

Печатает таблицу: счёт совпадения (2.00 — оба названия совпали), второй лучший
счёт, видео и найденный матч. Меньше 1.5 или второй близко к первому — проверь
глазами и поправь plan.json руками. Видео, у которых матч уже со статистикой
на сайте, помечаются «УЖЕ ЕСТЬ» и в план не идут.
"""
import argparse, datetime, difflib, glob, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import full_run.site as site

TR = str.maketrans({'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z',
                    'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r',
                    'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sh',
                    'ы': 'y', 'э': 'e', 'ю': 'yu', 'я': 'ya', 'ү': 'u', 'ө': 'o', 'ң': 'n', 'ъ': '', 'ь': ''})

def norm(x):
    return re.sub(r'[^a-z0-9]', '', x.lower().replace('фк', '').translate(TR).replace('fc', ''))

def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b: return 0.0
    if a in b or b in a: return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()

def sides(title):
    t = re.split(r'[,|]', title)[0]
    t = re.sub(r'(?i)центральный матч', '', t).replace('“', '').replace('”', '').replace('"', '')
    p = re.split(r'(?i)\s+vs\.?\s+|\s+-\s+|\s+—\s+', t)
    return (p[0], p[1]) if len(p) > 1 else (t, t)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('videos'); ap.add_argument('outdir')
    ap.add_argument('--from', dest='d0', required=True); ap.add_argument('--to', dest='d1', required=True)
    ap.add_argument('--rosters', action='append', required=True, help='glob файлов rosters.json прошлых прогонов')
    a = ap.parse_args()
    d0 = datetime.datetime.fromisoformat(a.d0); d1 = datetime.datetime.fromisoformat(a.d1) + datetime.timedelta(days=1)
    guids = set()
    for g in a.rosters:
        for f in glob.glob(g):
            try: guids |= set(json.load(open(f)))
            except Exception: pass
    tok = site.token(refresh=True)
    ms = {}
    for g in guids:
        try: r = site._req('v1/match/team?team_guid=' + g, token=tok)
        except Exception: continue
        for x in r.get('data') or []:
            m = x['match']
            if 'Архив' in m['season']: continue
            d = datetime.datetime.strptime(m['match_datetime'], '%d.%m.%Y %H:%M:%S')
            if d0 <= d < d1: ms[m['match_guid']] = x
    print('команд %d, матчей в окне %d' % (len(guids), len(ms)), file=sys.stderr)
    plan = []
    for v in json.load(open(a.videos)):
        h0, a0 = sides(v['title'])
        best = sorted(((max(sim(h0, x['home_team']['team']['title']) + sim(a0, x['away_team']['team']['title']),
                            sim(h0, x['away_team']['team']['title']) + sim(a0, x['home_team']['team']['title'])), g, x)
                       for g, x in ms.items()), key=lambda z: -z[0])
        if not best: print('нет матчей для', v['slug']); continue
        sc, g, x = best[0]; sc2 = best[1][0] if len(best) > 1 else 0
        H, A, m = x['home_team'], x['away_team'], x['match']
        mark = '' if sc >= 1.5 and sc - sc2 >= 0.3 else '  <-- ПРОВЕРЬ'
        print('%.2f %.2f | %-45s | %s - %s, %s тур, %s, %d:%d%s' % (sc, sc2, v['title'][:45], H['team']['title'],
              A['team']['title'], m['tour_number'], m['match_datetime'][:10], H['goals'], A['goals'], mark))
        if site.has_analytics(g): print('   УЖЕ ЕСТЬ статистика на сайте, пропускаю'); continue
        plan.append(dict(v, field=None, match_guid=g, home=H['team']['title'], away=A['team']['title'],
                         home_guid=H['team']['guid'], away_guid=A['team']['guid'],
                         home_goals=H['goals'], away_goals=A['goals']))
    ros = {}
    for v in plan:
        for s in ('home', 'away'):
            g = v[s + '_guid']
            if g in ros: continue
            r = site._req('v1/player/?page_size=100&team_guid=' + g, token=tok)['data']['results']
            ros[g] = {'title': v[s], 'numbers': sorted({p['player_number'] for p in r if p.get('player_number')})}
    os.makedirs(a.outdir, exist_ok=True)
    json.dump(plan, open(os.path.join(a.outdir, 'plan.json'), 'w'), ensure_ascii=False, indent=1)
    json.dump(ros, open(os.path.join(a.outdir, 'rosters.json'), 'w'), ensure_ascii=False, indent=1)
    print('план: %d игр, %d команд -> %s' % (len(plan), len(ros), a.outdir))

if __name__ == '__main__':
    main()
