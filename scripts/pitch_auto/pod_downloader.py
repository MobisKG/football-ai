"""Качалка для разметки поля: одна дешёвая машина без видеокарты качает видео игр.

    python pod_downloader.py [slug,slug,...] [--fmt 298] [--disk 60] [--max-swaps 4] [--keep]

Устроена как этап «качалка» полного прогона (`full_run/run.py`): при «Sign in to confirm»
двух видео подряд машина удаляется и берётся новая, продолжая с того же места, не больше
--max-swaps раз. Машина остаётся жить после скачивания (с неё видео забирают рабочие машины),
её адрес — в results/pitch/downloader.json. Дорогие машины берутся только после этого этапа.
"""
import os, sys, json, time, subprocess, glob
HERE = os.path.dirname(os.path.abspath(__file__)); SCR = os.path.dirname(HERE); ROOT = os.path.dirname(SCR)
sys.path.insert(0, SCR)
from full_run import core
from full_run.core import Pod, log, rp

def opt(k, d=None): return sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d
FMT = opt("--fmt", "298/bv[height<=720][ext=mp4][vcodec^=avc1]/bv[height<=720][ext=mp4]")
DISK = int(opt("--disk", 60)); MAX_SWAPS = int(opt("--max-swaps", 4)); B2 = "--no-b2" not in sys.argv
STATE = os.path.join(ROOT, "results/pitch/downloader.json")
RELAY = os.path.join(ROOT, "results/pitch/relay_key")
BLOCKED = ("Sign in to confirm", "not a bot", "HTTP Error 403", "blocked it in your country")
SETUP = ("mkdir -p /root/fb/videos && cd /root/fb && "
         "(command -v yt-dlp >/dev/null || pip install -q --root-user-action=ignore yt-dlp) && "
         "(command -v ffprobe >/dev/null || (curl -sL -o /tmp/ff.tar.xz "
         "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz && "
         "mkdir -p /tmp/ff && tar xJf /tmp/ff.tar.xz -C /tmp/ff --strip-components=1 && "
         "install -m755 /tmp/ff/bin/ffmpeg /tmp/ff/bin/ffprobe /usr/local/bin/)); yt-dlp --version; ffprobe -version | head -1")

def pubkeys():
    paths = sorted(glob.glob(os.path.expanduser("~/.ssh/id_ed25519*.pub")))
    if os.path.exists(RELAY + ".pub"): paths.append(RELAY + ".pub")
    return "\n".join(open(f).read().strip() for f in dict.fromkeys(paths) if os.path.exists(f))

def ensure_relay():
    if not os.path.exists(RELAY):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "pitch-relay", "-f", RELAY], check=True)
        log("ключ для передачи видео между машинами создан")

def dl_one(pod, slug, yt, timeout=2400):
    """Скачать одно видео, проверить целостность по последнему пакету. → ok, blocked"""
    cmd = ("cd /root/fb && V=videos/%s.mp4 && ([ -s $V ] || for c in '' android ios mweb; do "
           "ea=''; [ -n \"$c\" ] && ea=\"--extractor-args youtube:player_client=$c\"; "
           "yt-dlp -q --no-warnings --socket-timeout 30 --retries 10 $ea -f '%s' --no-part -o $V "
           "'https://www.youtube.com/watch?v=%s' 2>>out/%s.dl.err && break; rm -f $V; done); "
           "[ -s $V ] && D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 $V) && "
           "L=$(ffprobe -v error -select_streams v:0 -read_intervals \"$(python3 -c \"print(max(0,float('$D')-60))\")%%\" "
           "-show_entries packet=pts_time -of csv=p=0 $V | grep -E '^[0-9.]+$' | tail -1) && "
           "python3 -c \"import sys;sys.exit(0 if float('$L')>=float('$D')-10 else 1)\" && echo OK_$(du -m $V|cut -f1)MB || "
           "(echo FAIL; tail -2 out/%s.dl.err)") % (slug, FMT, yt, slug, slug)
    rc, out = pod.ssh(cmd, timeout=timeout, raise_dead=False)
    if "OK_" in out: return True, False
    return False, any(b in out for b in BLOCKED)

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    jobs = [j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json"))) if not j.get("merged_into")]
    if args: want = set(args[0].split(",")); jobs = [j for j in jobs if j["slug"] in want]
    ensure_relay()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {"downloaded": [], "pods": []}
    todo = [j for j in jobs if j["slug"] not in state["downloaded"]]
    log("качалка: игр %d, уже скачано %d" % (len(jobs), len(jobs) - len(todo)))
    pod = None; swaps = 0
    while todo and swaps <= MAX_SWAPS:
        if pod is None:
            pod = Pod("pitchdl", gpu=False, disk=DISK, pubkeys=pubkeys(), run_id="pitch")
            if not pod.create(): log("!!! качалку создать не удалось"); return 1
            rc, out = pod.ssh(SETUP, timeout=900, raise_dead=False)
            if rc != 0 and "yt-dlp" not in out:
                log("установка не удалась:", out[-200:]); pod.delete(); pod = None; swaps += 1; continue
            pod.ssh("mkdir -p /root/fb/out", timeout=60, raise_dead=False)
            subprocess.run(["scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                            "-i", core.SSH_KEY, "-P", str(pod.port), "-q", RELAY, "root@%s:/root/.ssh/relay_key" % pod.ip], timeout=120)
            pod.ssh("chmod 600 /root/.ssh/relay_key", timeout=60, raise_dead=False)
            if B2:      # ключи хранилища и библиотека: видео переживает удаление машины
                subprocess.run(["scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                                "-i", core.SSH_KEY, "-P", str(pod.port), "-q", "/tmp/b2env", "root@%s:/root/.b2env" % pod.ip], timeout=120)
                subprocess.run(["scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                                "-i", core.SSH_KEY, "-P", str(pod.port), "-q", os.path.join(HERE, "b2.py"), "root@%s:/root/fb/scripts/pitch_auto/b2.py" % pod.ip], timeout=120)
                pod.ssh("mkdir -p /root/fb/scripts/pitch_auto; chmod 600 /root/.b2env; pip3 install -q --root-user-action=ignore b2sdk 2>&1 | tail -0; "
                        "P=$(head -1 $(command -v pip3) | sed 's/^#!//'); echo \"$P\" > /root/.b2py; $P -c 'import b2sdk' && echo b2 ok", timeout=900, raise_dead=False)
            state["pods"].append({"id": pod.id, "ip": pod.ip, "port": pod.port, "t0": time.time()})
            state["pod"] = {"id": pod.id, "ip": pod.ip, "port": pod.port, "cost": pod.cost}
            json.dump(state, open(STATE, "w"), indent=1)
            log("качалка готова: %s %s:%s, %.3f $/ч" % (pod.id, pod.ip, pod.port, pod.cost))
        blocked_here = 0
        for j in list(todo):
            t0 = time.time(); ok, blocked = dl_one(pod, j["slug"], j["yt"])
            if ok:
                todo.remove(j); state["downloaded"].append(j["slug"]); json.dump(state, open(STATE, "w"), indent=1)
                log("скачано %s за %.0f с, осталось %d" % (j["slug"], time.time() - t0, len(todo)))
                if B2:
                    rc2, o2 = pod.ssh("cd /root/fb && $(cat /root/.b2py) scripts/pitch_auto/b2.py put videos/%s.mp4 source/%s.mp4 2>&1 | tail -1" % (j["slug"], j["slug"]), timeout=3600, raise_dead=False)
                    log("%s в хранилище: %s" % (j["slug"], o2.strip()[-60:]))
                blocked_here = 0
            elif blocked:
                blocked_here += 1; log("%s: YouTube блокирует адрес (%d подряд)" % (j["slug"], blocked_here))
                if blocked_here >= 2: break
            else:
                log("%s: не скачалось по другой причине, пропускаю" % j["slug"]); todo.remove(j)
        if todo and blocked_here >= 2:
            swaps += 1
            if swaps > MAX_SWAPS: log("!!! замен качалки уже %d, останавливаюсь" % (swaps - 1)); break
            log("меняю качалку (замена %d из %d)" % (swaps, MAX_SWAPS)); pod.delete(); pod = None
            state["pod"] = None; json.dump(state, open(STATE, "w"), indent=1)
    if pod is not None:
        rc, out = pod.ssh("du -sh /root/fb/videos; ls /root/fb/videos | wc -l", timeout=120, raise_dead=False)
        log("на качалке:", out.strip().replace("\n", ", "))
        if "--keep" not in sys.argv: log("качалка оставлена жить:", "%s:%s" % (pod.ip, pod.port))
    log("не скачано: %s" % (", ".join(j["slug"] for j in todo) if todo else "нет"))
    return 0
if __name__ == "__main__": sys.exit(main())
