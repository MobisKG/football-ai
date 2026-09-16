"""Рабочие машины для разметки поля и привязки: берут видео с качалки и считают.

    python pod_workers.py [--pods 5] [--par 3] [--only slug,slug] [--keep-dl]

Порядок: качалка уже скачала видео (pod_downloader.py, results/pitch/downloader.json).
Игры раскладываются змейкой по длительности, каждая машина получает свои видео с качалки
напрямую (scp по общему ключу), запускает pod_speeds.sh под nohup и работает сама.
Готовые игры (auto/ + track_auto.json) забираются на ноутбук по мере появления, машина
удаляется, когда её игры собраны. Качалка удаляется, как только все видео разошлись.
"""
import os, sys, json, math, time, subprocess, threading, tarfile
HERE = os.path.dirname(os.path.abspath(__file__)); SCR = os.path.dirname(HERE); ROOT = os.path.dirname(SCR)
sys.path.insert(0, SCR)
from full_run import core
from full_run.core import Pod, log, rp
def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
NPODS = int(opt("--pods", 5)); PAR = int(opt("--par", 3))
STATE = os.path.join(ROOT, "results/pitch/workers.json"); DL = json.load(open(os.path.join(ROOT, "results/pitch/downloader.json")))
RELAY = os.path.join(ROOT, "results/pitch/relay_key")
BUNDLE = os.path.join(ROOT, "results/pitch/bundle.tar.gz")
lock = threading.Lock(); started = set(); NGROUPS = 0

def pubkeys():
    import glob
    paths = sorted(glob.glob(os.path.expanduser("~/.ssh/id_ed25519*.pub")))
    if os.path.exists(RELAY + ".pub"): paths.append(RELAY + ".pub")
    return "\n".join(open(f).read().strip() for f in dict.fromkeys(paths) if os.path.exists(f))

def make_bundle():
    """код, веса и ручные опоры с их кадрами — то, что нужно машине"""
    import glob, shutil
    stage = os.path.join(ROOT, "results/pitch/_bundle"); shutil.rmtree(stage, ignore_errors=True)
    def cp(src, dst):
        d = os.path.join(stage, dst); os.makedirs(os.path.dirname(d), exist_ok=True); shutil.copy(src, d)
    for f in glob.glob("scripts/pitch_auto/*.py") + ["scripts/pitch_auto/pod_speeds.sh", "scripts/pitch_track.py",
                                                     "scripts/pitch_check.py", "docs/pitch.json", "results/pitch/jobs.json", "weights/pitch_lines_v2.pt"]:
        cp(os.path.join(ROOT, f), f)
    for f in glob.glob(os.path.join(ROOT, "results/pitch/*/manual/frame_t*.json")):
        j = json.load(open(f))
        if not j.get("H_img2plan"): continue
        game = f.split("/")[-3]; cp(f, os.path.relpath(f, ROOT))
        img = j.get("image")
        if img and os.path.exists(img): cp(img, "results/pitch/%s/frames_%s_full/t%04d.jpg" % (game, game, j["sec"]))
    with tarfile.open(BUNDLE, "w:gz") as t: t.add(stage, arcname=".")
    log("пакет для машин: %.0f МБ" % (os.path.getsize(BUNDLE) / 1e6))

def snake(games, n):
    """змейкой по убыванию длительности: машины освобождаются примерно вместе"""
    games = sorted(games, key=lambda g: -g.get("frames", 0)); out = [[] for _ in range(n)]
    for i, g in enumerate(games):
        k = i % (2 * n); out[k if k < n else 2 * n - 1 - k].append(g)
    return [x for x in out if x]

def worker(idx, games, done_evt):
    tag = "w%d" % idx; slugs = [g["slug"] for g in games]
    p = Pod(tag, gpu=True, disk=80, pubkeys=pubkeys(), run_id="pitch")
    if not p.create(): log(tag, "!!! машина не создалась"); return
    with lock:
        st = json.load(open(STATE)); st["pods"].append({"tag": tag, "id": p.id, "ip": p.ip, "port": p.port, "games": slugs}); json.dump(st, open(STATE, "w"), indent=1)
    rc, out = p.ssh("mkdir -p /root/fb/videos /root/fb/out", timeout=120, raise_dead=False)
    p.scp(BUNDLE, p.remote("/root/fb/bundle.tar.gz"))
    subprocess.run(["scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                    "-i", core.SSH_KEY, "-P", str(p.port), "-q", RELAY, "root@%s:/root/.ssh/relay_key" % p.ip], timeout=300)
    rc, out = p.ssh("cd /root/fb && tar xzf bundle.tar.gz && chmod 600 /root/.ssh/relay_key && "
                    "pip install -q --root-user-action=ignore scipy opencv-python-headless 2>&1 | tail -0; python -c \"import torch,cv2,scipy;print('готово', torch.cuda.is_available())\"", timeout=1200, raise_dead=False)
    log(tag, "установка:", out.strip()[-60:])
    # видео с качалки
    dl = DL["pod"]; got = []
    for s in slugs:
        rc, out = p.ssh("cd /root/fb && scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i /root/.ssh/relay_key "
                        "-P %d root@%s:/root/fb/videos/%s.mp4 videos/ >/dev/null 2>&1; [ -s videos/%s.mp4 ] && echo OK" % (dl["port"], dl["ip"], s, s),
                        timeout=1800, raise_dead=False)
        (got if "OK" in out else []).append(s)
        log(tag, s, "видео получено" if "OK" in out else "!!! видео не получено")
    if not got: log(tag, "нет видео, удаляю машину"); p.delete(); return
    p.ssh("cd /root/fb && (setsid nohup bash scripts/pitch_auto/pod_speeds.sh '%s' %d > out/speeds.log 2>&1 </dev/null &); sleep 3; tail -1 out/speeds.log"
          % (" ".join("%s:%s" % (s, next(g["yt"] for g in games if g["slug"] == s)) for s in got), PAR), timeout=300, raise_dead=False)
    log(tag, "работа запущена на %d играх" % len(got))
    with lock:
        started.add(tag)
        if len(started) >= NGROUPS: transfers_done.set()
    left = set(got); t0 = time.time()
    while left and time.time() - t0 < 5 * 3600:
        time.sleep(180)
        rc, out = p.ssh("cd /root/fb && ls results/pitch/*/track_auto.json 2>/dev/null | cut -d/ -f3 | tr '\\n' ' '", timeout=180, raise_dead=False)
        ready = [s for s in out.split() if s in left]
        for s in ready:
            rc, out = p.ssh("cd /root/fb && tar cf out/%s.res.tar results/pitch/%s/auto results/pitch/%s/track_auto.json results/pitch/%s/check_auto.jpg 2>/dev/null; du -m out/%s.res.tar | cut -f1" % (s, s, s, s, s), timeout=600, raise_dead=False)
            dst = os.path.join(ROOT, "results/pitch", "%s.res.tar" % s)
            try:
                p.scp(p.remote("/root/fb/out/%s.res.tar" % s), dst, timeout=1800)
                with tarfile.open(dst) as t: t.extractall(ROOT)
                os.remove(dst); left.discard(s); log(tag, s, "забрано (осталось %d)" % len(left))
                with lock:
                    st = json.load(open(STATE)); st.setdefault("collected", []).append(s); json.dump(st, open(STATE, "w"), indent=1)
            except Exception as e: log(tag, s, "не забралось:", str(e)[:100])
    log(tag, "готово, удаляю машину; не забрано:", ", ".join(left) if left else "нет")
    p.delete(); done_evt.set()

def main():
    jobs = [j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json"))) if not j.get("merged_into")]
    only = opt("--only")
    if only: jobs = [j for j in jobs if j["slug"] in only.split(",")]
    have = set(DL.get("downloaded", []))
    games = [j for j in jobs if j["slug"] in have]
    log("игр с видео на качалке: %d из %d" % (len(games), len(jobs)))
    if not games: return 1
    json.dump({"pods": [], "collected": []}, open(STATE, "w"), indent=1)
    make_bundle()
    groups = snake(games, min(NPODS, len(games)))
    globals()["NGROUPS"] = len(groups)
    log("машин: %d, игр на машину: %s" % (len(groups), ", ".join(str(len(g)) for g in groups)))
    transfers_done = threading.Event(); globals()["transfers_done"] = transfers_done
    evts = []; ths = []
    for i, g in enumerate(groups):
        e = threading.Event(); evts.append(e)
        t = threading.Thread(target=worker, args=(i + 1, g, e), daemon=True); t.start(); ths.append(t); time.sleep(20)
    # качалку удаляем, когда все машины забрали свои видео
    def kill_dl():
        # ждём, пока все машины заберут свои видео: удалять качалку по таймеру нельзя —
        # 16.09 она умерла посреди передачи, и двум машинам видео не хватило
        while not transfers_done.is_set(): time.sleep(30)
        if "--keep-dl" in sys.argv: return
        try:
            rp("DELETE", "/pods/" + DL["pod"]["id"]); log("качалка удалена")
        except Exception as e: log("качалку не удалить:", str(e)[:80])
    threading.Thread(target=kill_dl, daemon=True).start()
    for t in ths: t.join()
    log("все машины отработали")
    return 0
if __name__ == "__main__": sys.exit(main())
