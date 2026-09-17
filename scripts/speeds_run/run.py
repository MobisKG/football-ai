"""Скорости разметкой каждого кадра: одна машина с видеокартой на игру, результат — track_perframe.json.
   python -m speeds_run.run slug1 slug2 ...   (из scripts/)"""
import os, sys, json, time, threading
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
from full_run import core
from full_run.core import Pod, log
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
BUNDLE = os.environ["SPEEDS_BUNDLE"]; B2ENV = os.environ["B2_READ_ENV"]
OUT = os.path.join(ROOT, "results/speeds_2026-09-17"); os.makedirs(OUT, exist_ok=True)
st = core.State(os.path.join(OUT, "state.json"))
PARTS = int(os.environ.get("PARTS", 8)); LIMIT_H = float(os.environ.get("LIMIT_H", 2.5))

def one(slug, tag):
    pod = Pod(tag, st, gpu=True, disk=40, pubkeys="\n".join(open(f).read().strip() for f in dict.fromkeys([core.SSH_KEY + ".pub"] + sorted(__import__("glob").glob(os.path.expanduser("~/.ssh/id_ed25519*.pub")))) if os.path.exists(f)), run_id="sp")
    try:
        if not pod.create(): log(tag, slug, "машина не создалась"); return
        pod.ssh("mkdir -p /root/fb/results/pitch/videos /root/fb/weights", raise_dead=False)
        pod.scp([BUNDLE, os.path.join(HERE, "pod_speeds_pf.sh"), os.path.join(ROOT, "scripts/pod_watchdog.sh")], pod.remote("/root/"))
        pod.scp(B2ENV, pod.remote("/root/.b2env"))
        pod.ssh("echo '%s' > /root/.runpod_key; cd /root/fb && tar xzf /root/speeds_bundle.tar.gz && cp /root/pod_watchdog.sh . && chmod +x pod_watchdog.sh && touch heartbeat && (nohup ./pod_watchdog.sh 9000 >/dev/null 2>&1 &); true" % core.RUNPOD_KEY, raise_dead=False)
        rc, out = pod.ssh("cd /root/fb && pip install -q --root-user-action=ignore b2sdk opencv-python-headless scipy 2>&1 | tail -1; "
                          "python scripts/pitch_auto/b2.py get deps/weights/pitch_lines_v2.pt weights/pitch_lines_v2.pt && "
                          "python scripts/pitch_auto/b2.py get source/%s.mp4 results/pitch/videos/%s.mp4 && ls -la results/pitch/videos/" % (slug, slug), timeout=1800)
        log(tag, slug, "подготовка:", out.strip()[-160:])
        pod.ssh("cd /root/fb && (nohup bash /root/pod_speeds_pf.sh %s %d > speeds.log 2>&1 &); true" % (slug, PARTS), raise_dead=False)
        t0 = time.time()
        while time.time() - t0 < LIMIT_H * 3600:
            time.sleep(120)
            rc, o = pod.ssh("cd /root/fb && touch heartbeat; ls .DONE 2>/dev/null; tail -qn1 pf_part*.log 2>/dev/null | tail -%d" % PARTS, raise_dead=False)
            if ".DONE" in o: break
            if int(time.time() - t0) % 600 < 120: log(tag, slug, o.strip().replace("\n", " | ")[-300:])
        d = os.path.join(ROOT, "results/pitch", slug); os.makedirs(d, exist_ok=True)
        for rp_, lp in (("results/pitch/%s/track_perframe.json" % slug, d), ("results/pitch/%s/perframe_full" % slug, d),
                        ("results/pitch/%s/pf_merged.json" % slug, d), ("pf_full.log", os.path.join(d, "pf_full.log"))):
            try: pod.scp(pod.remote("/root/fb/" + rp_), lp)
            except Exception as e: log(tag, slug, "не забрал", rp_, e)
        log(tag, slug, "готово за %.0f мин" % ((time.time() - t0) / 60))
    except Exception as e:
        log(tag, slug, "ошибка:", str(e)[:200])
    finally:
        pod.delete()

ths = [threading.Thread(target=one, args=(s, "s%d" % i)) for i, s in enumerate(sys.argv[1:])]
for t in ths: t.start(); time.sleep(3)
for t in ths: t.join()
log("скорости: все машины удалены")
