'''A coloured ball (switch ball_tracking): the biggest blob in the colour's HSV ranges. Also used by treasure.py.'''
import cv2
import numpy as np

from .common import box_target

SWITCHES, KIND, EVERY_N_FRAMES = ('ball_tracking',), 'target', 1
WORK_WIDTH, WORK_HEIGHT = 160, 120
MIN_RADIUS_PX = 3
COLOR_PRESETS = {
    'red':    [((0, 120, 70), (10, 255, 255)), ((170, 120, 70), (179, 255, 255))],
    'orange': [((10, 120, 90), (22, 255, 255))],
    'yellow': [((22, 100, 100), (35, 255, 255))],
    'green':  [((36, 80, 60), (85, 255, 255))],
    'blue':   [((95, 120, 60), (130, 255, 255))],
    'purple': [((130, 80, 60), (165, 255, 255))],
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


def detect_color(tracker, frame, color):
    ball = find_ball(frame, tracker.hsv_ranges(color))
    if not ball:
        return None
    x, y, radius = ball
    target = box_target(x - radius, y - radius, 2 * radius, 2 * radius, WORK_WIDTH, WORK_HEIGHT, color)
    target['radius_px'] = round(radius, 1)
    return target


def detect(tracker, frame):
    return detect_color(tracker, frame, tracker.color)
