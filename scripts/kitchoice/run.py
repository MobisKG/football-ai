"""Одна машина: выбор формы для игр из games.tsv. python -m kitchoice.run (из scripts/)"""
import os, sys, time, glob
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
from full_run import core
from full_run.core import Pod, log
import full_run.run as R
DATA = os.path.join(os.path.dirname(os.path.dirname(HERE)), "results", "kitchoice"); os.makedirs(DATA, exist_ok=True)
st = core.State(os.path.join(DATA, "state.json"))
pod = Pod("kc", st, gpu=True, disk=80, pubkeys=R.pubkeys(), run_id="kc")
try:
    if not pod.create(): sys.exit("машина не создалась")
    pod.ssh("mkdir -p /root/kc", raise_dead=False)
    files = [os.path.join(HERE, f) for f in ("kit_choice.py", "pod_kitchoice.sh", "extract_crops2.py", "sheet.py")] + [os.path.join(DATA, f) for f in ("games.tsv", "sheets.txt")] + glob.glob(os.path.join(DATA, "*.pos.json"))
    pod.scp(files, pod.remote("/root/kc/"))
    pod.scp([os.path.join(HERE, "..", "pitch_auto", "b2.py")], pod.remote("/root/"))
    pod.scp(os.path.expanduser("~/.config/fb/b2read.env"), pod.remote("/root/.b2env"))
    pod.ssh("echo '%s' > /root/.runpod_key" % core.RUNPOD_KEY, raise_dead=False)
    pod.ssh("cd /root/kc && (nohup bash pod_kitchoice.sh > job.log 2>&1 &); true", raise_dead=False)
    t0 = time.time()
    while time.time() - t0 < 2.5 * 3600:
        time.sleep(90)
        rc, o = pod.ssh("cd /root/kc; ls ALL_DONE 2>/dev/null; tail -2 job.log; tail -qn1 *.choice.log 2>/dev/null | tail -1", raise_dead=False)
        log("kc", o.strip().replace("\n", " | ")[-200:])
        if "ALL_DONE" in o: break
    for f in glob.glob(os.path.join(DATA, "*.pos.json")):
        s = os.path.basename(f).replace(".pos.json", "")
        for ext in ("choice.json", "sheet.jpg"):
            try: pod.scp(pod.remote("/root/kc/%s.%s" % (s, ext)), DATA + "/")
            except Exception: pass
finally:
    pod.delete()
