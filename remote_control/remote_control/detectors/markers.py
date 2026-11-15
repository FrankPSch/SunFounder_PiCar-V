'''ArUco room markers (switches markers, room_navigation): 4x4, ids 0-49 -> rooms.observe() and rooms.navigate_step().'''
import cv2
import numpy as np

from .. import rooms

SWITCHES, KIND, EVERY_N_FRAMES = ('markers', 'room_navigation'), 'extra', 1


def find(frame):
    small = cv2.resize(frame, (320, 240))
    dictionary = cv2.aruco.Dictionary_get(cv2.aruco.DICT_4X4_50)
    corners, ids, _ = cv2.aruco.detectMarkers(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), dictionary,
                                              parameters=cv2.aruco.DetectorParameters_create())
    found = []
    for marker_corners, marker_id in zip(corners, [] if ids is None else ids.flatten()):
        points = marker_corners[0]
        side = np.mean([np.linalg.norm(points[i] - points[(i + 1) % 4]) for i in range(4)])
        found.append({'id': int(marker_id), 'x': float(points[:, 0].mean() / 320), 'y': float(points[:, 1].mean() / 240),
                      'size': round(float(side / 320), 3)})
    return found


def detect(tracker, frame):
    found = find(frame)
    rooms.observe(found)
    rooms.navigate_step(found)
    tracker.status['markers'] = found


def off(tracker):
    tracker.status['markers'] = []
