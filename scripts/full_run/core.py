"""Общая часть полного прогона: машины RunPod, состояние, сеть, логи.

Отличия от scripts/batch_run.py (прогон 14–15.09): машина создаётся с любым
набором свойств (диск, видеокарта или только процессор), состояние — один файл
на прогон, ожидания всегда с пределом времени и проверкой сети. Управлять
прогоном может и ноутбук, и сервер: путей, привязанных к маку, здесь нет.
"""
import json, os, subprocess, sys, threading, time, urllib.error, urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
ROOT = os.path.dirname(SCRIPTS)

# Образ с CUDA для рабочих машин — тот же, что отработал ночью 14–15.09.
IMAGE_GPU = os.environ.get("FB_IMAGE_GPU", "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04")
# Качалке видеокарта не нужна, поэтому образ лёгкий: меньше тянуть и меньше диска.
IMAGE_CPU = os.environ.get("FB_IMAGE_CPU", "runpod/base:0.6.3-cpu")
GPUS = ["NVIDIA A40", "NVIDIA RTX A6000", "NVIDIA GeForce RTX 4090", "NVIDIA L40S", "NVIDIA RTX A5000", "NVIDIA L40"]
# Процессорные машины RunPod: от дешёвых к дорогим. Если ни одна не создаётся,
# качалка берёт самую дешёвую машину с видеокартой (см. Pod.create).
CPU_FLAVORS = ["cpu3c-2-4", "cpu3c-4-8", "cpu5c-2-4", "cpu5c-4-8"]

def env_key(name, *files):
    v = os.environ.get(name)
    if v: return v.strip()
    for f in files:
        try:
            for line in open(os.path.expanduser(f)):
                if line.startswith(name + "="): return line.split("=", 1)[1].strip()
        except OSError: pass
    return None

RUNPOD_KEY = env_key("RUNPOD_KEY", os.path.join(ROOT, ".env")) or env_key("KEY", os.path.join(ROOT, ".env"))
SPORTX_TOKEN = env_key("SPORTX_TOKEN", os.path.join(ROOT, ".env"), "~/Documents/sport/.env")
SSH_KEY = os.path.expanduser(os.environ.get("FB_SSH_KEY", "~/.ssh/id_ed25519_personal"))

_lock = threading.Lock()
LOG_PATH = None

def log(*a):
    line = "[%s] %s" % (datetime.now().strftime("%d.%m %H:%M:%S"), " ".join(str(x) for x in a))
    with _lock:
        print(line, flush=True)
        if LOG_PATH:
            try: open(LOG_PATH, "a").write(line + "\n")
            except OSError: pass

# ---------------- состояние ----------------
class State:
    """Состояние прогона: один json, запись атомарная, перезапуск продолжает с места."""
    def __init__(self, path):
        self.path = path
        self.d = json.load(open(path)) if os.path.exists(path) else {
            "stage": "plan", "videos": {}, "pods": [], "spent_usd": 0.0, "started": time.time()}
    def save(self):
        with _lock:
            json.dump(self.d, open(self.path + ".tmp", "w"), ensure_ascii=False, indent=1)
            os.replace(self.path + ".tmp", self.path)
    def v(self, slug):
        return self.d["videos"].setdefault(slug, {"status": "queued", "attempts": 0, "log": []})
    def set_stage(self, name):
        self.d["stage"] = name; self.save(); log("=== этап:", name)

# ---------------- RunPod ----------------
def rp(method, path, body=None, timeout=60):
    req = urllib.request.Request(
        "https://rest.runpod.io/v1" + path,
        data=json.dumps(body).encode() if body is not None else None, method=method,
        # Cloudflare режет python-urllib по User-Agent: без подмены приходит 403, код 1010.
        headers={"Authorization": "Bearer " + (RUNPOD_KEY or ""), "Content-Type": "application/json",
                 "User-Agent": "curl/8.4.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t = r.read().decode(); return json.loads(t) if t.strip() else {}
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %d %s" % (e.code, e.read().decode()[:300])}
    except Exception as e:
        return {"error": str(e)[:300]}

def list_pods():
    r = rp("GET", "/pods")
    if isinstance(r, list): return r
    if isinstance(r, dict): return r.get("pods") or []
    return []

def delete_run_pods(state, tags=None, name_prefix=None):
    """Удалить ТОЛЬКО машины этого прогона (их id записаны в состоянии).

    Раньше здесь был сплошной снос всех машин аккаунта — 16.09 он убил чужую
    работающую машину соседней задачи (2,5 часа расчёта). Сплошной снос остался
    отдельной командой sweep_pods(), и её никто не вызывает сам по себе.
    """
    mine = [p for p in state.d.get("pods", []) if not p.get("t1")]
    if tags: mine = [p for p in mine if p.get("tag") in tags]
    for rec in mine:
        log("удаляю машину прогона", rec["id"], rec.get("tag") or "")
        r = rp("DELETE", "/pods/" + rec["id"])
        rec["t1"] = time.time(); rec["deleted"] = r.get("error") or "ok"
    state.save()
    if name_prefix:                     # свои машины, не попавшие в состояние
        for p in list_pods():
            if isinstance(p, dict) and str(p.get("name", "")).startswith(name_prefix):
                log("удаляю машину прогона по имени", p["id"], p.get("name"))
                rp("DELETE", "/pods/" + p["id"])
    ids = {m["id"] for m in mine}
    alive = [p.get("id") for p in list_pods() if isinstance(p, dict)
             and (p.get("id") in ids or (name_prefix and str(p.get("name", "")).startswith(name_prefix)))]
    log("машин прогона осталось:", alive if alive else "нет")
    return alive

def sweep_pods(prefix="fb-", ask=True):
    """Снести машины по имени. Вызывать только по прямой просьбе человека:
    на счету могут работать машины других задач."""
    doomed = [p for p in list_pods() if isinstance(p, dict) and str(p.get("name", "")).startswith(prefix)]
    for p in doomed:
        log("снос:", p["id"], p.get("name")); rp("DELETE", "/pods/" + p["id"])
    left = [(p.get("id"), p.get("name")) for p in list_pods() if isinstance(p, dict)]
    log("после сноса на счету машин:", left if left else "нет")
    return left

def netcheck(timeout=20):
    """Есть ли сеть у управляющей машины. Ожидания без этой проверки уже стоили часа простоя."""
    try:
        rp("GET", "/pods", timeout=timeout); return "error" not in (rp("GET", "/pods", timeout=timeout) or {})
    except Exception:
        return False

class PodDead(Exception): pass

class Pod:
    """Машина RunPod. Один и тот же класс для качалки (без видеокарты) и рабочих."""
    def __init__(self, tag, state=None, gpu=True, disk=60, image=None, pubkeys="", run_id=""):
        self.tag = tag; self.state = state; self.want_gpu = gpu; self.disk = disk
        # имя с меткой прогона: по нему в конце находятся и свои машины, которые
        # не успели попасть в state.json (создались и упали до записи)
        self.run_id = run_id; self.name = "fb-%s-%s" % (run_id, tag) if run_id else "fb-" + tag
        self.image = image or (IMAGE_GPU if gpu else IMAGE_CPU); self.pubkeys = pubkeys
        self.id = self.ip = self.port = self.gpu = None; self.cost = 0.0; self.t0 = None; self.ready = False

    # --- создание ---
    def _body(self, gpus=None, flavor=None):
        b = {"name": self.name, "imageName": self.image, "containerDiskInGb": self.disk,
             "volumeInGb": 0, "ports": ["22/tcp"], "cloudType": "SECURE", "env": {"PUBLIC_KEY": self.pubkeys}}
        if flavor: b.update({"cpuFlavorIds": [flavor], "vcpuCount": 4})
        else: b.update({"gpuTypeIds": gpus or GPUS, "gpuCount": 1})
        return b

    def create(self, tries=6):
        """Создать машину. Качалка сначала пробует процессорные, потом дешёвую с видеокартой."""
        plans = []
        if not self.want_gpu:
            plans += [("процессорная " + f, self._body(flavor=f)) for f in CPU_FLAVORS]
            plans += [("видеокарта (запасной вариант качалки)", self._body(gpus=["NVIDIA RTX A4000", "NVIDIA RTX A5000"] + GPUS))]
        else:
            plans += [("видеокарта", self._body())]
        for attempt in range(tries):
            for name, body in plans:
                r = rp("POST", "/pods", body)
                if not r.get("id"):
                    log(self.tag, "не создалась (%s):" % name, (r.get("error") or str(r))[:160]); continue
                self.id = r["id"]; self.gpu = (r.get("machine") or {}).get("gpuTypeId") or name
                self.cost = float(r.get("costPerHr") or 0); self.t0 = time.time()
                if self.state is not None:
                    self.state.d["pods"].append({"id": self.id, "tag": self.tag, "kind": name,
                                                 "cost": self.cost, "t0": self.t0}); self.state.save()
                log(self.tag, "машина", self.id, name, "%.2f $/ч" % self.cost)
                if self.wait_ready(): self.ready = True; return True
                log(self.tag, "не поднялась, удаляю и беру другую"); self.delete()
            time.sleep(60)
        return False

    def wait_ready(self, ip_tries=42, ssh_tries=16):
        for _ in range(ip_tries):                       # ~7 минут на адрес и порт
            r = rp("GET", "/pods/" + self.id)
            pm = r.get("portMappings") or {}
            if r.get("publicIp") and pm.get("22"):
                self.ip, self.port = r["publicIp"], int(pm["22"]); break
            time.sleep(10)
        else:
            return False
        log(self.tag, "адрес", self.ip, self.port)
        for _ in range(ssh_tries):                      # ~4 минуты на ssh
            rc, out = self.ssh("echo READY; nvidia-smi -L 2>/dev/null | head -1", timeout=40, raise_dead=False)
            if rc == 0 and "READY" in out:
                log(self.tag, "готова:", (out.replace("READY", "").strip() or "без видеокарты")[:60]); return True
            time.sleep(15)
        return False

    # --- связь ---
    def ssh(self, cmd, timeout=120, raise_dead=True):
        for _ in range(3):                              # один сброс соединения — не смерть машины
            p = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                 "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                 "-o", "BatchMode=yes", "-i", SSH_KEY, "-p", str(self.port), "root@" + self.ip, cmd],
                capture_output=True, text=True, timeout=timeout + 60)
            if p.returncode != 255: break
            time.sleep(10)
        if p.returncode == 255 and raise_dead: raise PodDead(p.stderr.strip()[:200])
        return p.returncode, p.stdout + p.stderr

    def scp(self, src, dst, timeout=1800):
        err = ""
        for attempt in range(3):
            try:
                p = subprocess.run(
                    ["scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
                     "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                     "-i", SSH_KEY, "-P", str(self.port), "-q", "-r"]
                    + (src if isinstance(src, list) else [src]) + [dst],
                    capture_output=True, text=True, timeout=timeout)
                err = p.stderr.strip()[:200]
                if p.returncode == 0: return
            except subprocess.TimeoutExpired:
                err = "таймаут %d с" % timeout
            if "No such file" in err: raise RuntimeError("scp: " + err)   # локальная ошибка, повтор не поможет
            log(self.tag, "scp не удалось (%s), попытка %d/3" % (err, attempt + 1)); time.sleep(20)
        raise RuntimeError("scp: " + err)

    def remote(self, path): return "root@%s:%s" % (self.ip, path)

    def put_relay_key(self, key_path):
        """Ключ, которым машины ходят друг к другу: качалка отдаёт видео напрямую рабочим."""
        self.scp(key_path, self.remote("/root/.ssh/relay_key"))
        self.ssh("chmod 600 /root/.ssh/relay_key; grep -q fb-relay /root/.ssh/authorized_keys 2>/dev/null || "
                 "cat >> /root/.ssh/authorized_keys <<'EOK'\n%s\nEOK" % open(key_path + ".pub").read().strip(),
                 raise_dead=False)

    def pull_from(self, other, remote_path, dest_dir):
        """Забрать файл с другой машины напрямую, минуя управляющую."""
        rc, out = self.ssh(
            "mkdir -p %s && scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
            "-o ConnectTimeout=30 -i /root/.ssh/relay_key -P %d root@%s:%s %s/ && ls -la %s/"
            % (dest_dir, other.port, other.ip, remote_path, dest_dir, dest_dir), timeout=3600)
        return rc == 0, out

    # --- удаление ---
    def delete(self):
        if not self.id: return
        r = rp("DELETE", "/pods/" + self.id)
        spent = self.cost * (time.time() - (self.t0 or time.time())) / 3600
        if self.state is not None:
            self.state.d["spent_usd"] = round(self.state.d.get("spent_usd", 0) + spent, 3)
            for p in self.state.d["pods"]:
                if p["id"] == self.id: p["t1"] = time.time(); p["spent"] = round(spent, 3)
            self.state.save()
        log(self.tag, "машина", self.id, "удалена (%s), %.2f $ за %.0f мин"
            % (r.get("error") or "ok", spent, (time.time() - (self.t0 or time.time())) / 60))
        self.id = None; self.ready = False

def wait_until(check, limit_s, step_s=60, what=""):
    """Ожидание с пределом времени и сообщением о пропаже сети. Возвращает результат check() или None."""
    t0 = time.time(); no_net = 0
    while time.time() - t0 < limit_s:
        r = check()
        if r: return r
        if not netcheck():
            no_net += 1
            if no_net == 3: log("СЕТЬ ПРОПАЛА (управляющая машина), жду:", what)
        elif no_net:
            log("сеть вернулась"); no_net = 0
        time.sleep(step_s)
    log("предел ожидания %.0f мин:" % (limit_s / 60), what)
    return None
