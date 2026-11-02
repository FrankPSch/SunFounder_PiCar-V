'''
Ball tracking on the Pi (no PC): colour mask -> largest blob -> move the camera
by integer steps of car.cam_step ticks per frame until the ball is centred.
Frames come from the running mjpg-streamer snapshot URL, so the live stream stays available.
Optional (feature tracking_drive): steer towards the camera direction and drive while the ball is far.
'''
import threading
import time
import urllib.request

import cv2
import numpy as np

from . import features
from .car import car, SIM, DEGREES_PER_TICK, STEER_MIN, STEER_MAX

SNAPSHOT_URL = 'http://127.0.0.1:8080/?action=snapshot'
WORK_WIDTH, WORK_HEIGHT = 160, 120
CENTER_TOLERANCE_PX = 8          # dead band around the image centre, in work-image pixels
MIN_RADIUS_PX = 3
NEAR_RADIUS_PX = 30              # tracking_drive stops when the ball looks this big
DRIVE_SPEED = 30

COLOR_PRESETS = {
    'red':    [((0, 120, 70), (10, 255, 255)), ((170, 120, 70), (179, 255, 255))],
    'orange': [((10, 120, 90), (22, 255, 255))],
    'yellow': [((22, 100, 100), (35, 255, 255))],
    'green':  [((36, 80, 60), (85, 255, 255))],
    'blue':   [((95, 120, 60), (130, 255, 255))],
}


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


class Tracker(object):
    def __init__(self):
        self.color = 'red'
        self.custom_ranges = None
        self.status = {'found': False, 'fps': 0.0, 'frames': 0, 'error': None}
        features.on_change(self._on_feature_change)
        threading.Thread(target=self._loop, name='tracker', daemon=True).start()

    def hsv_ranges(self):
        return self.custom_ranges if self.color == 'custom' else COLOR_PRESETS[self.color]

    def set_color(self, name):
        if name not in COLOR_PRESETS and not (name == 'custom' and self.custom_ranges):
            raise ValueError('unknown colour %r' % name)
        self.color = name

    def teach(self):
        '''Learn the colour of whatever is in the centre of the image (hold the ball there).'''
        hsv = cv2.cvtColor(cv2.resize(self.grab_frame(), (WORK_WIDTH, WORK_HEIGHT)), cv2.COLOR_BGR2HSV)
        centre = hsv[WORK_HEIGHT // 2 - 8:WORK_HEIGHT // 2 + 8, WORK_WIDTH // 2 - 8:WORK_WIDTH // 2 + 8]
        hue, saturation, value = [int(np.median(centre[:, :, channel])) for channel in range(3)]
        self.custom_ranges = hsv_range_around(hue, saturation, value)
        self.color = 'custom'
        print('tracker: learned custom colour hsv=(%d,%d,%d)' % (hue, saturation, value))
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
        if name in ('ball_tracking', 'tracking_drive') and not is_on:
            car.stop('tracking off')

    def _loop(self):
        window_start, window_frames = time.time(), 0
        while True:
            if not features.enabled('ball_tracking'):
                self.status.update(found=False, fps=0.0)
                time.sleep(0.2)
                continue
            try:
                ball = find_ball(self.grab_frame(), self.hsv_ranges())
                self.status['error'] = None
            except Exception as error:
                ball, self.status['error'] = None, str(error)
                time.sleep(0.5)
            self._follow(ball)
            self.status['frames'] += 1
            window_frames += 1
            if time.time() - window_start >= 2:
                self.status['fps'] = round(window_frames / (time.time() - window_start), 1)
                window_start, window_frames = time.time(), 0
            if SIM:
                time.sleep(0.05)

    def _follow(self, ball):
        if ball is None:
            self.status.update(found=False)
            if features.enabled('tracking_drive'):
                car.stop('ball lost')
            return
        x, y, radius = ball
        self.status.update(found=True, x=round(x / WORK_WIDTH, 3), y=round(y / WORK_HEIGHT, 3),
                           r=round(radius / WORK_WIDTH, 3), radius_px=round(radius, 1))
        pan_steps = 0 if abs(x - WORK_WIDTH / 2) <= CENTER_TOLERANCE_PX else (1 if x < WORK_WIDTH / 2 else -1)
        tilt_steps = 0 if abs(y - WORK_HEIGHT / 2) <= CENTER_TOLERANCE_PX else (1 if y < WORK_HEIGHT / 2 else -1)
        if pan_steps or tilt_steps:
            car.move_cam(pan_steps, tilt_steps)
        self.status['centred'] = not (pan_steps or tilt_steps)
        if features.enabled('tracking_drive'):
            camera_bearing_deg = car.pan_ticks * DEGREES_PER_TICK
            car.set_steer(max(STEER_MIN, min(STEER_MAX, round(90 - camera_bearing_deg))))
            if radius < NEAR_RADIUS_PX:
                if car.drive_direction != 1:
                    car.set_speed(DRIVE_SPEED)
                    car.drive(1)
                car.keepalive()
            else:
                car.stop('ball near')

    def describe(self):
        return dict(self.status, color=self.color, colors=sorted(COLOR_PRESETS) + (['custom'] if self.custom_ranges else []),
                    enabled=features.enabled('ball_tracking'), drive=features.enabled('tracking_drive'))


tracker = Tracker()
