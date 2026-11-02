# Usage: python tools/cam_step_check.py [base_url]   (needs numpy + opencv on the PC)
"""Self-check on the real car: move the camera by N ticks and measure the image shift with phase correlation.
Expect: +pan (left) moves the scene right in the image (dx > 0); +tilt (up) moves it down (dy > 0);
shift grows roughly linearly with ticks. Needs a static, textured scene in front of the camera."""
import json
import sys
import time
import urllib.request

import cv2
import numpy as np

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://192.168.178.131:8000'
SNAPSHOT = BASE.rsplit(':', 1)[0] + ':8080/?action=snapshot'
SETTLE_S = 0.8


def cmd(command_name, **args):
    request = urllib.request.Request(BASE + '/api/cmd', data=json.dumps(dict(cmd=command_name, **args)).encode(), method='POST')
    return json.load(urllib.request.urlopen(request))['car']


def grey_frame():
    time.sleep(SETTLE_S)
    urllib.request.urlopen(SNAPSHOT).read()  # drop a frame captured during the move
    raw = urllib.request.urlopen(SNAPSHOT).read()
    frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    return np.float32(frame)


def shift(pan_ticks, tilt_ticks):
    cmd('cam_set', pan=0, tilt=0)
    reference = grey_frame()
    cmd('cam_set', pan=pan_ticks, tilt=tilt_ticks)
    moved = grey_frame()
    (dx, dy), response = cv2.phaseCorrelate(reference, moved, cv2.createHanningWindow(reference.shape[::-1], cv2.CV_32F))
    return dx, dy, response


cmd('feature', name='ball_tracking', on=False)
degrees_per_tick = cmd('cam_center')['degrees_per_tick']
print('%-16s %8s %8s %8s %10s' % ('move', 'dx px', 'dy px', 'quality', 'px/tick'))
for label, pan, tilt in (('pan +1', 1, 0), ('pan +5', 5, 0), ('pan +20', 20, 0), ('pan -20', -20, 0),
                         ('tilt +20', 0, 20), ('tilt -20', 0, -20)):
    dx, dy, quality = shift(pan, tilt)
    ticks = abs(pan or tilt)
    print('%-16s %8.1f %8.1f %8.2f %10.2f' % (label, dx, dy, quality, (abs(dx) if pan else abs(dy)) / ticks))
cmd('cam_center')
print('1 tick = %.3f deg (nominal)' % degrees_per_tick)
