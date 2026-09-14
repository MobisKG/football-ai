# Назначение скриптов

| Скрипт | Что делает |
|---|---|
| `analyze_match.py` | Основной. Детекция, трекинг, команды, выгрузка позиций. Без рендера, каждый второй кадр, команда один раз на трек. 15 кадр/с на маке. |
| `stats.py` | Считает владение, передачи, потери, отборы из выгрузки позиций. |
| `analyze3.py` | То же плюс рендер размеченного видео. Медленнее, нужен для просмотра. |
| `analyze5.py` | Плюс распознавание номеров на футболках через EasyOCR. |
| `autolabel.py` | Автоматическая разметка кадров для дообучения под новую площадку. |
| `build_ds_all.py` | Сборка датасета из размеченных кадров нескольких матчей. |
| `setup.sh` | Установка окружения на чистой машине с CUDA. |
| `run_match.sh` | Обработка матча одной командой. |

## Как дообучить под новую площадку

    ffmpeg -i match.mp4 -vf "fps=1/4" -q:v 2 frames/f_%05d.jpg
    python autolabel.py --frames frames --out labels --device cuda \
        --extra-ball-weights weights/best.pt --slice 512
    python build_ds_all.py
    yolo detect train model=yolo11m.pt data=ds_all/yolo/data.yaml \
        epochs=40 imgsz=1280 batch=8 device=0

Тридцати-сорока эпох достаточно, дальше качество не растёт.

## Известные ловушки

- `main.py` из репозитория roboflow/sports не работает по SSH, он вызывает
  `cv2.imshow`. Использовать скрипты из этой папки.
- `yolo11m` при imgsz 1280 требует более 16 ГБ. На маке уходит в своп и считает
  одну эпоху за девять часов. На 16 ГБ ставить imgsz 960 либо batch 2.
- Маска газона в `autolabel.py` строится по цвету и берёт выпуклую оболочку.
  Без оболочки кромка проваливается внутрь поля на пятнистом газоне.

## Скрипты после перехода на схему «мак готовит, аренда учит»

| Скрипт | Где | Что делает |
|---|---|---|
| `mac_prepare.sh` | мак мини | кадры + авторазметка по всем видео, упаковка датасета |
| `setup_gdrive.sh` | мак мини | подключает Google Drive через rclone, один раз |
| `upload_to_gdrive.sh` | мак мини | заливает архив датасета на Диск |
| `fetch_gdrive.sh` | аренда | забирает архив, умеет rclone и публичную ссылку |
| `setup.sh` | аренда | окружение без apt: pip плюс статический ffmpeg |
| `run_train.sh` | аренда | собрать датасет и обучить |
| `train_best.py` | аренда | выбирает модель и batch по объёму видеопамяти |
| `analyze_match.py` | обе | детекция, трекинг, команды, выгрузка позиций |
| `stats.py` | обе | владение, передачи, потери, отборы |
| `analyze3.py` | обе | то же плюс рендер размеченного видео |
| `analyze5.py` | обе | плюс распознавание номеров |
| `autolabel.py` | обе | автоматическая разметка кадров |
| `build_ds_multi.py` | обе | сборка датасета из нескольких матчей |
| `run_match.sh` | аренда | обработать один матч готовой моделью |

Удалены как устаревшие: `prepare_data.sh` и `run_all.sh` качали видео с YouTube
прямо на арендованной машине, что невозможно. `build_ds_all.py` заменён на
`build_ds_multi.py`, который работает с произвольным числом матчей.

## Доступ к Google Drive

rclone подключается с областью `drive.file`, а не полной. Он видит только те
файлы, которые создал сам. Это важно, потому что конфигурацию с токеном
приходится копировать на арендованную чужую машину.

Аккаунт: adminroomskg@gmail.com. Вход делает человек в браузере, командой
`rclone authorize "drive" --drive-scope drive.file` на своём ноутбуке.

## Работа с RunPod через API

    export RUNPOD_KEY=$(grep '^KEY=' .env | cut -d= -f2-)
    curl -s -H "Authorization: Bearer $RUNPOD_KEY" https://rest.runpod.io/v1/pods
    curl -s -X POST https://rest.runpod.io/v1/pods -H "Authorization: Bearer $RUNPOD_KEY" \
         -H "Content-Type: application/json" -d @podreq.json
    curl -s -X DELETE -H "Authorization: Bearer $RUNPOD_KEY" https://rest.runpod.io/v1/pods/<id>

Список видеокарт и цен — через GraphQL `https://api.runpod.io/graphql`,
запрос `{ gpuTypes { id displayName memoryInGb lowestPrice(input:{gpuCount:1}) { uninterruptablePrice } } }`.

При создании пода: передавать несколько `gpuTypeIds`, иначе часто приходит
"no instances currently available". Образ брать не `devel`, он весит 10 ГБ и
долго тянется; достаточно runtime-варианта. SSH-ключ передаётся в `env.PUBLIC_KEY`.
