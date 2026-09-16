# Управляющий узел прогона: живёт на сервере sportx, чтобы прогон не зависел
# от ноутбука. Видеокарта здесь не нужна — вся тяжёлая работа на арендованных
# машинах RunPod; этому контейнеру нужны только python, ssh и сеть.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client curl ca-certificates tar xz-utils procps \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir yt-dlp
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/scripts FB_SSH_KEY=/app/ssh/id_ed25519
CMD ["bash", "/app/server/watch_loop.sh"]
