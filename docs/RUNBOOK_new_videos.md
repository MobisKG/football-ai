# Новые видео: YouTube → B2 → RunPod → сайт

Пошаговая инструкция для следующей сессии. Устройство самого разбора описано в
`docs/PIPELINE_v2.md`, а ошибки во время прогона разобраны в скилле
`.claude/skills/full-run/SKILL.md`. Здесь описано, как это делается на практике
с ноября 2026: видео качаются по одному на ноутбуке, обработка идёт на сервере.

## Что где

| | |
|---|---|
| Канал | YouTube `@dordoifood_35` |
| Хранилище видео | Backblaze B2, бакет `football-ai`, файлы `source/<slug>.mp4` |
| Сервер-оркестратор | `ssh -o BatchMode=yes -i ~/.ssh/id_ed25519_personal root@46.101.9.74`, контейнер `football-runner`; папка на хосте `/opt/football-runner/results` видна в контейнере как `/data/results` |
| Машины с видеокартами | RunPod, через GraphQL (`scripts/full_run/core.py`, `rp()`) |
| Сайт | cp.sportx.kg; вход и токен — `scripts/full_run/site.py` |
| Ключи | `~/Documents/sport/.env` (B2 с правом записи, логин сайта), `football/.env` (`KEY=` RunPod); ключ B2 только на чтение для машин — `/opt/football-runner/results/b2read.env` |
| Инструменты для скачивания | `~/.cache/fb-ytdlp/venv` (yt-dlp, b2sdk), а также `ffmpeg`/`ffprobe` и `node` |

Секреты в вывод не печатать. Не делай `cat` или `sed` по `.env`: там в
закомментированных строках лежат пароли.

## 1. Найти новые видео

```bash
cd ~/Documents/sport/football/scripts
python3 -m full_run.channel --days 14 --json ../results/new_<дата>.json
```

`channel.py` отсеивает уже разобранные ролики по `results/*/matches.json` и
`known.json`. Локальная папка `results/` бывает почищена, поэтому список
перепроверь:
- сравни с прошлыми `results/ingest_*.json`;
- посмотри, что уже лежит в B2 (`b2.py ls source/`);
- открой папки прогонов на сервере
  (`/opt/football-runner/results/run_*/plan.json`, поле `yt`).

Трансляции и ролики без «vs» в названии убирай. Из оставшегося составь
`results/ingest_<дата>.json`: это список объектов с полями
`{yt, title, duration_s, date, slug}`. Slug получается из названия плюс 4 символа
id, его делает `channel.slugify`.

Скажи пользователю, сколько нашлось видео и часов, и качай только после
его «да».

## 2. Скачать в B2 строго по одному

```bash
cd ~/Documents/sport/football
YT_COOKIES=~/Documents/sport/www.youtube.com_cookies.txt \
  bash scripts/pitch_auto/ingest_b2.sh results/ingest_<дата>.json
```

Запускай фоном (`run_in_background`) и следи за `results/ingest.log`.
Для каждого видео скрипт делает так:
1. скачивает его через yt-dlp;
2. проверяет файл через ffprobe;
3. заливает в B2 и сверяет размер;
4. удаляет локальную копию.

Повторный запуск пропускает то, что уже лежит в B2. Если заливка упала, скрипт
останавливается.

**Cookies.** Файл `www.youtube.com_cookies.txt` принадлежит пользователю
(права 600). Правила:
- никуда его не отправлять и не добавлять в git;
- не печатать его значения;
- не доставать cookies из браузера самому;
- копировать на другой компьютер только с явного разрешения.

Без cookies YouTube часто отвечает `Sign in to confirm you're not a bot`.

**Mac mini** (`ssh doni@100.88.214.98`): там та же схема в `~/fb_ingest`,
ключ записи лежит в `~/.b2env`. Заливка в B2 оттуда быстрая, но без cookies
YouTube его блокирует. Качать там — только если пользователь просит.

**Если зависла заливка в B2** (размер не растёт, ответы 503 «no tomes»), отмени
недокачанные большие файлы и запусти скрипт заново:

```bash
cd scripts/pitch_auto && ~/.cache/fb-ytdlp/venv/bin/python -c "
import b2; bk=b2.bucket(); [bk.cancel_large_file(f.file_id) for f in bk.list_unfinished_large_files()]"
```

**Видео из двух частей** (одна игра выложена двумя роликами). Определи порядок
частей по первым кадрам: где разминка или центр поля в начале. Дальше:
1. склей части: `ffmpeg -f concat -safe 0 -i list.txt -c copy -movflags +faststart full.mp4`;
2. залей результат как `source/<slug>_full.mp4`;
3. в списке оставь одну запись со slug `<slug>_full` и суммарной длительностью.

**В конце сверь,** что все slug из списка есть в B2. Из B2 уже несколько раз
бесследно пропадали файлы. Если чего-то нет, перекачай это видео.

## 3. Сопоставить видео с матчами сайта

```bash
cd ~/Documents/sport/football/scripts
scp -i ~/.ssh/id_ed25519_personal 'root@46.101.9.74:/opt/football-runner/results/run_*/rosters.json' /tmp/ 2>/dev/null  # или скачай по одному
python3 -m full_run.match_plan ../results/ingest_<дата>.json <папка> \
    --from <дата первого матча> --to <сегодня> --rosters '<папка с rosters>/*.json'
```

Скрипт печатает таблицу соответствий и пишет `plan.json` и `rosters.json`.
Строки с пометкой `<-- ПРОВЕРЬ` и видео, которым матч не нашёлся, проверь
руками. Матч может быть из прошлого тура: тогда расширь окно `--from`. Видео,
у матча которого на сайте уже есть статистика, в план не попадает.

Пустые `numbers` в составах — это нормально: у многих команд на сайте нет номеров.

## 4. Запустить обработку на сервере

Сначала проверь, что нет висящих машин, и посмотри баланс:

```bash
ssh ... 'docker exec -w /app football-runner python -c "
from full_run import core; print(core.rp(\"GET\",\"/pods\"))
print(core._gql(\"query{myself{clientBalance currentSpendPerHr}}\"))"'
```

Потом залей план на сервер и запусти прогон:

```bash
D=run_<дата>t<тур>
ssh ... "mkdir -p /opt/football-runner/results/$D"
scp -i ~/.ssh/id_ed25519_personal plan.json rosters.json root@46.101.9.74:/opt/football-runner/results/$D/
ssh ... "docker exec -w /app football-runner python -m full_run.run --outdir /data/results/$D \
  --source b2 --b2-env /data/results/b2read.env --per-pod 2 --dry-run"          # оценка
ssh ... "docker exec -d -w /app football-runner sh -c 'python -m full_run.run --outdir /data/results/$D \
  --source b2 --b2-env /data/results/b2read.env --per-pod 2 --max-usd N >> /data/results/$D/run.out 2>&1'"
```

**Деньги.** Встроенная оценка занижена примерно вдвое: на практике выходит
около 0,7 $ за игру, 29 игр стоили 21 $. Перед арендой назови пользователю
оценку и оставь `--max-usd` ниже баланса. В предел входят и работающие
машины. Если денег не хватает, попроси пополнить баланс.

**Слежка.** Журнал в `/opt/football-runner/results/$D/run.out`, состояние в
`state.json` (`pods`, `workers`, `spent_usd`). Разбор пары игр на одной машине
занимает 1,5–2 часа.

**Нет свободных видеокарт** (`There are no longer any instances available`):
прогон берёт сколько дали. Когда первая волна закончится, запусти ту же
команду ещё раз. Она продолжит с места и создаст машины только для непокрытых
игр.

**Остановка** — только `kill -TERM` процессу `full_run.run` в контейнере.
Удаляй только машины этого прогона, их id лежат в `state.json`. Сплошной снос
`sweep_pods()` делай только после вопроса пользователю: на счету бывают
машины других задач.

## 5. После прогона

- В `REPORT.md` смотри, какие игры загружены, а какие нет и почему.
- **Цвет хозяев не определён.** На сайт такое не грузится. Разбери руками:
  1. посмотри `<slug>/home.json` (`roster_scores`, `team_split`) и владение
     мячом в `stats.txt`;
  2. проверь, в какой форме команды играли в прошлых матчах:
     `site.analytics(match_guid)` → `data.data.teams.home/away.colour`;
  3. загружай, только если ответ однозначный:
     ```bash
     ssh ... "docker exec -w /app/scripts football-runner sh -c 'export SPORTX_TOKEN=\$(python -c \"import full_run.site as s;print(s.token(refresh=True))\"); \
       python upload_analytics.py /data/results/$D/<slug>/stats.json --match-guid G --home-colour C --video-url https://www.youtube.com/watch?v=YT'"
     ```
- Главное правило: **лучше пустая графа, чем неверная.** Обе команды в одном
  цвете или владение мячом 100/0 — не грузить, а перечислить пользователю.
- Отчитайся пользователю: сколько игр на сайте, что не загружено и почему,
  сколько потрачено. Проверь, что машин этого прогона не осталось.

## Правила

- В `sportx` (ветка develop) не пушить без отдельной просьбы: там автодеплой.
- Правки в `football` коммить в main. Пушить только по просьбе.
- Ничего сомнительного на сайт не загружать.
