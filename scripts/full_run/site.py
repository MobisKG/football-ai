"""Сайт cp.sportx.kg: токен с самообновлением и загрузка статистики.

Токен протухает («Ваша сессия истекла»), поэтому здесь он всегда добывается
заново при отказе: логин из SPORTX_USER/SPORTX_PASS (переменные окружения или
строки в ~/Documents/sport/.env либо football/.env). Свежий токен кладётся в
~/.cache/fb/token, чтобы не логиниться на каждую игру.
"""
import json, os, re, time, urllib.error, urllib.request

BASE = os.environ.get("SPORTX_BASE", "https://cp.sportx.kg").rstrip("/")
CACHE = os.path.expanduser("~/.cache/fb/token")
ENV_FILES = ["~/Documents/sport/.env", os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), ".env")]
UA = {"User-Agent": "curl/8.4.0"}

def _env(name):
    v = os.environ.get(name)
    if v: return v.strip()
    for f in ENV_FILES:
        try: txt = open(os.path.expanduser(f)).read()
        except OSError: continue
        m = re.search(r"^%s=(.+)$" % name, txt, re.M)
        if m: return m.group(1).strip()
        # логин мог остаться в файле строкой curl ... -d '{"username":"…","password":"…"}'
        key = {"SPORTX_USER": "username", "SPORTX_PASS": "password"}.get(name)
        if key:
            m = re.search(r'"%s\\?":\\?"([^"\\]+)' % key, txt)
            if m: return m.group(1)
    return None

def _req(path, data=None, token=None, method=None, timeout=60):
    h = dict(UA); h["Content-Type"] = "application/json"
    if token: h["Authorization"] = "Token " + token
    req = urllib.request.Request(BASE + "/" + path.lstrip("/"),
                                 data=json.dumps(data).encode() if data is not None else None,
                                 method=method or ("POST" if data is not None else "GET"), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        t = r.read().decode()
        return json.loads(t) if t.strip() else {}

def login():
    u, p = _env("SPORTX_USER"), _env("SPORTX_PASS")
    if not (u and p): raise RuntimeError("нет SPORTX_USER/SPORTX_PASS для входа на сайт")
    r = _req("v1/login/", {"username": u, "password": p})
    tok = (r.get("data") or r).get("token")
    if not tok: raise RuntimeError("вход не дал токен: " + json.dumps(r)[:200])
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as f: f.write(tok)
    os.chmod(CACHE, 0o600)
    return tok

def token(refresh=False):
    if not refresh:
        t = _env("SPORTX_TOKEN")
        if t: return t
        try:
            if time.time() - os.path.getmtime(CACHE) < 12 * 3600: return open(CACHE).read().strip()
        except OSError: pass
    return login()

def post_analytics(payload, tries=2):
    """Отправить аналитику; при «сессия истекла» — перелогиниться и повторить."""
    t = token()
    for attempt in range(tries):
        try:
            r = _req("v1/match/analytics", payload, token=t)
            if isinstance(r, dict) and "истекла" in json.dumps(r, ensure_ascii=False):
                t = token(refresh=True); continue
            return r
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            if e.code in (401, 403) and attempt + 1 < tries:
                t = token(refresh=True); continue
            raise RuntimeError("HTTP %d %s" % (e.code, body))
    raise RuntimeError("сайт не принял аналитику")

def analytics(match_guid, timeout=40):
    """Что сейчас лежит на сайте по игре (публичное чтение)."""
    try:
        r = _req("v1/match/analytics?match_guid=" + match_guid, timeout=timeout)
        return (r.get("data") or r)
    except Exception:
        return {}

def has_analytics(match_guid):
    d = analytics(match_guid) or {}
    return bool((d.get("data") or {}).get("possession"))
