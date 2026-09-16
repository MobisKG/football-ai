"""Список новых видео канала — на управляющей машине, до аренды.

    python -m full_run.channel [--channel @dordoifood_35] [--limit 60] [--days 14] [--json файл]

Что считается новым: ролик канала, который не разбирался в прошлых прогонах
(results/*/jobs.json, results/*/matches.json, known.json) И опубликован за
последние --days дней. Без ограничения по дате «новыми» выглядят все старые
записи канала: 16.09 это дало 36 роликов на 22 часа вместо 6 свежих. Ничего не арендует: yt-dlp работает локально (если его нет, скрипт
ставит его в свой кэш ~/.cache/fb-ytdlp).
"""
import json, os, re, subprocess, sys, urllib.request, glob

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CHANNEL = "https://www.youtube.com/@dordoifood_35/videos"
CACHE_VENV = os.path.expanduser("~/.cache/fb-ytdlp/venv")

def ytdlp_cmd():
    """yt-dlp из PATH, из текущего питона или из собственного кэша (ставится один раз)."""
    from shutil import which
    if which("yt-dlp"): return ["yt-dlp"]
    try:
        import yt_dlp  # noqa: F401
        return [sys.executable, "-m", "yt_dlp"]
    except ImportError:
        pass
    py = os.path.join(CACHE_VENV, "bin", "python")
    if not os.path.exists(py):
        print("ставлю yt-dlp в", CACHE_VENV, flush=True)
        subprocess.run([sys.executable, "-m", "venv", CACHE_VENV], check=True)
        subprocess.run([py, "-m", "pip", "install", "-q", "--upgrade", "pip", "yt-dlp"], check=True)
    return [py, "-m", "yt_dlp"]

def list_channel(url=CHANNEL, limit=60, timeout=300):
    """[{yt, title, duration_s, date}] — от новых к старым, без скачивания видео."""
    # approximate_date отдаёт дату публикации прямо в списке канала. Без этого дату
    # пришлось бы спрашивать по каждому ролику отдельно, а такие запросы YouTube с
    # адреса сервера не пускает («Sign in to confirm you're not a bot», 16.09).
    cmd = ytdlp_cmd() + ["--flat-playlist", "--dump-single-json", "--no-warnings",
                         "--extractor-args", "youtubetab:approximate_date",
                         "--playlist-end", str(limit), url]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0: raise RuntimeError("yt-dlp: " + (p.stderr or "")[-300:])
    d = json.loads(p.stdout)
    out = []
    for e in d.get("entries", []):
        if not e.get("id"): continue
        d = e.get("upload_date") or ""
        if not d and e.get("timestamp"):
            from datetime import datetime, timezone
            d = datetime.fromtimestamp(int(e["timestamp"]), timezone.utc).strftime("%Y%m%d")
        out.append({"yt": e["id"], "title": (e.get("title") or "").strip(),
                    "duration_s": int(e.get("duration") or 0), "date": d})
    return out

def results_dirs():
    """Где искать прошлые прогоны: рядом с кодом и там, куда вынесено состояние
    (на сервере /app примонтирован только на чтение, состояние в /data/results)."""
    dirs = [os.path.join(ROOT, "results")]
    env = os.environ.get("FB_RESULTS")
    if env and os.path.abspath(env) not in [os.path.abspath(d) for d in dirs]: dirs.append(env)
    return [d for d in dirs if os.path.isdir(d)]

def known_videos():
    """Уже разобранные ролики: jobs.json и matches.json прошлых прогонов плюс
    known.json — короткий список для сервера, куда папки результатов не копируются."""
    seen = {}
    for d in results_dirs():
        kp = os.path.join(d, "known.json")
        if os.path.exists(kp):
            try: seen.update(json.load(open(kp)))
            except Exception: pass
    for f in sum([glob.glob(os.path.join(d, "*", "jobs.json")) for d in results_dirs()], []):
        try:
            for j in json.load(open(f)):
                if j.get("yt"): seen[j["yt"]] = {"slug": j.get("slug"), "where": os.path.basename(os.path.dirname(f))}
        except Exception: pass
    for f in sum([glob.glob(os.path.join(d, "*", "matches.json")) for d in results_dirs()], []):
        try:
            for j in json.load(open(f)).get("videos", []):
                if j.get("yt"): seen.setdefault(j["yt"], {"slug": j.get("slug"), "where": os.path.basename(os.path.dirname(f))})
        except Exception: pass
    return seen

def has_analytics(match_guid, base="https://cp.sportx.kg", timeout=30):
    if not match_guid: return False
    try:
        req = urllib.request.Request("%s/v1/match/analytics?match_guid=%s" % (base, match_guid),
                                     headers={"User-Agent": "curl/8.4.0"})
        r = json.load(urllib.request.urlopen(req, timeout=timeout))
        d = (r.get("data") or r)
        return bool((d.get("data") or {}).get("possession"))
    except Exception:
        return False

def slugify(title, yt):
    t = re.sub(r"[^\w\s-]", " ", (title or "").lower(), flags=re.U)
    words = [w for w in t.split() if w][:3]
    s = "_".join(words) if words else "video"
    s = re.sub(r"[^a-z0-9_]+", "", s.translate(str.maketrans(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        "abvgdeejzijklmnoprstufhccss_y_eua")))
    return (s or "video") + "_" + yt[:4].lower()

def upload_dates(ids, timeout=600):
    """Даты публикации пачкой: в списке канала их нет, а без них «новыми» выглядят
    и прошлогодние записи (16.09: 36 «новых» вместо 6)."""
    if not ids: return {}
    cmd = ytdlp_cmd() + ["--skip-download", "--no-warnings", "--print", "%(id)s %(upload_date)s"] + \
          ["https://www.youtube.com/watch?v=" + i for i in ids]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = {}
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit(): out[parts[0]] = parts[1]
    return out

def new_videos(url=CHANNEL, limit=60, days=14, with_dates=True):
    """Новые ролики канала: не разбирались раньше и опубликованы за последние
    days дней (days=0 — без ограничения по дате)."""
    seen = known_videos(); cand = []
    for v in list_channel(url, limit):
        if v["yt"] in seen:
            v["known_as"] = seen[v["yt"]]; continue
        v["slug"] = slugify(v["title"], v["yt"])
        cand.append(v)
    if not days or not cand: return cand
    # даты обычно уже пришли со списком; спрашиваем поштучно только то, чего нет
    missing = [v["yt"] for v in cand if not v.get("date")]
    dates = upload_dates(missing) if (with_dates and missing) else {}
    from datetime import datetime, timedelta
    edge = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    out = []
    for v in cand:
        d = v.get("date") or dates.get(v["yt"], "")
        v["date"] = d
        if not d:                       # дату узнать не удалось — берём, лучше лишнее, чем пропуск
            out.append(v); continue
        if d >= edge: out.append(v)
    return out

def write_known(path):
    """Короткий список разобранных роликов: его достаточно, чтобы сервер не брал их снова."""
    json.dump(known_videos(), open(path, "w"), ensure_ascii=False, indent=1)
    return path

def main():
    opt = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    if "--write-known" in sys.argv:
        p = write_known(opt("--write-known"))
        print("разобранных роликов:", len(json.load(open(p))), "->", p); return
    url = opt("--channel", CHANNEL)
    if url.startswith("@"): url = "https://www.youtube.com/%s/videos" % url
    vids = new_videos(url, int(opt("--limit", 60)), days=int(opt("--days", 14)))
    total = sum(v["duration_s"] for v in vids)
    print("новых видео: %d, суммарно %.1f ч" % (len(vids), total / 3600))
    for v in vids:
        print("  %-28s %s  %s  %4d мин  %s" % (v["slug"], v["yt"], v.get("date") or "дата?", v["duration_s"] // 60, v["title"][:60]))
    path = opt("--json")
    if path:
        json.dump(vids, open(path, "w"), ensure_ascii=False, indent=1); print("список:", path)

if __name__ == "__main__":
    main()
