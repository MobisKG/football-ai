"""Ночной пакетный прогон: N машин RunPod разбирают очередь видео, ноутбук
(или мак мини) считает маковские шаги, статистику и грузит на сайт.

    python batch_run.py --matches results/batch_2026-09-14/matches.json --pods 4 [--only slug,slug] [--gpu-step 4]

Состояние в <outdir>/state.json (можно перезапускать: готовые видео и шаги
пропускаются), лог в <outdir>/batch.log, отчёт в <outdir>/REPORT.md.
Каждая машина удаляется, когда очередь пуста; в конце список машин проверяется
ещё раз. Все ожидания с лимитом времени и проверкой, что машина отвечает.
"""
import json, os, sys, time, subprocess, threading, queue, shutil, urllib.request, urllib.error, traceback, re
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
opt = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
MATCHES = os.path.abspath(opt("--matches", os.path.join(ROOT, "results/batch_2026-09-14/matches.json")))
OUT = os.path.dirname(os.path.abspath(MATCHES))
NPODS = int(opt("--pods", 4)); STEP = int(opt("--gpu-step", 4))
ONLY = set(opt("--only", "").split(",")) - {""}
PY = opt("--python", "/private/tmp/claude-501/-Users-doni-Documents-football/7f0d34e7-ac71-4fb5-9950-2123175b3367/scratchpad/venv/bin/python")
if not os.path.exists(PY): PY = sys.executable
KEY = os.path.expanduser(opt("--key", "~/.ssh/id_ed25519_personal"))
# на машину кладём все наши публичные ключи: с ноутбука и с мак мини можно зайти одним и тем же подом
import glob as _glob
PUBKEYS = "\n".join(open(f).read().strip() for f in sorted(_glob.glob(os.path.expanduser("~/.ssh/id_ed25519*.pub"))))
# общий ключ машин друг для друга: видео, которое YouTube не отдаёт одной машине, ей передаёт другая
BATCH_KEY = os.path.join(OUT, "batch_key")
PUBKEYS += "\n" + open(BATCH_KEY + ".pub").read().strip()
PODS = {}                     # tag -> Pod, чтобы находить машину с работающим YouTube
RUNPOD_KEY = [l.split("=", 1)[1].strip() for l in open(os.path.join(ROOT, ".env")) if l.startswith("KEY=")][0]
GPUS = ["NVIDIA A40", "NVIDIA RTX A6000", "NVIDIA GeForce RTX 4090", "NVIDIA L40S", "NVIDIA RTX A5000", "NVIDIA L40"]
IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
VIDEO_TIMEOUT = 3.5 * 3600      # на одно видео на машине
STATE_PATH = os.path.join(OUT, "state.json"); LOG_PATH = os.path.join(OUT, "batch.log")
lock = threading.Lock(); mac_lock = threading.Lock()

def log(*a):
    line = "[%s] %s" % (datetime.now().strftime("%d.%m %H:%M:%S"), " ".join(str(x) for x in a))
    with lock:
        print(line, flush=True)
        open(LOG_PATH, "a").write(line + "\n")

state = json.load(open(STATE_PATH)) if os.path.exists(STATE_PATH) else {"videos": {}, "pods": [], "spent_usd": 0.0}
def save_state():
    with lock:
        json.dump(state, open(STATE_PATH + ".tmp", "w"), ensure_ascii=False, indent=1); os.replace(STATE_PATH + ".tmp", STATE_PATH)
def vst(slug):
    return state["videos"].setdefault(slug, {"status": "queued", "attempts": 0, "gpu_min": 0, "log": []})

# ---------------- RunPod ----------------
def rp(method, path, body=None, timeout=60):
    req = urllib.request.Request("https://rest.runpod.io/v1" + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers={"Authorization": "Bearer " + RUNPOD_KEY, "Content-Type": "application/json", "User-Agent": "curl/8.4.0"})   # Cloudflare режет Python-urllib (403, код 1010)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t = r.read().decode(); return json.loads(t) if t.strip() else {}
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %d %s" % (e.code, e.read().decode()[:300])}
    except Exception as e:
        return {"error": str(e)[:300]}

class PodDead(Exception): pass

class Pod:
    def __init__(self, tag):
        self.tag = tag; self.id = None; self.ip = None; self.port = None; self.gpu = None; self.cost = 0.0; self.t0 = None; self.ready = False
        self.yt_ok = False; PODS[tag] = self
    def start_log_saver(self):
        """log_saver.sh копирует непустые логи в *.last: повтор попытки обнуляет логи, причина падения остаётся"""
        # проверка по полной строке процесса: pgrep -f нашёл бы саму эту команду (в ней есть путь к скрипту) и не запустил бы сохранение
        self.ssh('[ "$(ps -eo args | grep -c "^/bin/bash /root/fb/log_saver[.]sh")" = 0 ] && (nohup /bin/bash /root/fb/log_saver.sh >/dev/null 2>&1 &); true', raise_dead=False)
    def install_batch_key(self):
        self.scp(BATCH_KEY, self.remote("/root/.ssh/batch_key"))
        self.ssh("chmod 600 /root/.ssh/batch_key; grep -q batch-pods /root/.ssh/authorized_keys || cat >> /root/.ssh/authorized_keys <<'EOK'\n%s\nEOK" % open(BATCH_KEY + ".pub").read().strip(), raise_dead=False)
    def fetch_from(self, other, slug):
        """скопировать out/<slug>.mp4 с другой машины напрямую"""
        rc, out = self.ssh("cd /root/fb && rm -f out/%s.mp4 && scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i /root/.ssh/batch_key -P %d root@%s:/root/fb/out/%s.mp4 out/ && ls -la out/%s.mp4" % (slug, other.port, other.ip, slug, slug), timeout=1500)
        return rc == 0
    def create(self):
        tried = []
        for attempt in range(8):
            gpus = [g for g in GPUS if g not in tried] or GPUS
            body = {"name": "fb-batch-%s" % self.tag, "imageName": IMAGE, "gpuTypeIds": gpus, "gpuCount": 1,
                    "containerDiskInGb": 60, "volumeInGb": 0, "ports": ["22/tcp"], "cloudType": "SECURE",
                    "env": {"PUBLIC_KEY": PUBKEYS}}
            r = rp("POST", "/pods", body)
            if not r.get("id"):
                log(self.tag, "под не создан:", (r.get("error") or str(r))[:200]); time.sleep(120); continue
            self.id = r["id"]; self.gpu = (r.get("machine") or {}).get("gpuTypeId"); self.cost = float(r.get("costPerHr") or 0); self.t0 = time.time()
            with lock: state["pods"].append({"id": self.id, "gpu": self.gpu, "cost": self.cost, "t0": self.t0, "tag": self.tag})
            save_state(); log(self.tag, "под", self.id, self.gpu, "%.2f $/ч" % self.cost)
            if self.wait_ready(): self.ready = True; return True
            log(self.tag, "машина не поднялась, удаляю и беру другой тип"); self.delete(); tried.append(self.gpu)
        return False
    def wait_ready(self):
        for i in range(42):                       # 7 минут на IP и порт
            r = rp("GET", "/pods/" + self.id)
            pm = r.get("portMappings") or {}
            if r.get("publicIp") and pm.get("22"):
                self.ip, self.port = r["publicIp"], int(pm["22"]); break
            time.sleep(10)
        else:
            return False
        log(self.tag, "адрес", self.ip, self.port)
        for i in range(14):                       # 3,5 минуты на ssh и CUDA
            rc, out = self.ssh('python -c "import torch;assert torch.cuda.is_available();print(torch.cuda.get_device_name(0))"', timeout=40, raise_dead=False)
            if rc == 0 and out.strip(): log(self.tag, "CUDA:", out.strip()); return True
            time.sleep(15)
        return False
    def ssh(self, cmd, timeout=120, raise_dead=True):
        # один сброс соединения — не смерть машины: пробуем трижды с паузой
        for attempt in range(3):
            p = self._ssh(cmd, timeout)
            if p.returncode != 255: break
            time.sleep(10)
        if p.returncode == 255 and raise_dead: raise PodDead(p.stderr.strip()[:200])
        return p.returncode, p.stdout + p.stderr
    def _ssh(self, cmd, timeout):
        return subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                            "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-o", "BatchMode=yes",
                            "-i", KEY, "-p", str(self.port), "root@%s" % self.ip, cmd], capture_output=True, text=True, timeout=timeout + 30)
    def attach(self, rec):
        """переподхват машины, поднятой прошлым запуском скрипта"""
        self.id, self.gpu, self.cost, self.t0 = rec["id"], rec.get("gpu"), float(rec.get("cost") or 0), rec.get("t0") or time.time()
        r = rp("GET", "/pods/" + self.id); pm = r.get("portMappings") or {}
        if not (r.get("publicIp") and pm.get("22")): return False
        self.ip, self.port = r["publicIp"], int(pm["22"])
        rc, out = self.ssh("python -c 'import torch;assert torch.cuda.is_available()' && echo ATTACH_OK; grep -q SETUP_OK /root/fb/setup.log 2>/dev/null && echo SETUP_DONE", timeout=60, raise_dead=False)
        if "ATTACH_OK" not in out: return False
        log(self.tag, "переподхвачен под", self.id, self.gpu, self.ip, self.port, "(установка готова)" if "SETUP_DONE" in out else "(установку повторю)")
        # задачи прошлых запусков скрипта продолжали бы работать параллельно новым — убираем все
        self.ssh("pkill -f '[r]un_video.sh' ; pkill -f '[r]un_gpu_match.sh' ; pkill -f '[a]nalyze_match.py' ; pkill -f '[b]all_yolo.py' ; "
                 "pkill -f '[e]xtract_crops2.py' ; pkill -f '[r]ead_numbers2.py' ; pkill -f '[r]eid_osnet.py' ; pkill -f '[s]coreboard.py' ; pkill -f '[y]t-dlp' ; "
                 "sleep 3; rm -f /root/fb/out/*.DONE /root/fb/out/*.FAILED; true", raise_dead=False)
        if "SETUP_DONE" not in out: self.setup()      # прошлый запуск оборвал установку — повторяем, шаги идемпотентны
        else: self.scp([os.path.join(HERE, f) for f in os.listdir(HERE) if f.endswith((".py", ".sh", ".yaml"))], self.remote("/root/fb/"))   # свежие скрипты
        self.install_batch_key(); self.start_log_saver()
        self.ready = True; return True
    def scp(self, src, dst, timeout=1800):
        p = subprocess.run(["scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                            "-o", "ConnectTimeout=30", "-i", KEY, "-P", str(self.port), "-q", "-r"] + (src if isinstance(src, list) else [src]) + [dst],
                           capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0: raise RuntimeError("scp: " + p.stderr.strip()[:200])
    def remote(self, path): return "root@%s:%s" % (self.ip, path)
    def setup(self):
        self.ssh("mkdir -p /root/fb/weights /root/fb/out")
        files = [os.path.join(HERE, f) for f in os.listdir(HERE) if f.endswith((".py", ".sh", ".yaml"))]
        self.scp(files, self.remote("/root/fb/"))
        self.scp(os.path.join(ROOT, "weights/best_7matches.pt"), self.remote("/root/fb/weights/"))
        rc, out = self.ssh("cd /root/fb && chmod +x *.sh && ./pod_setup.sh > setup.log 2>&1; tail -4 setup.log", timeout=1800)
        if "SETUP_OK" not in out: raise RuntimeError("установка не удалась: " + out[-400:])
        self.install_batch_key(); self.start_log_saver(); log(self.tag, "установка готова")
    def delete(self):
        if not self.id: return
        r = rp("DELETE", "/pods/" + self.id)
        spent = self.cost * (time.time() - self.t0) / 3600
        with lock:
            state["spent_usd"] = round(state.get("spent_usd", 0) + spent, 3)
            for p in state["pods"]:
                if p["id"] == self.id: p["t1"] = time.time(); p["spent"] = round(spent, 3)
        save_state(); log(self.tag, "под", self.id, "удалён (%s), %.2f $ за %.0f мин" % (r.get("error") or "ok", spent, (time.time() - self.t0) / 60))
        self.id = None; self.ready = False

def delete_all_pods():
    r = rp("GET", "/pods")
    pods = r if isinstance(r, list) else (r.get("pods") if isinstance(r, dict) else None) or []
    for p in pods:
        if isinstance(p, dict) and p.get("id"):
            log("удаляю оставшийся под", p["id"], p.get("name")); rp("DELETE", "/pods/" + p["id"])
    r = rp("GET", "/pods"); log("список машин после очистки:", json.dumps(r)[:200])
    return r

# ---------------- одно видео на машине ----------------
def run_on_pod(pod, v):
    slug, yt = v["slug"], v["yt"]; st = vst(slug)
    # шаблоны в скобках, иначе pkill находит и убивает собственную ssh-сессию (та же ловушка, что в STATE.md)
    # очистка и запуск — отдельными вызовами: в одной строке pkill находил «out/<имя>.log» из команды запуска и убивал сессию
    pod.ssh("pkill -f '[r]un_video.sh %s ' ; pkill -f '[r]un_gpu_match.sh out/%s[.]mp4' ; pkill -f '[o]ut/%s[/.]' ; sleep 2; true" % (slug, slug, slug), raise_dead=False)
    pod.ssh("cd /root/fb && (nohup ./run_video.sh %s %s %d > out/%s.log 2>&1 &) ; sleep 1" % (yt, slug, STEP, slug), raise_dead=True)
    t0 = time.time(); dead = 0; last = ""
    while True:
        time.sleep(60)
        if time.time() - t0 > VIDEO_TIMEOUT:
            pod.ssh("pkill -f '[r]un_video.sh %s' ; pkill -f '[r]un_gpu_match.sh .*%s' ; pkill -f '[o]ut/%s' " % (slug, slug, slug), raise_dead=False)
            raise RuntimeError("превышен лимит времени на видео")
        try:
            rc, out = pod.ssh("cd /root/fb && ls out/%s.DONE out/%s.FAILED 2>/dev/null; tail -n 1 out/%s.run.log 2>/dev/null; tail -n 1 out/%s.log" % (slug, slug, slug, slug), timeout=60)
            dead = 0
        except (PodDead, subprocess.TimeoutExpired) as e:
            dead += 1; log(pod.tag, slug, "машина не отвечает (%d)" % dead)
            if dead >= 6: raise PodDead("машина не отвечает 6 минут")
            continue
        tail = out.strip().splitlines()
        if tail and tail[-1] != last:
            last = tail[-1]; log(pod.tag, slug, "|", last[-150:])
            if "разбор" in last or "видео:" in last: pod.yt_ok = True
        if ".DONE" in out: break
        if ".FAILED" in out: raise RuntimeError("шаг на машине упал: " + (tail[-1] if tail else "")[-200:])
    st["gpu_min"] = round(st.get("gpu_min", 0) + (time.time() - t0) / 60)
    d = os.path.join(OUT, slug); os.makedirs(d, exist_ok=True)
    pod.scp(pod.remote("/root/fb/out/%s.tar.gz" % slug), os.path.join(OUT, slug + ".tar.gz"))
    try: pod.scp(pod.remote("/root/fb/out/%s_kits.tar.gz" % slug), os.path.join(OUT, slug + "_kits.tar.gz"))
    except Exception: pass
    subprocess.run(["tar", "xzf", os.path.join(OUT, slug + ".tar.gz"), "-C", OUT], check=True)
    os.remove(os.path.join(OUT, slug + ".tar.gz"))
    log(pod.tag, slug, "результаты скачаны, машина %d мин" % ((time.time() - t0) / 60))

def take_video(pod, q):
    """следующее видео из очереди; если на машине уже лежит скачанный файл какого-то видео — его"""
    have = set()
    if pod.ready:
        try:
            rc, out = pod.ssh("ls /root/fb/out/ 2>/dev/null | grep '[.]mp4$'", timeout=60, raise_dead=False)
            have = {l[:-4] for l in out.split() if l.endswith(".mp4")}
        except Exception: pass
    with q.mutex:
        items = list(q.queue)
        pick = next((v for v in items if v["slug"] in have), None) or (items[0] if items else None)
        if pick is not None: q.queue.remove(pick)
    return pick

def relay_video(donor, pod, v):
    """донор качает видео с YouTube (параллельно своей работе), получатель забирает его по scp"""
    slug, yt = v["slug"], v["yt"]
    try:
        rc, out = donor.ssh("cd /root/fb && ([ -s out/%s.mp4 ] || yt-dlp -q --no-warnings -f 299/137/'bv[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]' --no-part --merge-output-format mp4 -o out/%s.mp4 'https://www.youtube.com/watch?v=%s') && ls -la out/%s.mp4" % (slug, slug, yt, slug), timeout=1500)
        if rc != 0: log(donor.tag, "донор не смог скачать", slug, out[-150:]); return False
        ok = pod.fetch_from(donor, slug)
        donor.ssh("rm -f /root/fb/out/%s.mp4" % slug, raise_dead=False)
        return ok
    except Exception as e:
        log(donor.tag, "передача видео не удалась:", str(e)[-150:]); return False

# ---------------- маковские шаги ----------------
def sh(cmd, cwd=None, timeout=1800):
    p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)

def scoreboard_ok(d):
    """Табло годится, если прочитаны оба названия и часы видны хотя бы в трети чтений.
    Иначе (нет плашки на записи, плашка в другом месте) весь матч считался бы паузой:
    на пробном тайме игровое время вышло 3 секунды."""
    p = os.path.join(d, "scoreboard.json")
    if not os.path.exists(p): return False
    try: b = json.load(open(p))
    except Exception: return False
    reads = b.get("reads") or []; vis = sum(1 for r in reads if r.get("visible") and r.get("clock"))
    ok = all(b.get("teams") or [None, None]) and reads and vis >= 0.3 * len(reads)
    if not ok and not os.path.exists(os.path.join(d, "scoreboard_bad.json")):
        os.rename(p, os.path.join(d, "scoreboard_bad.json"))
    return ok

def mac_steps(v):
    slug = v["slug"]; d = os.path.join(OUT, slug); st = vst(slug)
    run = lambda c: sh("nice -n 15 " + c, cwd=d)
    def step(name, cmd, check_file):
        if os.path.exists(os.path.join(d, check_file)) and name != "stats": return
        rc, out = run(cmd)
        open(os.path.join(d, "mac_%s.log" % name), "w").write(out)
        if rc != 0 or not os.path.exists(os.path.join(d, check_file)): raise RuntimeError("%s: %s" % (name, out[-300:]))
    S = lambda f: os.path.join(HERE, f)
    step("ball", "%s %s ball_cands.json ball.json" % (PY, S("ball_track.py")), "ball.json")
    if not os.path.exists(os.path.join(d, "pos_final.json")):
        step("teams", "%s %s pos.json numbers.json pos_final.json" % (PY, S("team_from_kit.py")), "pos_final.json")
        # met_dummy — позиции в «формате метров» (те же пиксели, флаг «на поле» всегда истина)
        pos = json.load(open(os.path.join(d, "pos.json")))
        met = {"fps": pos["fps"], "team_of": pos["team_of"], "numbers": {}, "player_of": {},
               "frames": [{"players": [[int(t), x, y, True] for t, x, y in f["players"]]} for f in pos["frames"]]}
        json.dump(met, open(os.path.join(d, "met_dummy.json"), "w"))
        step("numbers", "%s %s pos_final.json met_dummy.json numbers.json --keep-teams" % (PY, S("apply_numbers.py")), "pos_final.json")
        step("cluster", "%s %s pos_final.json met_dummy.json osnet.json" % (PY, S("reid_cluster.py")), "pos_final.json")
    sb = "--scoreboard scoreboard.json" if scoreboard_ok(d) else ""
    rc, out = run("%s %s pos_final.json ball.json stats.json %s --name \"%s\"" % (PY, S("match_stats.py"), sb, v["title"].replace('"', "")))
    if rc != 0: raise RuntimeError("stats: " + out[-300:])
    open(os.path.join(d, "stats.txt"), "w").write(out)
    rc, out = run("%s %s %s %s" % (PY, S("home_side.py"), d, MATCHES))
    open(os.path.join(d, "mac_home.log"), "w").write(out)
    home = json.load(open(os.path.join(d, "home.json"))) if os.path.exists(os.path.join(d, "home.json")) else {"confident": False, "reason": out[-200:]}
    st["home"] = home
    log(slug, "статистика готова; хозяева:", json.dumps(home, ensure_ascii=False)[:200])
    return home

def upload(v, home):
    slug = v["slug"]; d = os.path.join(OUT, slug); st = vst(slug)
    if v.get("note", "").startswith("два тайма"): st["upload"] = "часть сдвоенной игры, грузится объединённо"; return
    if not v.get("match_guid"): v = dict(v, match_guid=home.get("match_guid")); st["match_guid"] = home.get("match_guid")
    if not v.get("match_guid"): st["upload"] = "нет match_guid"; return
    if not home.get("confident"): st["upload"] = "цвет хозяев не определён: " + str(home.get("reason", "")); return
    cmd = "%s %s stats.json --match-guid %s --home-colour %s %s %s --video-url https://www.youtube.com/watch?v=%s" % (
        PY, os.path.join(HERE, "upload_analytics.py"), v["match_guid"], home["home_colour"],
        "--home-is-top" if home.get("home_is_top") else "", "--scoreboard scoreboard.json" if home.get("home_is_top") is not None and os.path.exists(os.path.join(d, "scoreboard.json")) else "", v["yt"])
    rc, out = sh(cmd, cwd=d)
    open(os.path.join(d, "upload.log"), "w").write(cmd + "\n" + out)
    if rc != 0: st["upload"] = "ошибка загрузки: " + out[-200:]; log(slug, st["upload"]); return
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request("https://cp.sportx.kg/v1/match/analytics?match_guid=" + v["match_guid"], headers={"User-Agent": "curl/8.4.0"}), timeout=60))
        ok = (r.get("data") or r).get("data", {}).get("possession") if isinstance(r, dict) else None
        st["upload"] = "загружено, проверено чтением" if ok else "загружено, но чтение вернуло без владения: " + json.dumps(r)[:120]
    except Exception as e:
        st["upload"] = "загружено, проверка чтением не удалась: %s" % str(e)[:100]
    log(slug, st["upload"])

def finish_video(v):
    """маковские шаги + загрузка; под замком, чтобы ноутбук не перегружать"""
    slug = v["slug"]; st = vst(slug)
    with mac_lock:
        try:
            home = mac_steps(v); upload(v, home); st["status"] = "done"
        except Exception as e:
            st["status"] = "mac_failed"; st["error"] = str(e)[-300:]; log(slug, "маковские шаги упали:", str(e)[-300:])
    save_state()

# ---------------- рабочий поток на одну машину ----------------
orphans = queue.Queue()
def worker(tag, q):
    try:
        _worker(tag, q)
    except Exception:
        log(tag, "ПОТОК УПАЛ:", traceback.format_exc()[-600:])
        p = PODS.get(tag)
        if p and p.id:
            try: p.delete()
            except Exception: pass
def _worker(tag, q):
    pod = Pod(tag); v = None
    while not orphans.empty():
        try: rec = orphans.get_nowait()
        except queue.Empty: break
        try:
            if pod.attach(rec): break
            log(tag, "машина %s из прошлого запуска не отвечает, удаляю" % rec["id"])
        except Exception as e:
            log(tag, "переподхват %s не удался: %s — удаляю" % (rec["id"], str(e)[-150:]))
        pod.id = rec["id"]; pod.t0 = rec.get("t0") or time.time(); pod.cost = float(rec.get("cost") or 0); pod.ready = False; pod.delete()
    while True:
        if v is None:
            v = take_video(pod, q)
            if v is None: break
        st = vst(v["slug"])
        if st["status"] in ("done", "mac_failed") or (st["status"] == "gpu_done"):
            if st["status"] == "gpu_done": finish_video(v)
            v = None; continue
        if st["attempts"] >= 2:
            st["status"] = "failed"; save_state(); log(tag, v["slug"], "две попытки не удались, пропускаю"); v = None; continue
        try:
            if not pod.ready:
                if pod.id: pod.delete()          # недоделанная машина не должна остаться висеть
                if not pod.create(): log(tag, "не удалось получить машину, поток завершён"); q.put(v); break
                pod.setup()
            st["attempts"] += 1; st["status"] = "running"; st["pod"] = pod.id; st["gpu"] = pod.gpu; save_state()
            log(tag, "старт", v["slug"], "попытка", st["attempts"])
            run_on_pod(pod, v)
            st["status"] = "gpu_done"; save_state()
            finish_video(v); v = None
        except PodDead as e:
            log(tag, v["slug"], "машина умерла:", e); st["log"].append("машина умерла: %s" % e); st["status"] = "queued"; st["attempts"] -= 1
            pod.delete(); save_state()
        except Exception as e:
            log(tag, v["slug"], "ошибка:", str(e)[-300:]); st["log"].append(str(e)[-300:]); st["status"] = "queued"; save_state()
            if isinstance(e, RuntimeError) and "установка" in str(e): pod.delete()
            if "видео не скачалось" in str(e):
                st["attempts"] -= 1
                donor = next((p for p in PODS.values() if p is not pod and p.ready and p.yt_ok), None)
                if donor and relay_video(donor, pod, v):
                    log(tag, v["slug"], "видео получено с машины", donor.tag)
                else:
                    # YouTube не отдаёт видео с этого адреса и передать неоткуда — берём другую машину
                    log(tag, "YouTube не отдаёт видео с этого адреса, меняю машину"); pod.delete()
    pod.delete()

def merge_parts(m):
    """Игра, выложенная двумя видео: статистика складывается, голы обоих таймов вместе."""
    slug = m["slug"]; st = vst(slug); d = os.path.join(OUT, slug); os.makedirs(d, exist_ok=True)
    parts = [os.path.join(OUT, p) for p in m["parts"]]
    if not all(os.path.exists(os.path.join(p, "stats.json")) for p in parts):
        st["status"] = "failed"; st["error"] = "не все части готовы"; return
    ss = [json.load(open(os.path.join(p, "stats.json"))) for p in parts]
    cols = list(ss[0]["possession_pct"])
    if any(list(s["possession_pct"]) != cols for s in ss):
        st["status"] = "failed"; st["error"] = "цвета команд в частях различаются: %s" % [list(s["possession_pct"]) for s in ss]; return
    gt = [s["game_time_s"] or 1 for s in ss]
    out = {"match": m["title"], "duration_s": sum(s["duration_s"] for s in ss), "game_time_s": sum(s["game_time_s"] for s in ss),
           "paused_s": sum(s["paused_s"] for s in ss), "ball_visible_pct": round(sum(s["ball_visible_pct"] * s["duration_s"] for s in ss) / sum(s["duration_s"] for s in ss)),
           "possession_pct": {c: round(sum(s["possession_pct"][c] * g for s, g in zip(ss, gt)) / sum(gt)) for c in cols},
           "possession_changes": sum(s["possession_changes"] for s in ss), "teams": {}}
    for c in cols:
        pl = {}
        for s in ss:
            for p in s["teams"][c]["players"]:
                q = pl.setdefault(p["player"], dict(p))
                if q is not p:
                    for k in ("time_on_pitch_s", "touches", "possession_s", "passes"): q[k] = round((q.get(k) or 0) + (p.get(k) or 0), 1)
        out["teams"][c] = {"players": sorted(pl.values(), key=lambda r: -r["time_on_pitch_s"]), "unidentified_time_s": sum(s["teams"][c]["unidentified_time_s"] for s in ss)}
    json.dump(out, open(os.path.join(d, "stats.json"), "w"), ensure_ascii=False, indent=1)
    sbs = [json.load(open(os.path.join(p, "scoreboard.json"))) for p in parts if os.path.exists(os.path.join(p, "scoreboard.json"))]
    if sbs:
        sb = {"teams": sbs[0].get("teams"), "final_score": sbs[-1].get("final_score"), "goals": [g for s in sbs for g in s.get("goals", [])], "reads": sbs[0].get("reads", [])}
        json.dump(sb, open(os.path.join(d, "scoreboard.json"), "w"), ensure_ascii=False)
    with mac_lock:
        rc, o = sh("%s %s %s %s" % (PY, os.path.join(HERE, "home_side.py"), d, MATCHES), cwd=d)
        home = json.load(open(os.path.join(d, "home.json"))) if os.path.exists(os.path.join(d, "home.json")) else {"confident": False, "reason": o[-200:]}
        st["home"] = home; st["status"] = "done"; upload(m, home); save_state()

def write_report(videos):
    L = ["# Отчёт пакетного прогона %s" % datetime.now().strftime("%d.%m.%Y %H:%M"), "",
         "Аренда всего: **%.2f $** (%d машин)." % (state.get("spent_usd", 0), len(state["pods"])), "",
         "| Видео | Матч | Статус | Счёт табло | Счёт судьи | Владение | Игроков с №  | Машина, мин | Загрузка |", "|---|---|---|---|---|---|---|---|---|"]
    for v in videos:
        st = vst(v["slug"]); d = os.path.join(OUT, v["slug"])
        sb = tb = poss = nums = "—"
        try:
            s = json.load(open(os.path.join(d, "stats.json")))
            poss = ", ".join("%s %d%%" % kv for kv in s["possession_pct"].items())
            nums = " / ".join(str(len(t["players"])) for t in s["teams"].values())
        except Exception: pass
        try:
            b = json.load(open(os.path.join(d, "scoreboard.json"))); sb = "%s : %s" % tuple(b["final_score"]) + " (%s)" % " — ".join(b.get("teams") or [])
        except Exception:
            if os.path.exists(os.path.join(d, "scoreboard_bad.json")): sb = "табло не прочитано"
        except Exception: pass
        if v.get("home_goals") is not None: tb = "%s : %s" % (v["home_goals"], v["away_goals"])
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (v["yt"], v["title"], st["status"] + ((" — " + st["error"]) if st.get("error") else ""), sb, tb, poss, nums, st.get("gpu_min", 0), st.get("upload", "—")))
    pend = [v for v in videos if vst(v["slug"]).get("status") == "done" and not str(vst(v["slug"]).get("upload", "")).startswith("загружено") and not vst(v["slug"]).get("upload", "").startswith("часть")]
    if pend:
        L += ["", "## Не загружено — решить утром", "", "Снимки формы: `<slug>_kits.tar.gz` в папке прогона. Команда загрузки (подставить цвет хозяев и, если хозяева в верхней строке табло, `--home-is-top`):", ""]
        for v in pend:
            h = vst(v["slug"]).get("home") or {}
            L.append("- **%s** (%s vs %s): %s" % (v["slug"], v.get("home"), v.get("away"), h.get("reason", vst(v["slug"]).get("upload"))))
            L.append("  `cd %s && %s %s stats.json --match-guid %s --home-colour <цвет> [--home-is-top] --scoreboard scoreboard.json --video-url https://www.youtube.com/watch?v=%s`" % (
                os.path.join(OUT, v["slug"]), PY, os.path.join(HERE, "upload_analytics.py"), h.get("match_guid") or v.get("match_guid") or "<guid>", v["yt"]))
    L += ["", "## Машины", ""] + ["- %s %s: %.2f $/ч, %.2f $" % (p["tag"], p.get("gpu"), p.get("cost", 0), p.get("spent", 0)) for p in state["pods"]]
    L += ["", "## Ошибки по видео", ""] + ["- %s: %s" % (v["slug"], "; ".join(vst(v["slug"])["log"])) for v in videos if vst(v["slug"])["log"]]
    open(os.path.join(OUT, "REPORT.md"), "w").write("\n".join(L) + "\n")

def main():
    # второй экземпляр скрипта удалял бы машины первого: замок на папке прогона
    import fcntl
    global _lockf; _lockf = open(os.path.join(OUT, ".lock"), "w")
    try: fcntl.flock(_lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: sys.exit("уже запущен другой batch_run.py для этой папки")
    M = json.load(open(MATCHES)); videos = [v for v in M["videos"] if "parts" not in v]
    if ONLY: videos = [v for v in videos if v["slug"] in ONLY]
    log("старт: %d видео, %d машин, python %s" % (len(videos), NPODS, PY))
    for p in state["pods"]:
        if not p.get("t1"): orphans.put(p)
    q = queue.Queue()
    for v in videos:
        if vst(v["slug"])["status"] == "running": vst(v["slug"])["status"] = "queued"; vst(v["slug"])["attempts"] = max(0, vst(v["slug"])["attempts"] - 1)
        if vst(v["slug"])["status"] not in ("done", "failed", "mac_failed"): q.put(v)
        elif vst(v["slug"])["status"] == "mac_failed": vst(v["slug"])["status"] = "gpu_done"; q.put(v)
    ths = [threading.Thread(target=worker, args=("m%d" % i, q), daemon=True) for i in range(min(NPODS, max(1, q.qsize())))]
    for i, t in enumerate(ths): t.start(); time.sleep(20)
    for t in ths: t.join()
    delete_all_pods()
    for m in M.get("merged", []): merge_parts(m)
    write_report(videos + M.get("merged", [])); save_state(); log("готово. потрачено %.2f $" % state.get("spent_usd", 0))

if __name__ == "__main__":
    try: main()
    except Exception:
        log("СБОЙ управляющего скрипта:", traceback.format_exc()[-800:]); delete_all_pods(); raise
