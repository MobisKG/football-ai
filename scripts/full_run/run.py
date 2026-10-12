"""Полный прогон новых видео канала: план → качалка → раздача → разбор → сайт.

    python -m full_run.run [--outdir results/run_ГГГГ-ММ-ДД] [--per-pod 2]
                           [--limit 60] [--only slug,slug] [--stage all|plan|download|fanout|collect]
                           [--dry-run] [--max-usd 30] [--gpu-step 4] [--dl-disk 40]

Порядок и почему он такой (подробно — docs/PIPELINE_v2.md):
 1. План строится на управляющей машине, до всякой аренды: видно, что будет
    разобрано, и сколько это займёт.
 2. Одна дешёвая машина без видеокарты (диск 40 ГБ) качает все видео. YouTube
    пускает не с каждого адреса RunPod: если блокирует, машина заменяется на
    другую и качание продолжается с того же места.
 3. Рабочих машин — по числу игр, делённому на --per-pod (по умолчанию 2 игры
    на машину ≈ 1,5–2 часа). Каждая получает только свои видео, прямо с
    качалки. Как только все получили — качалка удаляется.
 4. Каждая машина работает сама (pod_job.sh под nohup): видеокарта, затем
    процессорные шаги, затем метки готовности. Ноутбук для этого не нужен.
 5. Сторож забирает готовые игры по мере появления, грузит на сайт и удаляет
    машину, когда её игры собраны. В конце список машин проверяется API.

Состояние в <outdir>/state.json: перезапуск продолжает с того же места.
"""
import json, math, os, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE); ROOT = os.path.dirname(SCRIPTS)
sys.path.insert(0, SCRIPTS)
from full_run import core, channel, site                                  # noqa: E402
from full_run.core import Pod, State, log, rp, list_pods, delete_run_pods  # noqa: E402

opt = lambda k, d=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
# на сервере состояние лежит вне кода (/data/results): /app примонтирован только на чтение
RESULTS = os.environ.get("FB_RESULTS", os.path.join(ROOT, "results"))
OUT = os.path.abspath(opt("--outdir", os.path.join(RESULTS, "run_" + time.strftime("%Y-%m-%d"))))
PER_POD = int(opt("--per-pod", 4))   # 4 игры на машину: подготовка машины оплачивается реже; LIMIT = int(opt("--limit", 60)); STEP = int(opt("--gpu-step", 4))
DAYS = int(opt("--days", 14))          # «новое» — за последние N дней; 0 — всё неразобранное
DL_DISK = int(opt("--dl-disk", 40)); MAX_USD = float(opt("--max-usd", 30))
ONLY = set(opt("--only", "").split(",")) - {""}
STAGE = opt("--stage", "all"); DRY = "--dry-run" in sys.argv
VIDEO_LIMIT_S = float(opt("--video-limit-h", 6)) * 3600
SOURCE = opt("--source", "youtube")      # b2: видео берутся из хранилища, качалка не нужна
B2_READ_ENV = opt("--b2-env", "")        # файл с KEY_ID/APPLICATION_KEY ключа только на чтение

os.makedirs(OUT, exist_ok=True)
core.LOG_PATH = os.path.join(OUT, "run.log")
PLAN = os.path.join(OUT, "plan.json")
RELAY = os.path.join(OUT, "relay_key")
st = State(os.path.join(OUT, "state.json"))
# метка этого прогона в именах машин: fb-<метка>-dl, fb-<метка>-w3. По ней в конце
# находятся все свои машины, включая те, что не успели записаться в состояние.
RUN_ID = "".join(c for c in os.path.basename(OUT).replace("run_", "").replace("-", "") if c.isalnum())[:12]

def pubkeys():
    """Ключи, которые кладутся на арендованные машины: ключ управляющего узла
    (на сервере он свой, в /app/ssh), ключи с ноутбука для ручного захода и общий
    ключ машин друг для друга."""
    import glob
    paths = [core.SSH_KEY + ".pub"] + sorted(glob.glob(os.path.expanduser("~/.ssh/id_ed25519*.pub")))
    keys = [open(f).read().strip() for f in dict.fromkeys(paths) if os.path.exists(f)]
    if os.path.exists(RELAY + ".pub"): keys.append(open(RELAY + ".pub").read().strip())
    return "\n".join(k for k in keys if k)

def ensure_relay_key():
    if not os.path.exists(RELAY):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "fb-relay", "-f", RELAY], check=True)
        log("ключ для передачи видео между машинами создан")

def spent():
    return st.d.get("spent_usd", 0.0)

def budget_left(msg=""):
    if spent() >= MAX_USD:
        log("!!! предел бюджета %.1f $ достигнут (%s), машины прогона удаляются" % (MAX_USD, msg))
        delete_run_pods(st, name_prefix="fb-%s-" % RUN_ID); return False
    return True

# ---------------- 1. план ----------------
def stage_plan():
    if os.path.exists(PLAN):
        plan = json.load(open(PLAN)); log("план уже есть:", PLAN, "игр:", len(plan))
    else:
        vids = channel.new_videos(limit=LIMIT, days=DAYS)
        log("отбор: не разбирались раньше и опубликованы за %d дней" % DAYS)
        for v in vids: v.setdefault("match_guid", None); v.setdefault("field", None)
        json.dump(vids, open(PLAN, "w"), ensure_ascii=False, indent=1); plan = vids
        log("новых видео:", len(plan), "суммарно %.1f ч" % (sum(v["duration_s"] for v in plan) / 3600))
    if ONLY: plan = [v for v in plan if v["slug"] in ONLY]
    # matches.json для home_side.py на машинах: те же поля, что у прошлых прогонов
    json.dump({"videos": plan}, open(os.path.join(OUT, "matches.json"), "w"), ensure_ascii=False, indent=1)
    npods = max(1, math.ceil(len(plan) / PER_POD))
    no_guid = [v["slug"] for v in plan if not v.get("match_guid")]
    log("машин будет: %d (по %d игры), оценка времени %.1f ч, оценка денег %.1f $"
        % (npods, PER_POD, PER_POD * 1.0 + 0.4, npods * (PER_POD * 0.9 + 0.3) * float(os.environ.get("FB_PRICE", 0.36))))
    if no_guid: log("без match_guid (статистика посчитается, загрузка придержится):", ", ".join(no_guid))
    for v in plan: st.v(v["slug"]).setdefault("yt", v["yt"])
    st.save()
    return plan

# ---------------- 2. качалка ----------------
DL_SETUP = ("mkdir -p /root/fb/out && cd /root/fb && "
            "(command -v yt-dlp >/dev/null || pip install -q --root-user-action=ignore yt-dlp) && "
            "(command -v ffprobe >/dev/null || (curl -sL -o /tmp/ff.tar.xz "
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz && "
            "mkdir -p /tmp/ff && tar xJf /tmp/ff.tar.xz -C /tmp/ff --strip-components=1 && "
            "install -m755 /tmp/ff/bin/ffmpeg /tmp/ff/bin/ffprobe /usr/local/bin/)) && "
            "yt-dlp --version && ffprobe -version | head -1")

BLOCKED = ("Sign in to confirm", "not a bot", "HTTP Error 403", "blocked it in your country")

def dl_one(pod, v, timeout=3000):
    """Скачать одно видео на качалку. Возвращает ok, blocked."""
    slug, yt = v["slug"], v["yt"]
    cmd = ("cd /root/fb && ([ -s out/%s.mp4 ] || for c in '' android ios mweb; do "
           "ea=''; [ -n \"$c\" ] && ea=\"--extractor-args youtube:player_client=$c\"; "
           "yt-dlp -q --no-warnings $ea -f '299/137/bv[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]' "
           "--no-part --merge-output-format mp4 -o out/%s.mp4 'https://www.youtube.com/watch?v=%s' 2>&1 | tail -3; "
           "[ -s out/%s.mp4 ] && break; done) ; "
           "python3 -c \"import subprocess,sys;"
           "d=float(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0','out/%s.mp4'],"
           "capture_output=True,text=True).stdout or 0);"
           "p=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-read_intervals',str(max(0,d-60))+'%%',"
           "'-show_entries','packet=pts_time','-of','csv=p=0','out/%s.mp4'],capture_output=True,text=True).stdout.split();"
           "last=float([x for x in p if x.replace('.','',1).isdigit()][-1]) if p else 0;"
           "print('VIDEO_OK' if d>60 and last>=d-10 else 'VIDEO_BAD', round(d))\"" % (slug, slug, yt, slug, slug, slug))
    rc, out = pod.ssh(cmd, timeout=timeout, raise_dead=True)
    if "VIDEO_OK" in out: return True, False
    blocked = any(b.lower() in out.lower() for b in BLOCKED)
    pod.ssh("rm -f /root/fb/out/%s.mp4" % slug, raise_dead=False)
    log("качалка: %s не скачалось%s | %s" % (slug, " (YouTube блокирует адрес)" if blocked else "", out.strip()[-160:]))
    return False, blocked

def stage_download(plan):
    """Одна дешёвая машина качает всё. При блокировке YouTube — замена машины."""
    ensure_relay_key()
    pod = None; replacements = 0
    todo = [v for v in plan if st.v(v["slug"]).get("downloaded") != True]
    if not todo: log("все видео уже на качалке"); return st.d.get("downloader")
    while todo and replacements <= 4 and budget_left("качалка"):
        if pod is None:
            pod = Pod("dl", st, gpu=False, disk=DL_DISK, pubkeys=pubkeys(), run_id=RUN_ID)
            if not pod.create(): log("!!! качалку создать не удалось"); return None
            rc, out = pod.ssh(DL_SETUP, timeout=900, raise_dead=False)
            if "yt-dlp" not in out and rc != 0:
                log("установка на качалке не удалась:", out[-200:]); pod.delete(); pod = None; replacements += 1; continue
            pod.put_relay_key(RELAY)
            st.d["downloader"] = {"id": pod.id, "ip": pod.ip, "port": pod.port}; st.save()
        blocked_here = 0
        for v in list(todo):
            ok, blocked = dl_one(pod, v)
            if ok:
                st.v(v["slug"])["downloaded"] = True; st.save(); todo.remove(v)
                log("качалка: %s скачано (осталось %d)" % (v["slug"], len(todo)))
            elif blocked:
                blocked_here += 1
                if blocked_here >= 2: break            # адрес машины в чёрном списке — меняем машину
            else:
                st.v(v["slug"])["downloaded"] = "не скачалось"; st.save(); todo.remove(v)
        if todo:
            log("меняю качалку: YouTube блокирует этот адрес"); pod.delete(); pod = None; replacements += 1
    if pod is None and todo: log("!!! не скачано:", ", ".join(v["slug"] for v in todo))
    st.d["downloader"] = {"id": pod.id, "ip": pod.ip, "port": pod.port} if pod else None; st.save()
    return pod

# ---------------- 3. раздача и запуск ----------------
def assign(plan):
    """Кому какие игры: длинные и короткие вперемешку, чтобы машины освободились примерно вместе."""
    vids = [v for v in plan if st.v(v["slug"]).get("downloaded") == True] or plan
    vids = sorted(vids, key=lambda v: -v.get("duration_s", 0))
    n = max(1, math.ceil(len(vids) / PER_POD))
    groups = [[] for _ in range(n)]
    for i, v in enumerate(vids): groups[i % n].append(v)   # змейкой: самые длинные расходятся по разным машинам
    return groups

WORKER_FILES = "pod_setup.sh run_video.sh run_gpu_match.sh log_saver.sh pod_watchdog.sh botsort_football.yaml"

def setup_worker(pod, group, dl):
    """Установка, свои видео с качалки, запуск самостоятельной работы."""
    pod.ssh("mkdir -p /root/fb/out /root/fb/weights /root/fb/full_run")
    files = [os.path.join(SCRIPTS, f) for f in os.listdir(SCRIPTS) if f.endswith((".py", ".sh", ".yaml"))]
    pod.scp(files, pod.remote("/root/fb/"))
    pod.scp([os.path.join(HERE, f) for f in ("pod_cpu_steps.py", "pod_job.sh")], pod.remote("/root/fb/full_run/"))
    pod.scp(os.path.join(ROOT, "weights/best_7matches.pt"), pod.remote("/root/fb/weights/"))
    pod.scp([PLAN, os.path.join(OUT, "matches.json")] + ([os.path.join(OUT, "rosters.json")] if os.path.exists(os.path.join(OUT, "rosters.json")) else []), pod.remote("/root/fb/"))
    if SOURCE == "b2":  # B2 до установки: оттуда же OSNet (Google Drive срывается)
        # видео из хранилища B2 (ключ только на чтение): YouTube машинам RunPod не отдаёт
        pod.scp([os.path.join(SCRIPTS, "pitch_auto", "b2.py"), B2_READ_ENV], pod.remote("/root/"))
        pod.ssh("mv /root/%s /root/.b2env && chmod 600 /root/.b2env && pip install -q --root-user-action=ignore b2sdk 2>&1 | tail -0" % os.path.basename(B2_READ_ENV), timeout=900, raise_dead=False)
    rc, out = pod.ssh("cd /root/fb && chmod +x *.sh full_run/*.sh && ./pod_setup.sh > setup.log 2>&1; tail -3 setup.log", timeout=2400)
    if "SETUP_OK" not in out: raise RuntimeError("установка не удалась: " + out[-300:])
    pod.put_relay_key(RELAY)
    # сторож: без сигнала 2,5 часа машина удаляет себя сама (обрыв связи с управляющим узлом)
    pod.ssh("echo '%s' > /root/.runpod_key; cd /root/fb && touch heartbeat && "
            "(nohup ./pod_watchdog.sh 9000 >/dev/null 2>&1 &); true" % core.RUNPOD_KEY, raise_dead=False)
    got = []
    if SOURCE == "b2":
        for v in group:
            if v.get("parts"):
                # игра выложена по таймам: скачать части и склеить без перекодирования
                cmd = "cd /root/fb && " + " && ".join("python /root/b2.py get source/%s.mp4 out/%s.mp4 >/dev/null 2>&1" % (p_, p_) for p_ in v["parts"])
                cmd += " && (%s) > out/%s.parts.txt && ffmpeg -v error -y -f concat -safe 0 -i out/%s.parts.txt -c copy out/%s.mp4 && rm -f %s; ls -la out/%s.mp4 | awk '{print $5}'" % (
                    "; ".join("echo file /root/fb/out/%s.mp4" % p_ for p_ in v["parts"]), v["slug"], v["slug"], v["slug"],
                    " ".join("out/%s.mp4" % p_ for p_ in v["parts"]), v["slug"])
                rc, out = pod.ssh(cmd, timeout=2400, raise_dead=False)
                log(pod.tag, v["slug"], "таймы склеены:", out.strip()[-20:] + " байт"); got.append(v["slug"]); continue
            rc, out = pod.ssh("cd /root/fb && python /root/b2.py get source/%s.mp4 out/%s.mp4 >/dev/null 2>&1; ls -la out/%s.mp4 | awk '{print $5}'" % (v["slug"], v["slug"], v["slug"]), timeout=1800, raise_dead=False)
            log(pod.tag, v["slug"], "видео из B2:", out.strip()[-20:] + " байт")
            got.append(v["slug"])
    for v in group:
        if SOURCE == "b2": break
        if dl is not None:
            ok, out = pod.pull_from(dl, "/root/fb/out/%s.mp4" % v["slug"], "/root/fb/out")
            if ok: got.append(v["slug"]); continue
            log(pod.tag, "видео %s с качалки не пришло, машина скачает сама" % v["slug"])
        got.append(v["slug"])
    pod.ssh("cd /root/fb && (nohup ./full_run/pod_job.sh %d %s > out/job.out 2>&1 &); sleep 2; ls out/job.log"
            % (STEP, " ".join(v["slug"] for v in group)), raise_dead=False)
    log(pod.tag, "запущена, игры:", ", ".join(v["slug"] for v in group))
    return got

def stage_fanout(plan, dl):
    groups = assign(plan)
    log("раздача: %d машин по %d игр" % (len(groups), PER_POD))
    workers = []; errors = []
    def one(i, group):
        pod = Pod("w%d" % i, st, gpu=True, disk=60, pubkeys=pubkeys(), run_id=RUN_ID)
        try:
            if not pod.create(): errors.append("w%d: машина не создалась" % i); return
            setup_worker(pod, group, dl)
            st.d.setdefault("workers", {})["w%d" % i] = {
                "id": pod.id, "ip": pod.ip, "port": pod.port, "slugs": [v["slug"] for v in group]}
            st.save(); workers.append((pod, group))
        except Exception as e:
            errors.append("w%d: %s" % (i, str(e)[-200:])); log("w%d упала на подготовке:" % i, str(e)[-200:])
            try: pod.delete()
            except Exception: pass
    ths = [threading.Thread(target=one, args=(i, g), daemon=True) for i, g in enumerate(groups)]
    for t in ths: t.start(); time.sleep(15)          # не создаём 12 машин в одну секунду
    for t in ths: t.join()
    if dl is not None:
        log("все машины получили свои видео, качалка больше не нужна"); dl.delete()
    if errors: log("проблемы при раздаче:", "; ".join(errors))
    return workers

# ---------------- 4. сбор и загрузка ----------------
def upload_game(v, d):
    """Загрузить одну игру на сайт. Токен берётся здесь, на машины он не попадает."""
    slug = v["slug"]; s = st.v(slug)
    home_p = os.path.join(d, "home.json")
    home = json.load(open(home_p)) if os.path.exists(home_p) else {}
    if not v.get("match_guid"):
        s["upload"] = "нет match_guid: задайте его в plan.json и повторите этап collect"; st.save(); return
    if not home.get("confident"):
        s["upload"] = "цвет хозяев не определён: " + str(home.get("reason", ""))[:120]; st.save(); return
    cmd = [sys.executable, os.path.join(SCRIPTS, "upload_analytics.py"), "stats.json",
           "--match-guid", v["match_guid"], "--home-colour", home["home_colour"],
           "--video-url", "https://www.youtube.com/watch?v=" + v["yt"]]
    if home.get("home_is_top") is not None and os.path.exists(os.path.join(d, "scoreboard.json")):
        cmd += ["--scoreboard", "scoreboard.json"] + (["--home-is-top"] if home.get("home_is_top") else [])
    env = dict(os.environ, SPORTX_TOKEN=site.token(refresh=True))   # токен из .env протухает
    p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=600, env=env)
    open(os.path.join(d, "upload.log"), "w").write(" ".join(cmd) + "\n" + p.stdout + p.stderr)
    if p.returncode != 0:
        s["upload"] = "ошибка загрузки: " + (p.stdout + p.stderr)[-200:]
    else:
        s["upload"] = "загружено, проверено чтением" if site.has_analytics(v["match_guid"]) else "загружено, чтение не подтвердило"
    log(slug, s["upload"]); st.save()

def collect_from(pod, group, plan_by_slug):
    """Забрать готовые игры машины. True, если машине больше нечего отдавать."""
    try:
        rc, out = pod.ssh("cd /root/fb && touch heartbeat; ls out/*.READY out/*.CPUFAILED out/ALL_DONE 2>/dev/null; "
                          "tail -n 1 out/job.log 2>/dev/null", timeout=90)
    except Exception as e:
        log(pod.tag, "не отвечает:", str(e)[-120:]); return False
    ready = {os.path.basename(x)[:-6] for x in out.split() if x.endswith(".READY")}
    failed = {os.path.basename(x)[:-11] for x in out.split() if x.endswith(".CPUFAILED")}
    for line in out.strip().splitlines()[-1:]:
        if line.strip() and not line.startswith("out/"): log(pod.tag, "|", line.strip()[-140:])
    for slug in sorted(ready):
        s = st.v(slug)
        if s.get("collected"): continue
        d = os.path.join(OUT, slug); os.makedirs(d, exist_ok=True)
        try:
            pod.scp(pod.remote("/root/fb/out/%s.stats.tar.gz" % slug), os.path.join(OUT, slug + ".stats.tar.gz"))
            subprocess.run(["tar", "xzf", os.path.join(OUT, slug + ".stats.tar.gz"), "-C", OUT], check=True)
            os.remove(os.path.join(OUT, slug + ".stats.tar.gz"))
            try:                                        # полный результат — для архива, не обязателен
                pod.scp(pod.remote("/root/fb/out/%s.tar.gz" % slug), os.path.join(OUT, slug + ".full.tar.gz"), timeout=1200)
            except Exception: pass
        except Exception as e:
            log(pod.tag, slug, "результаты не забрались:", str(e)[-150:]); continue
        s["collected"] = True; s["status"] = "done"; st.save()
        log(pod.tag, slug, "результаты забраны")
        upload_game(plan_by_slug[slug], d)
    for slug in failed:
        if st.v(slug).get("status") != "cpu_failed":
            st.v(slug)["status"] = "cpu_failed"; st.save(); log(pod.tag, slug, "процессорные шаги упали на машине")
    done_all = "out/ALL_DONE" in out
    left = [s for s in group if not st.v(s["slug"]).get("collected") and st.v(s["slug"]).get("status") != "cpu_failed"]
    return done_all and not left

def stage_collect(plan, workers, limit_h=6):
    plan_by_slug = {v["slug"]: v for v in plan}
    if not workers and not (st.d.get("workers") or st.d.get("downloader")):
        log("этап сбора: машин этого прогона нет, ничего не делаю"); return
    if not workers:                                     # перезапуск: машины взяты из состояния
        for tag, w in (st.d.get("workers") or {}).items():
            p = Pod(tag, st); p.id, p.ip, p.port = w["id"], w["ip"], w["port"]; p.ready = True
            rec = next((r for r in st.d["pods"] if r["id"] == w["id"]), {})
            p.cost = rec.get("cost", 0); p.t0 = rec.get("t0", time.time())
            workers.append((p, [plan_by_slug[s] for s in w["slugs"] if s in plan_by_slug]))
    t0 = time.time(); alive = list(workers)
    while alive and time.time() - t0 < limit_h * 3600:
        for pod, group in list(alive):
            if collect_from(pod, group, plan_by_slug):
                log(pod.tag, "все свои игры отдала, удаляю машину"); pod.delete(); alive.remove((pod, group))
        if not budget_left("сбор"): break
        if alive: time.sleep(120)
    if alive: log("предел времени сбора, удаляю оставшиеся машины")
    for pod, _ in alive: pod.delete()
    delete_run_pods(st, name_prefix="fb-%s-" % RUN_ID)   # только свои: чужие задачи на счету не трогаем

# ---------------- отчёт ----------------
def report(plan):
    lines = ["# Прогон %s" % os.path.basename(OUT), "",
             "Игр в плане: %d. Потрачено: %.2f $." % (len(plan), spent()), "",
             "| игра | видео | разбор | загрузка |", "|---|---|---|---|"]
    for v in plan:
        s = st.v(v["slug"])
        lines.append("| %s | %s | %s | %s |" % (
            v["slug"], "скачано" if s.get("downloaded") is True else str(s.get("downloaded") or "нет"),
            s.get("status", "—"), (s.get("upload") or "—")[:80]))
    p = os.path.join(OUT, "REPORT.md"); open(p, "w").write("\n".join(lines) + "\n")
    log("отчёт:", p)

def main():
    if not core.RUNPOD_KEY: sys.exit("нет ключа RunPod (KEY= в football/.env или RUNPOD_KEY)")
    log("прогон в", OUT, "| этап:", STAGE, "| предел денег %.0f $" % MAX_USD)
    plan = stage_plan()
    if DRY or STAGE == "plan": log("остановка после плана (--dry-run)"); return
    dl = None
    if STAGE in ("all", "download"): st.set_stage("download"); dl = stage_download(plan)
    if STAGE == "download": return
    workers = []
    if STAGE in ("all", "fanout"):
        st.set_stage("fanout")
        if dl is None and st.d.get("downloader"):
            w = st.d["downloader"]; dl = Pod("dl", st); dl.id, dl.ip, dl.port = w["id"], w["ip"], w["port"]; dl.ready = True
        workers = stage_fanout(plan, dl)
    if STAGE in ("all", "fanout", "collect"):
        st.set_stage("collect"); stage_collect(plan, workers)
    st.set_stage("done"); report(plan)
    log("прогон закончен, потрачено %.2f $" % spent())

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("остановлено вручную: удаляю машины прогона"); delete_run_pods(st, name_prefix="fb-%s-" % RUN_ID); raise
    except Exception:
        import traceback
        log("СБОЙ управляющего скрипта:", traceback.format_exc()[-700:]); delete_run_pods(st, name_prefix="fb-%s-" % RUN_ID); raise
