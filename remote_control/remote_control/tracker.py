'''
Vision loop on the Pi (no PC). One frame grab from the mjpg-streamer snapshot URL feeds every vision feature:
  ball_tracking / face_tracking / animal_tracking - camera follows the target by integer steps of car.cam_step
  tracking_drive  - "bull fight": steer towards the camera direction, drive while the target is far
  qr_reader       - decode QR codes
  markers         - ArUco markers -> rooms.observe(); room_navigation drives via rooms.navigate_step()
  treasure_hunt   - names a colour; found + centred for TREASURE_FRAMES frames = point, next colour
  stuck_detection - while driving, an image that stops changing means blocked -> stop
Each detector reports "missing" instead of crashing when its model/data file is not installed.
'''
import os
import random
import threading
import time
import urllib.request

import cv2
import numpy as np

from . import events, features, rooms
from .car import car, SIM, DEGREES_PER_TICK, STEER_MIN, STEER_MAX

SNAPSHOT_URL = 'http://127.0.0.1:8080/?action=snapshot'
WORK_WIDTH, WORK_HEIGHT = 160, 120
CENTER_TOLERANCE = 0.05          # dead band around the image centre, fraction of width/height
MIN_RADIUS_PX = 3
NEAR_SIZE = 0.38                 # tracking_drive stops when the target is this wide (fraction of image width)
DRIVE_SPEED = 30
QR_EVERY_N_FRAMES = 3
TREASURE_FRAMES = 5
TREASURE_MIN_RADIUS_PX = 8
STUCK_SECONDS = 1.5
STUCK_MEAN_DIFF = 2.0            # mean abs grey difference (0-255) between frames while driving

COLOR_PRESETS = {
    'red':    [((0, 120, 70), (10, 255, 255)), ((170, 120, 70), (179, 255, 255))],
    'orange': [((10, 120, 90), (22, 255, 255))],
    'yellow': [((22, 100, 100), (35, 255, 255))],
    'green':  [((36, 80, 60), (85, 255, 255))],
    'blue':   [((95, 120, 60), (130, 255, 255))],
    'purple': [((130, 80, 60), (165, 255, 255))],
}
MODEL_DIR = os.path.join(features.DATA_DIR, 'models')
SSD_PROTOTXT = os.path.join(MODEL_DIR, 'MobileNetSSD_deploy.prototxt')
SSD_WEIGHTS = os.path.join(MODEL_DIR, 'MobileNetSSD_deploy.caffemodel')
SSD_CLASSES = ['background', 'aeroplane', 'bicycle', 'bird', 'boat', 'bottle', 'bus', 'car', 'cat', 'chair', 'cow',
               'diningtable', 'dog', 'horse', 'motorbike', 'person', 'pottedplant', 'sheep', 'sofa', 'train', 'tvmonitor']
ANIMAL_TARGETS = ('dog', 'cat', 'person')
SSD_MIN_CONFIDENCE = 0.5
FACE_CASCADE_NAME = 'haarcascade_frontalface_default.xml'
FACE_CASCADE_DIRS = [getattr(getattr(cv2, 'data', None), 'haarcascades', ''), '/usr/share/opencv4/haarcascades/',
                     '/usr/share/opencv/haarcascades/']
VISION_FEATURES = ('ball_tracking', 'face_tracking', 'animal_tracking', 'qr_reader', 'markers',
                   'treasure_hunt', 'room_navigation')
assert set(VISION_FEATURES) <= set(features.FEATURES), 'VISION_FEATURES names a feature missing from FEATURES'
assert set(features.TRACKING_FEATURES) <= set(VISION_FEATURES)


def hsv_range_around(hue, saturation, value, hue_margin=10):
    low_s, low_v = max(int(saturation) - 60, 60), max(int(value) - 60, 50)
    low_h, high_h = int(hue) - hue_margin, int(hue) + hue_margin
    if low_h < 0:
        return [((0, low_s, low_v), (high_h, 255, 255)), ((180 + low_h, low_s, low_v), (179, 255, 255))]
    if high_h > 179:
        return [((low_h, low_s, low_v), (179, 255, 255)), ((0, low_s, low_v), (high_h - 180, 255, 255))]
    return [((low_h, low_s, low_v), (high_h, 255, 255))]


def find_ball(bgr_frame, hsv_ranges):
    '''Returns (x, y, radius) in work-image pixels, or None.'''
    small = cv2.resize(bgr_frame, (WORK_WIDTH, WORK_HEIGHT))
    hsv = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0), cv2.COLOR_BGR2HSV)
    mask = np.zeros(hsv.shape[:2], np.uint8)
    for low, high in hsv_ranges:
        mask |= cv2.inRange(hsv, np.array(low, np.uint8), np.array(high, np.uint8))
    mask = cv2.dilate(cv2.erode(mask, None, iterations=1), None, iterations=2)
    contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
    if not contours:
        return None
    (x, y), radius = cv2.minEnclosingCircle(max(contours, key=cv2.contourArea))
    return (x, y, radius) if radius >= MIN_RADIUS_PX else None


def _box_target(x, y, width, height, frame_width, frame_height, label):
    return {'x': (x + width / 2) / frame_width, 'y': (y + height / 2) / frame_height,
            'w': width / frame_width, 'h': height / frame_height, 'size': width / frame_width, 'label': label}


class Tracker(object):
    def __init__(self):
        self.color = 'red'
        self.custom_ranges = None
        self.animal = 'dog'
        self.status = {'found': False, 'fps': 0.0, 'frames': 0, 'error': None, 'target': None,
                       'markers': [], 'qr': None, 'stuck_stops': 0,
                       'treasure': {'color': None, 'score': 0, 'progress': 0}, 'detectors': {}}
        self._face_cascade = None
        self._ssd = None
        self._previous_grey = None
        self._still_since = None
        features.on_change(self._on_feature_change)
        threading.Thread(target=self._loop, name='vision', daemon=True).start()

    # ---- settings ----
    def hsv_ranges(self, color=None):
        color = color or self.color
        return self.custom_ranges if color == 'custom' else COLOR_PRESETS[color]

    def set_color(self, name):
        if name not in COLOR_PRESETS and not (name == 'custom' and self.custom_ranges):
            raise ValueError('unknown colour %r' % name)
        self.color = name

    def set_animal(self, name):
        if name not in ANIMAL_TARGETS:
            raise ValueError('unknown animal target %r' % name)
        self.animal = name

    def teach(self):
        '''Learn the colour of whatever is in the centre of the image (hold the ball there).'''
        hsv = cv2.cvtColor(cv2.resize(self.grab_frame(), (WORK_WIDTH, WORK_HEIGHT)), cv2.COLOR_BGR2HSV)
        centre = hsv[WORK_HEIGHT // 2 - 8:WORK_HEIGHT // 2 + 8, WORK_WIDTH // 2 - 8:WORK_WIDTH // 2 + 8]
        hue, saturation, value = [int(np.median(centre[:, :, channel])) for channel in range(3)]
        self.custom_ranges = hsv_range_around(hue, saturation, value)
        self.color = 'custom'
        events.add('vision', 'learned custom colour hsv=(%d,%d,%d)' % (hue, saturation, value))
        return {'hsv': [hue, saturation, value]}

    def grab_frame(self):
        if SIM:
            from .sim import world
            return world.render()
        with urllib.request.urlopen(SNAPSHOT_URL, timeout=2) as response:
            frame = cv2.imdecode(np.frombuffer(response.read(), np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise IOError('snapshot could not be decoded')
        return frame

    def _on_feature_change(self, name, is_on):
        if name in features.TRACKING_FEATURES + ('tracking_drive', 'treasure_hunt') and not is_on:
            car.stop('%s off' % name)
        if name == 'treasure_hunt' and is_on:
            self._next_treasure()
        if name == 'room_navigation' and not is_on:
            rooms.cancel('stopped (feature off)')

    # ---- detectors ----
    def _detect_face(self, frame):
        if self._face_cascade is None:
            path = next((os.path.join(folder, FACE_CASCADE_NAME) for folder in FACE_CASCADE_DIRS
                         if folder and os.path.exists(os.path.join(folder, FACE_CASCADE_NAME))), None)
            if not path:
                self.status['detectors']['face'] = 'missing ' + FACE_CASCADE_NAME + ' (apt install opencv-data)'
                return None
            self._face_cascade = cv2.CascadeClassifier(path)
            self.status['detectors']['face'] = 'ok'
        grey = cv2.cvtColor(cv2.resize(frame, (320, 240)), cv2.COLOR_BGR2GRAY)
        faces = self._face_cascade.detectMultiScale(grey, scaleFactor=1.2, minNeighbors=5, minSize=(24, 24))
        if len(faces) == 0:
            return None
        x, y, width, height = max(faces, key=lambda face: face[2] * face[3])
        return _box_target(x, y, width, height, 320, 240, 'face')

    def _detect_animal(self, frame):
        if self._ssd is None:
            if not (os.path.exists(SSD_PROTOTXT) and os.path.exists(SSD_WEIGHTS)):
                self.status['detectors']['animal'] = 'missing MobileNet-SSD in %s (tools/install_extras.sh)' % MODEL_DIR
                return None
            self._ssd = cv2.dnn.readNetFromCaffe(SSD_PROTOTXT, SSD_WEIGHTS)
            self.status['detectors']['animal'] = 'ok'
        blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
        self._ssd.setInput(blob)
        detections = self._ssd.forward()[0, 0]
        wanted = SSD_CLASSES.index(self.animal)
        best = None
        for _, class_id, confidence, left, top, right, bottom in detections:
            if int(class_id) == wanted and confidence >= SSD_MIN_CONFIDENCE and (best is None or confidence > best[0]):
                best = (confidence, left, top, right, bottom)
        if not best:
            return None
        confidence, left, top, right, bottom = best
        target = _box_target(left, top, right - left, bottom - top, 1.0, 1.0, '%s %.0f%%' % (self.animal, confidence * 100))
        return target

    def _detect_color(self, frame, color):
        ball = find_ball(frame, self.hsv_ranges(color))
        if not ball:
            return None
        x, y, radius = ball
        target = _box_target(x - radius, y - radius, 2 * radius, 2 * radius, WORK_WIDTH, WORK_HEIGHT, color)
        target['radius_px'] = round(radius, 1)
        return target

    def _detect_markers(self, frame):
        small = cv2.resize(frame, (320, 240))
        dictionary = cv2.aruco.Dictionary_get(cv2.aruco.DICT_4X4_50)
        corners, ids, _ = cv2.aruco.detectMarkers(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), dictionary,
                                                  parameters=cv2.aruco.DetectorParameters_create())
        markers = []
        for marker_corners, marker_id in zip(corners, [] if ids is None else ids.flatten()):
            points = marker_corners[0]
            side = np.mean([np.linalg.norm(points[i] - points[(i + 1) % 4]) for i in range(4)])
            markers.append({'id': int(marker_id), 'x': float(points[:, 0].mean() / 320), 'y': float(points[:, 1].mean() / 240),
                            'size': round(float(side / 320), 3)})
        return markers

    def _read_qr(self, frame):
        text, points, _ = cv2.QRCodeDetector().detectAndDecode(frame)
        if text and (not self.status['qr'] or self.status['qr']['text'] != text):
            events.add('vision', 'QR code: ' + text, say='QR code')
        if text:
            self.status['qr'] = {'text': text, 'time': time.strftime('%H:%M:%S')}

    # ---- loop ----
    def _active(self):
        return (any(features.enabled(name) for name in VISION_FEATURES)
                or (features.enabled('stuck_detection') and car.drive_direction != 0 and not SIM))

    def _loop(self):
        window_start, window_frames = time.time(), 0
        while True:
            if not self._active():
                self.status.update(found=False, fps=0.0, target=None, markers=[])
                self._previous_grey, self._still_since = None, None
                time.sleep(0.2)
                continue
            try:
                frame = self.grab_frame()
                self.status['error'] = None
                self._process(frame)
            except Exception as error:
                self.status['error'] = '%s: %s' % (type(error).__name__, error)
                time.sleep(0.5)
            self.status['frames'] += 1
            window_frames += 1
            if time.time() - window_start >= 2:
                self.status['fps'] = round(window_frames / (time.time() - window_start), 1)
                window_start, window_frames = time.time(), 0
            if SIM:
                time.sleep(0.05)

    def _process(self, frame):
        if features.enabled('stuck_detection') and not SIM:
            self._check_stuck(frame)
        markers = []
        if features.enabled('markers') or features.enabled('room_navigation'):
            markers = self._detect_markers(frame)
            rooms.observe(markers)
            rooms.navigate_step(markers)
        self.status['markers'] = markers
        if features.enabled('qr_reader') and self.status['frames'] % QR_EVERY_N_FRAMES == 0:
            self._read_qr(frame)

        navigating = rooms.nav['state'] in ('scan', 'approach')
        target = None
        if features.enabled('treasure_hunt'):
            target = self._treasure_step(frame)
        elif features.enabled('ball_tracking'):
            target = self._detect_color(frame, self.color)
        elif features.enabled('face_tracking'):
            target = self._detect_face(frame)
        elif features.enabled('animal_tracking'):
            target = self._detect_animal(frame)
        else:
            self.status.update(found=False, target=None)
            return
        if not navigating:
            self._follow(target)

    def _check_stuck(self, frame):
        grey = cv2.cvtColor(cv2.resize(frame, (80, 60)), cv2.COLOR_BGR2GRAY).astype(np.int16)
        if car.drive_direction == 0 or self._previous_grey is None:
            self._previous_grey, self._still_since = grey, None
            return
        mean_diff = float(np.mean(np.abs(grey - self._previous_grey)))
        self._previous_grey = grey
        if mean_diff >= STUCK_MEAN_DIFF:
            self._still_since = None
        elif self._still_since is None:
            self._still_since = time.time()
        elif time.time() - self._still_since > STUCK_SECONDS:
            self.status['stuck_stops'] += 1
            self._still_since = None
            car.stop('stuck: image not changing')
            rooms.cancel('stopped: car seems blocked')
            events.add('vision', 'stuck: image did not change for %.1f s while driving - obstacle?' % STUCK_SECONDS, push=True)

    def _next_treasure(self):
        previous = self.status['treasure']['color']
        self.status['treasure'].update(color=random.choice([c for c in COLOR_PRESETS if c != previous]), progress=0)
        color = self.status['treasure']['color']
        events.add('treasure', 'find something %s!' % color, say='find something %s' % color)

    def _treasure_step(self, frame):
        treasure = self.status['treasure']
        if not treasure['color']:
            self._next_treasure()
        target = self._detect_color(frame, treasure['color'])
        if target and target['radius_px'] >= TREASURE_MIN_RADIUS_PX and abs(target['x'] - 0.5) < 0.12 and abs(target['y'] - 0.5) < 0.12:
            treasure['progress'] += 1
            if treasure['progress'] >= TREASURE_FRAMES:
                treasure['score'] += 1
                events.add('treasure', 'found %s! score %d' % (treasure['color'], treasure['score']), say='well done')
                self._next_treasure()
        else:
            treasure['progress'] = 0
        return target

    def _follow(self, target):
        if target is None:
            self.status.update(found=False, target=None)
            if features.enabled('tracking_drive') and car.drive_direction:
                car.stop('target lost')
            return
        self.status.update(found=True, target=target, x=round(target['x'], 3), y=round(target['y'], 3),
                           r=round(target['w'] / 2, 3), radius_px=target.get('radius_px'))
        pan_steps = 0 if abs(target['x'] - 0.5) <= CENTER_TOLERANCE else (1 if target['x'] < 0.5 else -1)
        tilt_steps = 0 if abs(target['y'] - 0.5) <= CENTER_TOLERANCE else (1 if target['y'] < 0.5 else -1)
        if pan_steps or tilt_steps:
            car.move_cam(pan_steps, tilt_steps)
        self.status['centred'] = not (pan_steps or tilt_steps)
        if features.enabled('tracking_drive'):
            camera_bearing_deg = car.pan_ticks * DEGREES_PER_TICK
            car.set_steer(max(STEER_MIN, min(STEER_MAX, round(90 - camera_bearing_deg))))
            if target['size'] < NEAR_SIZE:
                if car.drive_direction != 1:
                    car.set_speed(DRIVE_SPEED)
                    car.drive(1)
                car.keepalive()
            else:
                car.stop('target near')

    def describe(self):
        mode = next((name for name in ('treasure_hunt',) + features.TRACKING_FEATURES if features.enabled(name)), None)
        return dict(self.status, color=self.color, animal=self.animal, mode=mode,
                    colors=sorted(COLOR_PRESETS) + (['custom'] if self.custom_ranges else []), animals=list(ANIMAL_TARGETS),
                    enabled=features.enabled('ball_tracking'), drive=features.enabled('tracking_drive'))


tracker = Tracker()
