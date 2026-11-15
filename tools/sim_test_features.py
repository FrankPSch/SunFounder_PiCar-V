# Usage: start the server with PICAR_SIM=1 (see PiCar_Setup_Log.md), then: python tools/sim_test_features.py
"""Simulation checks for the second feature round: toggles, exclusivity, media, QR, markers/rooms, treasure hunt, audio status."""
import ast
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8765'
failures = []


def cmd(command_name, **args):
    request = urllib.request.Request(BASE + '/api/cmd', data=json.dumps(dict(cmd=command_name, **args)).encode(), method='POST')
    return json.load(urllib.request.urlopen(request))


def state():
    return json.load(urllib.request.urlopen(BASE + '/api/state'))


def check(label, actual, expected):
    ok = actual == expected
    print('%-52s %s  (got %r, want %r)' % (label, 'PASS' if ok else 'FAIL', actual, expected))
    if not ok:
        failures.append(label)


def wait_for(predicate, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        current = state()
        if predicate(current):
            return current
        time.sleep(0.3)
    return state()


def enabled(current, name):
    return next(f['on'] for f in current['features'] if f['name'] == name)


names = [f['name'] for f in state()['features']]
FEATURES_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'remote_control', 'remote_control', 'features.py')
registry = next(node.value for node in ast.walk(ast.parse(open(FEATURES_SOURCE, encoding='utf-8').read()))
                if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == 'FEATURES')
check('all toggles of the FEATURES registry served', names, [key.value for key in registry.keys])
for name in names:
    if name not in ('watchdog', 'telemetry', 'power_warning', 'stuck_detection'):
        cmd('feature', name=name, on=False)
cmd('cam_center')

# exclusivity of camera-follow modes
cmd('feature', name='ball_tracking', on=True)
s = cmd('feature', name='face_tracking', on=True)
check('face on switches ball off', (enabled(s, 'face_tracking'), enabled(s, 'ball_tracking')), (True, False))
s = cmd('feature', name='animal_tracking', on=True)
check('animal on switches face off', (enabled(s, 'animal_tracking'), enabled(s, 'face_tracking')), (True, False))
s = wait_for(lambda c: c['tracker']['detectors'].get('animal'))
check('animal model missing is reported, no crash', 'missing' in s['tracker']['detectors'].get('animal', ''), True)
check('vision loop has no error', s['tracker']['error'], None)
cmd('track_animal', name='cat')
check('animal target selectable', state()['tracker']['animal'], 'cat')
cmd('feature', name='animal_tracking', on=False)

# media
photo_name = cmd('photo') and state()['media']['files'][0]['name']
check('photo saved', photo_name.startswith('photo-') and photo_name.endswith('.jpg'), True)
with urllib.request.urlopen(BASE + '/media/' + photo_name) as response:
    check('photo downloadable as JPEG', response.read(3), b'\xff\xd8\xff')
try:
    urllib.request.urlopen(BASE + '/media/../features.json')
    failures.append('path traversal allowed')
except urllib.error.HTTPError as error:
    check('path traversal refused', error.code, 404)
cmd('record', on=True)
time.sleep(2.5)
s = state()
check('recording running', bool(s['media']['recording']), True)
cmd('record', on=False)
video = next(f for f in state()['media']['files'] if f['name'].endswith('.mjpg'))
check('video file has data', video['bytes'] > 20000, True)
cmd('setting', name='timelapse_seconds', value=3)
cmd('feature', name='timelapse', on=True)
s = wait_for(lambda c: any(f['name'].startswith('timelapse-') for f in c['media']['files']), 10)
check('timelapse wrote a photo', any(f['name'].startswith('timelapse-') for f in s['media']['files']), True)
cmd('feature', name='timelapse', on=False)

# QR reader (sim world has a QR code next to the start view)
cmd('feature', name='qr_reader', on=True)
s = wait_for(lambda c: c['tracker']['qr'])
check('QR code read', (s['tracker']['qr'] or {}).get('text'), 'picar-sim-qr')
cmd('feature', name='qr_reader', on=False)

# markers and rooms
cmd('feature', name='markers', on=True)
s = wait_for(lambda c: c['tracker']['markers'])
check('ArUco marker 3 detected', [m['id'] for m in s['tracker']['markers']], [3])
cmd('room_name', id=3, name='kitchen')
s = wait_for(lambda c: c['rooms']['current']['room'] == 'kitchen')
check('named marker sets current room', s['rooms']['current']['room'], 'kitchen')
try:
    cmd('room_goto', name='kitchen')
    failures.append('goto allowed with navigation off')
except urllib.error.HTTPError as error:
    check('goto refused while room_navigation is off', error.code, 400)
cmd('feature', name='room_navigation', on=True)
cmd('room_goto', name='kitchen')
s = wait_for(lambda c: c['rooms']['nav']['state'] == 'approach')
check('navigation finds marker -> approach', s['rooms']['nav']['state'], 'approach')
check('navigation drives forward slowly', (s['car']['drive_direction'], s['car']['speed']), (1, 25))
cmd('room_cancel')
s = state()
check('cancel stops the car', (s['rooms']['nav']['state'], s['car']['drive_direction']), ('idle', 0))
cmd('feature', name='room_navigation', on=False)
cmd('feature', name='markers', on=False)

# treasure hunt
s = cmd('feature', name='treasure_hunt', on=True)
s = wait_for(lambda c: c['tracker']['treasure']['color'])
first = s['tracker']['treasure']['color']
check('treasure hunt names a colour', first in ('red', 'orange', 'yellow', 'green', 'blue', 'purple'), True)
cmd('treasure_next')
check('next colour differs', state()['tracker']['treasure']['color'] != first, True)
for _ in range(12):  # sim ball is red: steer the game to red and expect a point
    if state()['tracker']['treasure']['color'] == 'red':
        break
    cmd('treasure_next')
cmd('cam_step', value=5)
s = wait_for(lambda c: c['tracker']['treasure']['score'] >= 1, 25)
check('treasure found (red sim ball) -> score', s['tracker']['treasure']['score'] >= 1, True)
cmd('feature', name='treasure_hunt', on=False)

# microphone: no arecord on the PC -> reported, not crashed
cmd('feature', name='sound_level', on=True)
s = wait_for(lambda c: c['audio']['error'], 6)
check('mic error reported (no arecord in sim)', bool(s['audio']['error']), True)
cmd('feature', name='sound_level', on=False)

# pause_voice_commands: no conversation runs in the sim, so nothing is paused; the switch only flips
s = cmd('feature', name='pause_voice_commands', on=True)
check('pause_voice_commands on', enabled(s, 'pause_voice_commands'), True)
check('no conversation -> commands not paused', state()['audio']['commands_paused'], False)
s = cmd('feature', name='pause_voice_commands', on=False)
check('pause_voice_commands off', (enabled(s, 'pause_voice_commands'), state()['audio']['commands_paused']), (False, False))
check('pause_voice_commands does not drive', state()['car']['drive_direction'], 0)

# dashboard_pause_switch: only shows the pause switch in the dashboard; it must not flip the pause itself
with urllib.request.urlopen(BASE + '/static/dashboard.js') as response:
    dashboard_js = response.read().decode('utf-8')
check('dashboard.js builds the pause switch', ('data-feature="pause_voice_commands"' in dashboard_js, "'dashboard_pause_switch'" in dashboard_js), (True, True))
s = cmd('feature', name='dashboard_pause_switch', on=True)
check('dashboard_pause_switch on, pause stays off', (enabled(s, 'dashboard_pause_switch'), enabled(s, 'pause_voice_commands')), (True, False))
s = cmd('feature', name='pause_voice_commands', on=True)
check('pause toggles while shown', enabled(s, 'pause_voice_commands'), True)
cmd('feature', name='pause_voice_commands', on=False)
s = cmd('feature', name='dashboard_pause_switch', on=False)
check('dashboard_pause_switch off', (enabled(s, 'dashboard_pause_switch'), enabled(s, 'pause_voice_commands')), (False, False))
check('dashboard_pause_switch does not drive', state()['car']['drive_direction'], 0)

# mbrola_voice: no MBROLA voice files on the PC -> reported missing, speech stays plain espeak-ng
check('mbrola_voice off by default', state()['speaker'], {'mbrola': 'off', 'voices': {}})
s = cmd('feature', name='mbrola_voice', on=True)
check('mbrola_voice on', enabled(s, 'mbrola_voice'), True)
check('mbrola voice missing is reported (sim)', (s['speaker']['mbrola'].startswith('missing'), s['speaker']['voices']), (True, {}))
s = cmd('feature', name='mbrola_voice', on=False)
check('mbrola_voice off', (enabled(s, 'mbrola_voice'), s['speaker']), (False, {'mbrola': 'off', 'voices': {}}))
check('mbrola_voice does not drive', state()['car']['drive_direction'], 0)

# mbrola_check: switches mbrola_voice on and says which voice speaks; no voice files on the PC -> "fehlt"
check('mbrola_check off: no report', state()['mbrola_check'], {'said': None})
s = cmd('feature', name='mbrola_check', on=True)
check('mbrola_check on turns mbrola_voice on', (enabled(s, 'mbrola_check'), enabled(s, 'mbrola_voice')), (True, True))
check('mbrola_check reports missing voice (sim)', s['mbrola_check']['said'], 'Die weiche Stimme fehlt, ich spreche mit espeak.')
check('mbrola_check logs an event', any('voice check: espeak-ng' in e['text'] for e in s['events']), True)
s = cmd('feature', name='mbrola_voice', on=False)
check('mbrola_voice off is reported', s['mbrola_check']['said'], 'Die weiche Stimme ist aus, ich spreche mit espeak.')
s = cmd('feature', name='mbrola_check', on=False)
check('mbrola_check off clears the report', (enabled(s, 'mbrola_check'), s['mbrola_check']), (False, {'said': None}))
s = cmd('feature', name='mbrola_voice', on=True)
check('mbrola_check off: mbrola_voice flips silently', s['mbrola_check'], {'said': None})
cmd('feature', name='mbrola_voice', on=False)
check('mbrola_check does not drive', state()['car']['drive_direction'], 0)

check('events logged', len(state()['events']) >= 5, True)
print('RESULT:', 'ALL PASS' if not failures else 'FAILED: %s' % failures)
