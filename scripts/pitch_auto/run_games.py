"""Пакет: автоопоры для списка игр подряд. python run_games.py <weights> slug1,slug2,... [--exclude-manual] [--no-gallery]
Лог — stdout; сводка каждой игры — results/pitch/<slug>/auto/summary.json"""
import os, sys, time, json
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from calibrate_game import run
w = sys.argv[1]; games = sys.argv[2].split(",")
for slug in games:
    try:
        run(slug, w, "--exclude-manual" in sys.argv, None, log=lambda s: print(time.strftime("%H:%M:%S"), slug, s, flush=True), use_gallery="--no-gallery" not in sys.argv)
    except Exception as e:
        import traceback; traceback.print_exc(); print(time.strftime("%H:%M:%S"), slug, "ОШИБКА", e, flush=True)
print("ALL_DONE", flush=True)
