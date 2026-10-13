# Образ рабочей машины RunPod для разбора матчей: всё, что pod_setup.sh раньше ставил
# при каждом запуске. Только открытые библиотеки и публичные модели — наших весов,
# кода и ключей здесь нет (они приходят с B2 и с управляющей машины).
# Qwen2.5-VL (16 ГБ) не кладём: он скачивается с HuggingFace за 1–2 минуты.
# CUDA 12.1: на хостах Community драйверы часто старше 12.4 — с 12.4 машины падали с «CUDA unknown error» (13.10)
FROM runpod/pytorch:2.2.0-py3.10-cuda12.1.1-devel-ubuntu22.04

# torch 2.4 под CUDA 12.1 — та же версия, что была на машинах раньше
RUN pip install -q --no-cache-dir --root-user-action=ignore torch==2.4.0 torchvision==0.19.0 --index-url https://download.pytorch.org/whl/cu121

RUN curl -sL -o /tmp/ff.tar.xz https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz \
 && mkdir -p /tmp/ff && tar xJf /tmp/ff.tar.xz -C /tmp/ff --strip-components=1 \
 && install -m755 /tmp/ff/bin/ffmpeg /tmp/ff/bin/ffprobe /usr/local/bin/ && rm -rf /tmp/ff /tmp/ff.tar.xz

# transformers новее 4.49 требуют torch >= 2.5 и молча отключают модели; закрепляем
RUN pip install -q --no-cache-dir --root-user-action=ignore \
      ultralytics supervision opencv-python-headless umap-learn scikit-learn \
      more-itertools tqdm yt-dlp gdown pyyaml sentencepiece protobuf accelerate qwen-vl-utils \
      Cython scipy tensorboard yacs h5py imageio future six b2sdk 'transformers==4.49.0'

RUN git clone -q https://github.com/roboflow/sports.git /opt/sports \
 && git clone -q https://github.com/KaiyangZhou/deep-person-reid.git /root/deep-person-reid

# SigLIP классификатора команд — маленький, кладём сразу
RUN python -c "from huggingface_hub import snapshot_download as s; s('google/siglip-base-patch16-224')"

RUN echo "football-pod $(date +%F)" > /etc/football-pod
