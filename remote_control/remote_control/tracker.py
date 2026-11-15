'''
Vision loop on the Pi (no PC). One frame grab from the mjpg-streamer snapshot URL feeds every vision feature:
  ball_tracking / face_tracking / animal_tracking - camera follows the target by integer steps of car.cam_step
  tracking_drive  - "bull fight": steer towards the camera direction, drive while the target is far
  qr_reader       - decode QR codes
  markers         - ArUco markers -> rooms.observe(); room_navigation drives via rooms.navigate_step()
  treasure_hunt   - names a colour; found + centred for TREASURE_FRAMES frames = point, next colour
  stuck_detection - while driving, an image that stops changing means blocked -> stop
Each detector reports "missing" instead of crashing when its model/data file is not installed.
The detectors are one module each in detectors/ (switch detector_registry: run from detectors.DETECTORS; off: the
old fixed order below, calling the same functions).
'''
import random
import time
import urllib.request

import cv2
import numpy as np

from . import detectors, events, features, rooms, services, spoken_texts
from .car import car, SIM, DEGREES_PER_TICK, STEER_MIN, STEER_MAX
from .detectors import animal, ball, face, markers, qr, treasure

SNAPSHOT_URL = 'http://127.0.0.1:8080/?action=snapshot'
CENTER_TOLERANCE = 0.05          # dead band around the image centre, fraction of width/height
NEAR_SIZE = 0.38                 # tracking_drive stops when the target is this wide (fraction of image width)
DRIVE_SPEED = 30
STUCK_SECONDS = 1.5
STUCK_MEAN_DIFF = 2.0            # mean abs grey difference (0-255) between frames while driving

COLOR_PRESETS, ANIMAL_TARGETS = ball.COLOR_PRESETS, animal.ANIMAL_TARGETS   # used outside (tests, spoken_texts)
hsv_range_around, find_ball = ball.hsv_range_around, ball.find_ball
WORK_WIDTH, WORK_HEIGHT = ball.WORK_WIDTH, ball.WORK_HEIGHT
VISION_FEATURES = ('ball_tracking', 'face_tracking', 'animal_tracking', 'qr_reader', 'markers',
                   'treasure_hunt', 'room_navigation')
assert set(VISION_FEATURES) <= set(features.FEATURES), 'VISION_FEATURES names a feature missing from FEATURES'
assert set(features.TRACKING_FEATURES) <= set(VISION_FEATURES)
assert set(VISION_FEATURES) == {name for detector in detectors.DETECTORS for name in detector.SWITCHES}, 'VISION_FEATURES and the detectors differ'


class Tracker(object):
    def __init__(self):
        self.color = 'red'
        self.custom_ranges = None
        self.animal = 'dog'
        self.status = {'found': False, 'fps': 0.0, 'frames': 0, 'error': None, 'target': None,
                       'markers': [], 'qr': None, 'stuck_stops': 0,
                       'treasure': {'color': None, 'score': 0, 'progress': 0}, 'detectors': {}}
        self._previous_grey = None
        self._still_since = None
        features.on_change(self._on_feature_change)
        services.start('vision', self._loop)

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
            self.next_treasure()
        if name == 'room_navigation' and not is_on:
            rooms.cancel('stopped (feature off)')

    # ---- detectors (the old fixed order calls them; the code is in detectors/) ----
    def _detect_face(self, frame):
        return face.detect(self, frame)

    def _detect_animal(self, frame):
        return animal.detect(self, frame)

    def _detect_color(self, frame, color):
        return ball.detect_color(self, frame, color)

    def _detect_markers(self, frame):
        return markers.find(frame)

    def _read_qr(self, frame):
        qr.detect(self, frame)

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
        if features.enabled('detector_registry'):
            self._process_registered(frame)
            return
        markers = []
        if features.enabled('markers') or features.enabled('room_navigation'):
            markers = self._detect_markers(frame)
            rooms.observe(markers)
            rooms.navigate_step(markers)
        self.status['markers'] = markers
        if features.enabled('qr_reader') and self.status['frames'] % qr.EVERY_N_FRAMES == 0:
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

    def _process_registered(self, frame):
        '''Switch detector_registry: every active detector in detectors.DETECTORS; the first active target is followed.'''
        target, following = None, False
        for detector in detectors.DETECTORS:
            if not detectors.active(detector, features.enabled):
                getattr(detector, 'off', lambda tracker: None)(self)
            elif self.status['frames'] % detector.EVERY_N_FRAMES:
                continue
            elif detector.KIND == 'extra':
                detector.detect(self, frame)
            elif not following:
                target, following = detector.detect(self, frame), True
        if not following:
            self.status.update(found=False, target=None)
        elif rooms.nav['state'] not in ('scan', 'approach'):
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

    def next_treasure(self):
        previous = self.status['treasure']['color']
        self.status['treasure'].update(color=random.choice([c for c in COLOR_PRESETS if c != previous]), progress=0)
        color = self.status['treasure']['color']
        events.add('treasure', 'find something %s!' % color, say=spoken_texts.treasure(color))

    def _treasure_step(self, frame):
        return treasure.detect(self, frame)

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
