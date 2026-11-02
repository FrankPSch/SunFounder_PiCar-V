# Usage (ON THE PC, needs opencv-contrib + numpy; car on the floor, 1-2 m in front of a printed ArUco 4x4 marker):
#   python tools/drive_table.py calibrate 100        # marker exactly 100 cm away -> prints focal length in px
#   python tools/drive_table.py speed  --focal 520   # speed table: cm/s per speed setting
#   python tools/drive_table.py steer  --focal 520   # steering table: bearing change per steering angle
"""Measures driving with the camera: marker distance = focal * marker_side / side_px. Each burst is 0.5 s
forward then the same backward, so the car returns near its start. Aborts below MIN_DISTANCE_CM. NOT yet tested on the car."""
import argparse
import json
import math
import time
import urllib.request

import cv2
import numpy as np

BASE = 'http://192.168.178.131:8000'
SNAPSHOT = 'http://192.168.178.131:8080/?action=snapshot'
BURST_S = 0.5
MIN_DISTANCE_CM = 50


def cmd(command_name, **args):
    request = urllib.request.Request(BASE + '/api/cmd', data=json.dumps(dict(cmd=command_name, **args)).encode(), method='POST')
    return json.load(urllib.request.urlopen(request))['car']


def marker_view():
    time.sleep(0.6)
    urllib.request.urlopen(SNAPSHOT).read()
    frame = cv2.imdecode(np.frombuffer(urllib.request.urlopen(SNAPSHOT).read(), np.uint8), cv2.IMREAD_GRAYSCALE)
    corners, ids, _ = cv2.aruco.detectMarkers(frame, cv2.aruco.Dictionary_get(cv2.aruco.DICT_4X4_50))
    if ids is None:
        raise SystemExit('no marker in view')
    points = corners[0][0]
    side_px = np.mean([np.linalg.norm(points[i] - points[(i + 1) % 4]) for i in range(4)])
    return side_px, points[:, 0].mean() - frame.shape[1] / 2


def burst(direction):
    cmd('drive', direction=direction)
    end = time.time() + BURST_S
    while time.time() < end:
        time.sleep(0.15)
        cmd('keepalive')
    cmd('stop')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['calibrate', 'speed', 'steer'])
    parser.add_argument('distance_cm', nargs='?', type=float)
    parser.add_argument('--focal', type=float)
    parser.add_argument('--marker-cm', type=float, default=10.0)
    args = parser.parse_args()
    cmd('cam_center')
    if args.mode == 'calibrate':
        side_px, _ = marker_view()
        print('focal = %.0f px  (side %.1f px at %.0f cm, marker %.1f cm)' % (side_px * args.distance_cm / args.marker_cm,
                                                                           side_px, args.distance_cm, args.marker_cm))
        return
    distance = lambda side_px: args.focal * args.marker_cm / side_px
    rows = []
    settings = range(30, 101, 10) if args.mode == 'speed' else range(60, 121, 15)
    for setting in settings:
        side_before, offset_before = marker_view()
        if distance(side_before) < MIN_DISTANCE_CM:
            raise SystemExit('too close to the marker (%.0f cm) - move the car back' % distance(side_before))
        if args.mode == 'speed':
            cmd('speed', value=setting); cmd('steer', value=90)
        else:
            cmd('speed', value=40); cmd('steer', value=setting)
        burst(1)
        side_after, offset_after = marker_view()
        burst(-1)
        moved_cm = distance(side_before) - distance(side_after)
        bearing_change = math.degrees(math.atan(offset_after / args.focal) - math.atan(offset_before / args.focal))
        rows.append((setting, moved_cm / BURST_S, bearing_change))
        print('%-6s %4d  ->  %6.1f cm/s  bearing change %+5.1f deg' % (args.mode, setting, moved_cm / BURST_S, bearing_change))
    cmd('steer', value=90)


if __name__ == '__main__':
    main()
