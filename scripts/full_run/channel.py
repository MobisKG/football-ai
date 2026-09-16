"""Список новых видео канала — на управляющей машине, до аренды.

    python -m full_run.channel [--channel @dordoifood_35] [--limit 60] [--json файл]

Что считается новым: видео канала, которого нет ни в одном разобранном прогоне
(results/*/state.json, results/*/jobs.json) и у которого на сайте ещё нет
аналитики. Ничего не арендует: yt-dlp работает локально (если его нет, скрипт
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
    cmd = ytdlp_cmd() + ["--flat-playlist", "--dump-single-json", "--playlist-end", str(limit), url]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0: raise RuntimeError("yt-dlp: " + (p.stderr or "")[-300:])
    d = json.loads(p.stdout)
    out = []
    for e in d.get("entries", []):
        if not e.get("id"): continue
        out.append({"yt": e["id"], "title": (e.get("title") or "").strip(),
                    "duration_s": int(e.get("duration") or 0),
                    "date": (e.get("upload_date") or e.get("timestamp") or "")})
    return out

def known_videos():
    """Видео, уже разобранные в прошлых прогонах: results/*/state.json и results/*/jobs.json."""
    seen = {}
    for f in glob.glob(os.path.join(ROOT, "results", "*", "jobs.json")):
        try:
            for j in json.load(open(f)):
                if j.get("yt"): seen[j["yt"]] = {"slug": j.get("slug"), "where": os.path.basename(os.path.dirname(f))}
        except Exception: pass
    for f in glob.glob(os.path.join(ROOT, "results", "*", "matches.json")):
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

def new_videos(url=CHANNEL, limit=60):
    seen = known_videos(); out = []
    for v in list_channel(url, limit):
        if v["yt"] in seen:
            v["known_as"] = seen[v["yt"]]; continue
        v["slug"] = slugify(v["title"], v["yt"])
        out.append(v)
    return out

def main():
    opt = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
    url = opt("--channel", CHANNEL)
    if url.startswith("@"): url = "https://www.youtube.com/%s/videos" % url
    vids = new_videos(url, int(opt("--limit", 60)))
    total = sum(v["duration_s"] for v in vids)
    print("новых видео: %d, суммарно %.1f ч" % (len(vids), total / 3600))
    for v in vids:
        print("  %-28s %s  %4d мин  %s" % (v["slug"], v["yt"], v["duration_s"] // 60, v["title"][:60]))
    path = opt("--json")
    if path:
        json.dump(vids, open(path, "w"), ensure_ascii=False, indent=1); print("список:", path)

if __name__ == "__main__":
    main()
