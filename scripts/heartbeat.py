"""Сигнал жизни машинам: раз в минуту трогает /root/fb/heartbeat на каждой
арендованной машине и следит, чтобы там работал pod_watchdog.sh (см. его).
Запускается отдельно от batch_run.py на управляющем узле:

    python heartbeat.py <ключ ssh> [лимит_сек]
"""
import json, os, subprocess, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
KEY = os.path.expanduser(sys.argv[1]); LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 2700
RUNPOD_KEY = [l.split("=", 1)[1].strip() for l in open(os.path.join(ROOT, ".env")) if l.startswith("KEY=")][0]
SSH = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=20", "-o", "BatchMode=yes", "-i", KEY]
def pods():
    req = urllib.request.Request("https://rest.runpod.io/v1/pods", headers={"Authorization": "Bearer " + RUNPOD_KEY, "User-Agent": "curl/8.4.0"})
    return json.load(urllib.request.urlopen(req, timeout=60))
armed = set(); fails = 0
def netcheck():
    """сеть с ноутбука: DNS и HTTPS до RunPod; пишем перелом состояния один раз"""
    global fails
    try:
        urllib.request.urlopen(urllib.request.Request("https://rest.runpod.io/v1/pods", headers={"Authorization": "Bearer " + RUNPOD_KEY, "User-Agent": "curl/8.4.0"}), timeout=20).read()
        if fails >= 3: print(time.strftime("%H:%M:%S"), "сеть вернулась", flush=True)
        fails = 0; return True
    except Exception as e:
        fails += 1
        if fails == 3: print(time.strftime("%H:%M:%S"), "СЕТЬ ПРОПАЛА:", str(e)[:120], flush=True)
        return False
while True:
    if not netcheck(): time.sleep(60); continue
    try:
        for p in pods():
            pm = p.get("portMappings") or {}
            if not (p.get("publicIp") and pm.get("22")): continue
            host = ["-p", str(pm["22"]), "root@%s" % p["publicIp"]]
            cmd = "touch /root/fb/heartbeat; pgrep -f '[p]od_watchdog.sh' >/dev/null && echo WD_OK; echo TOUCHED"
            r = subprocess.run(SSH + host + [cmd], capture_output=True, text=True, timeout=60)
            if "TOUCHED" in r.stdout and "WD_OK" not in r.stdout:
                subprocess.run(["scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR", "-i", KEY, "-P", str(pm["22"]), "-q",
                                os.path.join(HERE, "pod_watchdog.sh"), "root@%s:/root/fb/" % p["publicIp"]], timeout=60)
                subprocess.run(SSH + host + ["printf '%%s' '%s' > /root/.runpod_key; chmod 600 /root/.runpod_key; chmod +x /root/fb/pod_watchdog.sh; (nohup /root/fb/pod_watchdog.sh %d > /dev/null 2>&1 &); echo armed" % (RUNPOD_KEY, LIMIT)], capture_output=True, text=True, timeout=60)
                if p["id"] not in armed: print(time.strftime("%H:%M:%S"), "сторож поставлен на", p["id"], flush=True); armed.add(p["id"])
    except Exception as e:
        print(time.strftime("%H:%M:%S"), "ошибка:", str(e)[:150], flush=True)
    time.sleep(60)
