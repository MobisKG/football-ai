#!/bin/bash
# На машине: лёгкая установка, потом по каждой игре: видео из B2 → вырезки → выбор формы.
#   bash pod_kitchoice.sh   (рядом games.tsv: slug<TAB>форма A<TAB>форма B, и <slug>.pos.json)
cd /root/kc; export HF_HUB_ENABLE_HF_TRANSFER=0
pip install -q --root-user-action=ignore opencv-python-headless accelerate qwen-vl-utils 'transformers==4.49.0' b2sdk 2>&1 | tail -1
python -c "from huggingface_hub import snapshot_download as s; s('Qwen/Qwen2.5-VL-7B-Instruct')" 2>&1 | tail -1
echo SETUP_OK
while IFS=$'\t' read -r SLUG A B; do
  echo ">>> $SLUG"
  python /root/b2.py get source/$SLUG.mp4 $SLUG.mp4 >/dev/null
  python extract_crops2.py $SLUG.pos.json $SLUG.mp4 crops_$SLUG > $SLUG.crops.log 2>&1
  python kit_choice.py crops_$SLUG $SLUG.choice.json "$A" "$B" > $SLUG.choice.log 2>&1
  tail -1 $SLUG.choice.log; rm -f $SLUG.mp4
done < games.tsv
touch ALL_DONE
