"""Хранилище видео в Backblaze B2 (S3-совместимое): скачанное видео кладётся туда и
переживает удаление машины. Ключи — в ~/Documents/sport/.env (KEY_ID, APPLICATION_KEY).

    python b2.py put <файл> [имя_в_хранилище]
    python b2.py get <имя> <файл>
    python b2.py ls [префикс]
    python b2.py have <имя>        # код 0, если есть
"""
import os, sys, json
BUCKET = os.environ.get("B2_BUCKET", "football-ai")
ENDPOINT = os.environ.get("B2_ENDPOINT", "https://s3.eu-central-003.backblazeb2.com")
def creds():
    kid = os.environ.get("B2_KEY_ID"); key = os.environ.get("B2_APP_KEY")
    if kid and key: return kid, key
    for p in ("~/Documents/sport/.env", "~/.b2env", "/root/.b2env"):
        p = os.path.expanduser(p)
        if os.path.exists(p):
            d = dict(l.strip().split("=", 1) for l in open(p) if "=" in l and not l.strip().startswith("#"))
            if d.get("KEY_ID") and d.get("APPLICATION_KEY"): return d["KEY_ID"], d["APPLICATION_KEY"]
    raise SystemExit("нет ключей B2 (KEY_ID, APPLICATION_KEY)")
def bucket():
    """B2 через родной интерфейс: у ключа учётной записи нет вида, который принимает S3
    («Malformed Access Key Id»), поэтому работаем b2sdk, а не boto3"""
    from b2sdk.v2 import InMemoryAccountInfo, B2Api
    kid, key = creds()
    api = B2Api(InMemoryAccountInfo()); api.authorize_account("production", kid, key)
    return api.get_bucket_by_name(BUCKET)
def put(path, name=None):
    name = name or os.path.basename(path)
    bucket().upload_local_file(local_file=path, file_name=name)
    return name
def get(name, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    bucket().download_file_by_name(name).save_to(path)
    return path
def size(name):
    """размер файла в хранилище или None; ls b2sdk принимает папку, а не имя файла"""
    folder = name.rsplit("/", 1)[0] if "/" in name else ""
    for f, _ in bucket().ls(folder, recursive=False):
        if f.file_name == name: return f.size
    return None
def have(name):
    try: return size(name) is not None
    except Exception: return False
def ls(prefix=""):
    return [(f.file_name, f.size) for f, _ in bucket().ls(prefix, recursive=True)]
if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "put": print(put(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    elif cmd == "get": print(get(sys.argv[2], sys.argv[3]))
    elif cmd == "have": sys.exit(0 if have(sys.argv[2]) else 1)
    elif cmd == "size": print(size(sys.argv[2]) or "")
    elif cmd == "ls":
        for k, sz in ls(sys.argv[2] if len(sys.argv) > 2 else ""): print("%9.0f МБ  %s" % (sz / 1e6, k))
