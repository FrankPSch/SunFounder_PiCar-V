#!/bin/sh
# Runs ON THE PI (as pi). One-time install of the optional extras; each feature reports "missing" until done.
#   face_tracking   -> opencv-data (Haar cascades)
#   voice_prompts   -> espeak-ng
#   stop_word, voice_commands -> vosk 0.3.45 (armv7l wheel) + vosk-model-small-en-us-0.15 (41 MB)
#   animal_tracking -> MobileNet-SSD Caffe model (chuanqi305, 23 MB)
set -e
MODELS=/home/pi/picar-data/models
sudo apt-get install -y -q opencv-data espeak-ng unzip
sudo pip3 install -q vosk==0.3.45
mkdir -p "$MODELS" && cd "$MODELS"
if [ ! -d vosk-model-small-en-us-0.15 ]; then
    curl -fsSLO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    unzip -q vosk-model-small-en-us-0.15.zip && rm vosk-model-small-en-us-0.15.zip
fi
[ -f MobileNetSSD_deploy.prototxt ] || curl -fsSL -o MobileNetSSD_deploy.prototxt \
    https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/deploy.prototxt
[ -f MobileNetSSD_deploy.caffemodel ] || curl -fsSL -o MobileNetSSD_deploy.caffemodel \
    https://github.com/chuanqi305/MobileNet-SSD/raw/master/mobilenet_iter_73000.caffemodel
ls -la "$MODELS"
python3 -c "import vosk; print('vosk ok')"
echo "extras installed - restart: sudo systemctl restart picar"
