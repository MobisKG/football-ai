"""Сводный отчёт results/pitch/AUTO_REPORT.md по всем играм: опоры, сверка с ручными, скорости, загрузка."""
import os, sys, json, glob, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE)); P = os.path.join(ROOT, "results/pitch")
jobs = json.load(open(os.path.join(P, "jobs.json")))
def ld(p):
    try: return json.load(open(p))
    except Exception: return None
rows = []
for j in jobs:
    s = j["slug"]; D = os.path.join(P, s); sm = ld(os.path.join(D, "auto/summary.json")) or {}
    mc = sm.get("manual_check") or {}; pl = ld(os.path.join(D, "speeds_auto/plausibility.json")) or {}
    up = os.path.exists(os.path.join(D, "speeds_auto/upload.log")) and "SUCCESS" in open(os.path.join(D, "speeds_auto/upload.log")).read()
    manual = len([f for f in glob.glob(os.path.join(D, "manual/frame_t*.json")) if (ld(f) or {}).get("H_img2plan")])
    rows.append({"slug": s, "field": j["field"], "frames": sm.get("frames"), "acc": sm.get("accepted"), "strong": sm.get("strong"), "src": sm.get("sources"), "weights": sm.get("weights"),
                 "manual": manual, "mc_n": mc.get("n"), "mc_mean": mc.get("mean_m"), "mc_max": mc.get("max_m"),
                 "dist": pl.get("dist_per_min_median"), "top": pl.get("top_speed_median"), "topmax": pl.get("top_speed_max"), "jit": pl.get("standing_speed_median_mps"), "pl_ok": pl.get("ok"), "pl_reason": pl.get("reason"),
                 "uploaded": up, "merged": j.get("merged_into"), "home": j.get("home_colour"), "video": os.path.exists(os.path.join(P, "videos", s + ".mp4"))})
if __name__ == "__main__":
    f = lambda v, d=1: "—" if v is None else (("%%.%df" % d) % v if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))
    L = ["# Автоматическая привязка поля и скорости: отчёт", "", "Обновлено %s. Сеть линий: `weights/pitch_lines*.pt`, код `scripts/pitch_auto/`. Подробности метода — внизу." % time.strftime("%Y-%m-%d %H:%M"), "",
         "| Игра | Поле | Кадров | Принято опор | Ручных | Ошибка на ручных (ср/макс, м) | Пробег м/мин | Скорость медиана/макс км/ч | Дрожание м/с | Правдоподобие | Загружено |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        acc = "—" if r["acc"] is None else "%d (%s сильных)" % (r["acc"], f(r["strong"], 0))
        mc = "—" if not r["mc_n"] else "%s / %s (n=%d)" % (f(r["mc_mean"], 2), f(r["mc_max"], 2), r["mc_n"])
        plok = "—" if r["pl_ok"] is None else ("да" if r["pl_ok"] else "нет: " + (r["pl_reason"] or ""))
        upl = "да" if r["uploaded"] else ("часть сдвоенной" if r["merged"] else ("нет" if r["pl_ok"] is not None else "—"))
        L.append("| %s | %s | %s | %s | %d | %s | %s | %s / %s | %s | %s | %s |" % (r["slug"], r["field"].replace("Полей ", ""), f(r["frames"], 0), acc, r["manual"], mc, f(r["dist"], 0), f(r["top"]), f(r["topmax"]), f(r["jit"], 2), plok, upl))
    L += ["", "## Итог 16.09.2026", "",
          "- Загружено на сайт (версия 2026-09-16-speeds): игры с «да» в последней колонке. Прошли проверку пробега (40–70 м/мин), максимальной скорости (медиана 16–24 км/ч) и дрожания у стоящих (≤ 1 м/с), разметка на кадрах проверена глазами на части игр.",
          "- «Максимальная скорость» — 99,5-й процентиль скорости игрока по окнам 1,5 с за игру (раньше — наибольший из отрезков, один сбой привязки давал 30+ км/ч).",
          "- Не загружено: мало игроков с прочитанным номером (проверять не на чем), пробег или скорость вне нормы, не определены хозяева (Казарман vs Гипер Строй), не построились опоры (untitled_3735: 2 из 151).",
          "- Схема: дешёвая процессорная качалка (0,12–0,24 $/ч) → рабочие машины забирают видео с неё → опоры сетью линий и привязка → ноутбук считает метры и грузит. Исходники кладутся в Backblaze B2 (football-ai), сейчас там 2 видео: остальные машины удалены раньше, чем появились ключи, а повторное скачивание YouTube заблокировал.",
          "- Аренда за 16.09: около 8–10 $ (по журналам удалений 4,64 $ за 11 машин, остальное — машины, удалённые вручную и сторожем).",
          "- Что дальше: поле №2 и вечерний свет — мало принятых опор (Михайловка, Дос Атлетик); пробег у Долона 33 м/мин — проверить разметку глазами; видеокарта нужна только для опор, привязку дешевле считать на процессорных машинах."]
    open(os.path.join(P, "AUTO_REPORT.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))
