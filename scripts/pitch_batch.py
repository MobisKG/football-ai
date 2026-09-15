"""Пакет разметки поля: на одной машине по очереди скачать видео, протянуть привязку
с опорами своего поля и снять контрольные кадры.

    python pitch_batch.py <tag> <slug> [<slug> ...]

Опоры поля берутся из опорной игры (REF ниже), поле игры — из results/pitch/jobs.json.
На машине: out/track_<slug>.json, check_<slug>/ (4 кадра с разметкой), лог out/pb_<slug>.log.
Результаты забираются в results/pitch/<slug>/. Уже готовые игры пропускаются.
"""
import json, os, sys, subprocess, time, importlib.util
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
tag, slugs = sys.argv[1], sys.argv[2:]
REF = {"Полей №3": ("bsht_baldariki", "t0480", "t0810"),
       "Полей №1": ("povorot_kadamjai", "t0900", "t0930"),
       "Полей №2": ("elkat_ailin", "t0390", "t0840")}
jobs = {j["slug"]: j for j in json.load(open(os.path.join(ROOT, "results/pitch/jobs.json")))}
def pod():
    sys.argv = ["batch_run.py", "--matches", os.path.join(ROOT, "results/batch_2026-09-14/matches.json")]
    spec = importlib.util.spec_from_file_location("br", os.path.join(HERE, "batch_run.py")); br = importlib.util.module_from_spec(spec); spec.loader.exec_module(br)
    rec = json.load(open(os.path.join(ROOT, "results/pitch/pods.json")))[tag]; p = br.Pod(tag); p.id = rec["id"]
    r = br.rp("GET", "/pods/" + p.id); p.ip = r["publicIp"]; p.port = int(r["portMappings"]["22"]); return p
p = pod()
def log(*a): print(time.strftime("%H:%M:%S"), tag, *a, flush=True)
for slug in slugs:
    out = os.path.join(ROOT, "results/pitch", slug); os.makedirs(out, exist_ok=True)
    if os.path.exists(os.path.join(out, "track.json")): log(slug, "уже готово"); continue
    ref, a1, a2 = REF[jobs[slug]["field"]]
    step = max(1, round(59.94 / (jobs[slug]["pos_fps"] * 4)) * 4) if jobs[slug]["pos_fps"] < 10 else 4
    # шаг привязки = шаг разбора: 60 к/с -> 4; 30 к/с с позициями 7,5 к/с -> 4; 30 к/с с 15 к/с -> 2
    for t in (a1, a2):
        p.ssh("mkdir -p /root/fb/anchors/%s_%s" % (ref, t), raise_dead=False)
        p.scp([os.path.join(ROOT, "results/pitch", ref, "anchor_" + t, f) for f in ("anchor.json", "anchor_small.jpg")], p.remote("/root/fb/anchors/%s_%s/" % (ref, t)))
    log(slug, "поле", jobs[slug]["field"], "опоры", ref, "скачивание")
    r = subprocess.run([sys.executable, os.path.join(HERE, "pitch_pod.py"), "video", tag, slug, jobs[slug]["yt"]], capture_output=True, text=True)
    if r.returncode != 0: log(slug, "!!! видео не скачалось:", r.stdout[-200:]); continue
    rc, info = p.ssh("cd /root/fb && python -c \"import cv2;c=cv2.VideoCapture('out/%s.mp4');print(c.get(5))\"" % slug, timeout=120)
    src_fps = float(info.strip().splitlines()[-1]); step = max(1, round(src_fps / jobs[slug]["pos_fps"]))
    log(slug, "частота видео %.2f, позиций %.2f, шаг %d, протягивание" % (src_fps, jobs[slug]["pos_fps"], step))
    cmd = ("cd /root/fb && nice -n 5 python pitch_track.py out/{s}.mp4 anchors/{r}_{a1}/anchor.json out/track_{s}.json --step {st} --no-ref "
           "--ext-anchor anchors/{r}_{a1}/anchor.json --ext-anchor anchors/{r}_{a2}/anchor.json > out/pb_{s}.log 2>&1; tail -n 2 out/pb_{s}.log").format(s=slug, r=ref, a1=a1, a2=a2, st=step)
    rc, o = p.ssh(cmd, timeout=5400); log(slug, o.strip().splitlines()[-1] if o.strip() else "нет вывода")
    if "готово" not in o: log(slug, "!!! привязка не удалась"); continue
    dur = src = None
    rc, o2 = p.ssh("cd /root/fb && python -c \"import cv2;c=cv2.VideoCapture('out/{s}.mp4');print(c.get(7)/c.get(5))\" && rm -rf check_{s} && "
                   "nice -n 10 python -c \"import cv2;c=cv2.VideoCapture('out/{s}.mp4');print(int(c.get(7)/c.get(5)))\" > /tmp/d_{s}; D=$(cat /tmp/d_{s}); "
                   "python pitch_check.py out/{s}.mp4 out/track_{s}.json check_{s} $((D/5)) $((2*D/5)) $((3*D/5)) $((4*D/5)) > /dev/null 2>&1; tar cf check_{s}.tar check_{s}".format(s=slug), timeout=900)
    p.scp(p.remote("/root/fb/out/track_%s.json" % slug), os.path.join(out, "track.json"))
    try:
        p.scp(p.remote("/root/fb/check_%s.tar" % slug), os.path.join(out, "check.tar"))
        subprocess.run(["tar", "xf", os.path.join(out, "check.tar"), "-C", out], check=True); os.remove(os.path.join(out, "check.tar"))
    except Exception as e: log(slug, "контрольные кадры не забраны:", str(e)[:100])
    p.ssh("rm -f /root/fb/out/%s.mp4" % slug, raise_dead=False)          # место на диске
    log(slug, "готово")
