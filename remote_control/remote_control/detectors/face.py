'''A face (switch face_tracking): OpenCV's Haar cascade from opencv-data; reports "missing" when it is not installed.'''
import os

import cv2

from .common import box_target

SWITCHES, KIND, EVERY_N_FRAMES = ('face_tracking',), 'target', 1
FACE_CASCADE_NAME = 'haarcascade_frontalface_default.xml'
FACE_CASCADE_DIRS = [getattr(getattr(cv2, 'data', None), 'haarcascades', ''), '/usr/share/opencv4/haarcascades/',
                     '/usr/share/opencv/haarcascades/']
_cascade = []               # loaded once


def detect(tracker, frame):
    if not _cascade:
        path = next((os.path.join(folder, FACE_CASCADE_NAME) for folder in FACE_CASCADE_DIRS
                     if folder and os.path.exists(os.path.join(folder, FACE_CASCADE_NAME))), None)
        if not path:
            tracker.status['detectors']['face'] = 'missing ' + FACE_CASCADE_NAME + ' (apt install opencv-data)'
            return None
        _cascade.append(cv2.CascadeClassifier(path))
        tracker.status['detectors']['face'] = 'ok'
    grey = cv2.cvtColor(cv2.resize(frame, (320, 240)), cv2.COLOR_BGR2GRAY)
    faces = _cascade[0].detectMultiScale(grey, scaleFactor=1.2, minNeighbors=5, minSize=(24, 24))
    if len(faces) == 0:
        return None
    x, y, width, height = max(faces, key=lambda face: face[2] * face[3])
    return box_target(x, y, width, height, 320, 240, 'face')
