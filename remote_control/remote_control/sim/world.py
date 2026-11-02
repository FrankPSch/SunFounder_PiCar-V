'''
Simulated camera for PICAR_SIM=1: a fixed room texture with a red ball moving slowly on a Lissajous path.
The visible window follows the camera pan/tilt ticks, so the tracker loop can be tested end to end.
'''
import math
import time

import cv2
import numpy as np

WORLD_WIDTH, WORLD_HEIGHT = 1600, 1200
VIEW_WIDTH, VIEW_HEIGHT = 320, 240
PIXELS_PER_TICK = 2.0
BALL_RADIUS = 18

_rng = np.random.RandomState(7)
_room = np.full((WORLD_HEIGHT, WORLD_WIDTH, 3), (90, 110, 120), np.uint8)
for _ in range(120):
    corner = (_rng.randint(0, WORLD_WIDTH), _rng.randint(0, WORLD_HEIGHT))
    size = (_rng.randint(20, 160), _rng.randint(20, 160))
    shade = tuple(int(value) for value in _rng.randint(60, 200, 3))
    shade = (shade[0], shade[1], min(shade[2], shade[1]))  # no red-dominant patches
    cv2.rectangle(_room, corner, (corner[0] + size[0], corner[1] + size[1]), shade, -1)

SIM_MARKER_ID = 3
SIM_QR_TEXT = 'picar-sim-qr'


def _paste_white_framed(image, top_left, side):
    tile = cv2.cvtColor(cv2.resize(image, (side, side), interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
    x, y = top_left
    _room[y - 10:y + side + 10, x - 10:x + side + 10] = 255
    _room[y:y + side, x:x + side] = tile


if hasattr(cv2, 'aruco'):  # opencv-contrib; ArUco marker in view at the start position
    _paste_white_framed(cv2.aruco.drawMarker(cv2.aruco.Dictionary_get(cv2.aruco.DICT_4X4_50), SIM_MARKER_ID, 120), (660, 500), 60)
if hasattr(cv2, 'QRCodeEncoder'):
    _paste_white_framed(cv2.QRCodeEncoder.create().encode(SIM_QR_TEXT), (860, 560), 90)


def ball_position(at_time=None):
    t = time.time() if at_time is None else at_time
    return (WORLD_WIDTH / 2 + 250 * math.sin(t * 0.35), WORLD_HEIGHT / 2 + 150 * math.sin(t * 0.22 + 1.0))


def view_centre():
    from ..car import car
    return (WORLD_WIDTH / 2 - car.pan_ticks * PIXELS_PER_TICK, WORLD_HEIGHT / 2 - car.tilt_ticks * PIXELS_PER_TICK)


def render():
    centre_x, centre_y = view_centre()
    left, top = int(centre_x - VIEW_WIDTH / 2), int(centre_y - VIEW_HEIGHT / 2)
    left = max(0, min(WORLD_WIDTH - VIEW_WIDTH, left))
    top = max(0, min(WORLD_HEIGHT - VIEW_HEIGHT, top))
    frame = _room[top:top + VIEW_HEIGHT, left:left + VIEW_WIDTH].copy()
    ball_x, ball_y = ball_position()
    cv2.circle(frame, (int(ball_x - left), int(ball_y - top)), BALL_RADIUS, (30, 30, 220), -1)
    cv2.putText(frame, 'SIM', (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return frame


def mjpeg_frames():
    while True:
        ok, jpeg = cv2.imencode('.jpg', render())
        yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n'
        time.sleep(0.1)
