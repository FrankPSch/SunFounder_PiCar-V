'''A dog, cat or person (switch animal_tracking): MobileNet-SSD from DATA_DIR/models (tools/install_extras.sh), ~1-2 fps.'''
import os

import cv2

from .common import box_target
from .. import features

SWITCHES, KIND, EVERY_N_FRAMES = ('animal_tracking',), 'target', 1
MODEL_DIR = os.path.join(features.DATA_DIR, 'models')
SSD_PROTOTXT = os.path.join(MODEL_DIR, 'MobileNetSSD_deploy.prototxt')
SSD_WEIGHTS = os.path.join(MODEL_DIR, 'MobileNetSSD_deploy.caffemodel')
SSD_CLASSES = ['background', 'aeroplane', 'bicycle', 'bird', 'boat', 'bottle', 'bus', 'car', 'cat', 'chair', 'cow',
               'diningtable', 'dog', 'horse', 'motorbike', 'person', 'pottedplant', 'sheep', 'sofa', 'train', 'tvmonitor']
ANIMAL_TARGETS = ('dog', 'cat', 'person')
SSD_MIN_CONFIDENCE = 0.5
_network = []               # loaded once


def detect(tracker, frame):
    if not _network:
        if not (os.path.exists(SSD_PROTOTXT) and os.path.exists(SSD_WEIGHTS)):
            tracker.status['detectors']['animal'] = 'missing MobileNet-SSD in %s (tools/install_extras.sh)' % MODEL_DIR
            return None
        _network.append(cv2.dnn.readNetFromCaffe(SSD_PROTOTXT, SSD_WEIGHTS))
        tracker.status['detectors']['animal'] = 'ok'
    blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
    _network[0].setInput(blob)
    detections = _network[0].forward()[0, 0]
    wanted = SSD_CLASSES.index(tracker.animal)
    best = None
    for _, class_id, confidence, left, top, right, bottom in detections:
        if int(class_id) == wanted and confidence >= SSD_MIN_CONFIDENCE and (best is None or confidence > best[0]):
            best = (confidence, left, top, right, bottom)
    if not best:
        return None
    confidence, left, top, right, bottom = best
    return box_target(left, top, right - left, bottom - top, 1.0, 1.0, '%s %.0f%%' % (tracker.animal, confidence * 100))
