'''
Rooms by ArUco marker (print 4x4 markers, ids 0-49, one per room/door; name them on the dashboard).
- observe(): called by the vision loop with the markers in view; tracks the current room and
  the room-to-room transitions seen while driving (the room graph).
- Navigation (feature room_navigation, EXPERIMENTAL, camera only):
  scan (sweep the camera) -> approach (camera keeps the marker centred, steering follows the camera,
  slow speed, keepalives) -> arrived (marker large enough). Fails with a report when the marker is not
  found (door closed / out of view) or lost twice (obstacle?). stuck_detection in tracker.py stops a blocked car.
'''
import json
import os
import threading
import time

from . import events, features
from .car import car, DEGREES_PER_TICK, STEER_MIN, STEER_MAX, CAM_HALF_RANGE_TICKS

ROOMS_FILE = os.path.join(features.DATA_DIR, 'rooms.json')
NAV_SPEED = 25
ARRIVE_SIZE = 0.22          # marker side / image width
LOST_SECONDS = 1.5
SCAN_STEP_TICKS = 12
CENTER_TOLERANCE = 0.06

_lock = threading.Lock()


def _load():
    try:
        with open(ROOMS_FILE) as rooms_file:
            data = json.load(rooms_file)
    except (OSError, ValueError):
        data = {}
    return {'names': {int(key): value for key, value in data.get('names', {}).items()},
            'transitions': data.get('transitions', {})}


def _save():
    os.makedirs(features.DATA_DIR, exist_ok=True)
    with open(ROOMS_FILE, 'w') as rooms_file:
        json.dump({'names': {str(key): value for key, value in _data['names'].items()},
                   'transitions': _data['transitions']}, rooms_file, indent=2, sort_keys=True)


_data = _load()
_seen = {}                  # marker id -> last seen time
_current = {'room': None, 'since': None}
nav = {'state': 'idle', 'target': None, 'target_id': None, 'message': None, 'retries': 0, 'last_seen': 0.0, 'scan_dir': 1}


def name_marker(marker_id, name):
    with _lock:
        if name:
            _data['names'][int(marker_id)] = name.strip()
        else:
            _data['names'].pop(int(marker_id), None)
        _save()


def observe(markers):
    now = time.time()
    for marker in markers:
        _seen[marker['id']] = now
    named = [marker for marker in markers if marker['id'] in _data['names']]
    if not named:
        return
    room = _data['names'][max(named, key=lambda marker: marker['size'])['id']]
    if room != _current['room']:
        if _current['room']:
            key = '%s -> %s' % (_current['room'], room)
            with _lock:
                _data['transitions'][key] = _data['transitions'].get(key, 0) + 1
                _save()
        _current.update(room=room, since=time.strftime('%H:%M:%S'))
        events.add('rooms', 'now in/at: ' + room)


def goto(room_name):
    if not features.enabled('room_navigation'):
        raise ValueError('feature room_navigation is off')
    ids = [marker_id for marker_id, name in _data['names'].items() if name == room_name]
    if not ids:
        raise ValueError('no marker named %r' % room_name)
    nav.update(state='scan', target=room_name, target_id=ids[0], message='searching', retries=0, last_seen=0.0, scan_dir=1)
    car.center_cam()
    events.add('rooms', 'going to ' + room_name, say='going to ' + room_name)


def cancel(reason='cancelled'):
    if nav['state'] in ('scan', 'approach'):
        car.stop('navigation ' + reason)
        nav.update(state='idle', message=reason)
        events.add('rooms', 'navigation %s' % reason)


def _finish(state, message, push=False):
    car.stop('navigation ' + state)
    nav.update(state=state, message=message)
    events.add('rooms', message, push=push, say=message)


def navigate_step(markers):
    '''One vision frame of navigation. markers: list of dicts with id, x, y (0..1), size (side/width).'''
    if nav['state'] not in ('scan', 'approach'):
        return
    if not features.enabled('room_navigation'):
        cancel('stopped (feature off)')
        return
    target = next((marker for marker in markers if marker['id'] == nav['target_id']), None)
    now = time.time()
    if nav['state'] == 'scan':
        if target:
            nav.update(state='approach', message='marker found, approaching', last_seen=now)
            return
        next_pan = car.pan_ticks + nav['scan_dir'] * SCAN_STEP_TICKS
        if abs(next_pan) > CAM_HALF_RANGE_TICKS * 0.8:
            if nav['scan_dir'] > 0:
                nav['scan_dir'] = -1
                car.set_cam(0, car.tilt_ticks)
            else:
                _finish('failed', '%s: marker not found - door closed or out of view?' % nav['target'], push=True)
            return
        car.set_cam(next_pan, car.tilt_ticks)
        return
    # approach
    if not target:
        if now - nav['last_seen'] > LOST_SECONDS:
            car.stop('marker lost')
            if nav['retries'] >= 1:
                _finish('failed', '%s: marker lost twice - obstacle in the way?' % nav['target'], push=True)
            else:
                nav.update(state='scan', retries=nav['retries'] + 1, scan_dir=1, message='marker lost, searching again')
        return
    nav['last_seen'] = now
    if target['size'] >= ARRIVE_SIZE:
        _finish('arrived', 'arrived at ' + nav['target'])
        return
    pan_steps = 0 if abs(target['x'] - 0.5) <= CENTER_TOLERANCE else (1 if target['x'] < 0.5 else -1)
    if pan_steps:
        car.move_cam(pan_steps, 0, step_ticks=4)
    car.set_steer(max(STEER_MIN, min(STEER_MAX, round(90 - car.pan_ticks * DEGREES_PER_TICK))))
    if car.drive_direction != 1:
        car.set_speed(NAV_SPEED)
        car.drive(1)
    car.keepalive()
    nav['message'] = 'approaching (marker %.0f%% of width)' % (100 * target['size'])


def describe():
    now = time.time()
    return {'names': {str(key): value for key, value in sorted(_data['names'].items())},
            'seen': {str(key): round(now - seen, 1) for key, seen in sorted(_seen.items()) if now - seen < 60},
            'current': _current, 'transitions': _data['transitions'], 'nav': dict(nav),
            'rooms': sorted(set(_data['names'].values()))}
