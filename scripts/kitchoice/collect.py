"""Забрать результаты с машины выбора формы и удалить её: python collect.py <pod_id> <ip> <port>
Ждёт ALL_DONE, на ходу забирает готовые *.choice.json и *.sheet.jpg."""
import os, sys, time, subprocess
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
from full_run import core
from full_run.core import Pod, log
pid, ip, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
DATA = os.path.join(os.path.dirname(os.path.dirname(HERE)), "results", "kitchoice"); os.makedirs(DATA, exist_ok=True)
st = core.State(os.path.join(DATA, "state.json"))
pod = Pod("kc", st); pod.id, pod.ip, pod.port, pod.cost, pod.t0 = pid, ip, port, 0.74, time.time()
def pull():
    for ext in ("choice.json", "sheet.jpg"):
        subprocess.run("scp -q -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i %s -P %d 'root@%s:/root/kc/*.%s' %s/ 2>/dev/null" % (core.SSH_KEY, port, ip, ext, DATA), shell=True)
t0 = time.time()
while time.time() - t0 < 3 * 3600:
    rc, o = pod.ssh("cd /root/kc; ls ALL_DONE 2>/dev/null; touch heartbeat", raise_dead=False)
    pull()
    if "ALL_DONE" in o: break
    time.sleep(90)
pull(); log("kc", "всё забрано"); pod.delete()
