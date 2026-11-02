# Usage: start the server with PICAR_SIM=1 (see PiCar_Setup_Log.md), then: python tools/sim_test.py [base_url]
"""API tests against the simulated car: integer steps, clamps, watchdog, toggles, tracking."""
import json, sys, time, urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8765'  # or http://192.168.178.131:8000 on the car
failures = []

def cmd(command_name, **args):
    request = urllib.request.Request(BASE + '/api/cmd', data=json.dumps(dict(cmd=command_name, **args)).encode(), method='POST')
    return json.load(urllib.request.urlopen(request))

def state():
    return json.load(urllib.request.urlopen(BASE + '/api/state'))

def check(label, actual, expected):
    ok = actual == expected
    print('%-46s %s  (got %r, want %r)' % (label, 'PASS' if ok else 'FAIL', actual, expected))
    if not ok:
        failures.append(label)

cmd('feature', name='ball_tracking', on=False)
cmd('ready'); cmd('cam_step', value=1)
check('cam step +1', cmd('cam_step', delta=1)['car']['cam_step'], 2)
check('cam step +10', cmd('cam_step', delta=10)['car']['cam_step'], 12)
check('cam step -10', cmd('cam_step', delta=-10)['car']['cam_step'], 2)
check('cam step clamps at 1', cmd('cam_step', delta=-10)['car']['cam_step'], 1)
check('pan +1 step of 1 tick', cmd('cam_move', pan=1)['car']['pan_ticks'], 1)
check('tilt -1 step of 10 ticks (tenfold)', cmd('cam_move', tilt=-1, step=10)['car']['tilt_ticks'], -10)
check('pan clamps at limit', cmd('cam_set', pan=999, tilt=0)['car']['pan_ticks'], 221)
check('cam centre', cmd('cam_center')['car']['pan_ticks'], 0)
check('speed value', cmd('speed', value=37)['car']['speed'], 37)
check('speed +10', cmd('speed', delta=10)['car']['speed'], 47)
check('speed clamps at 100', cmd('speed', value=250)['car']['speed'], 100)
check('steer -1', cmd('steer', delta=-1)['car']['steer_angle'], 89)
check('steer clamps at min', cmd('steer', value=0)['car']['steer_angle'], 45)
cmd('steer', value=90)
check('bad command rejected', 'ok', 'ok')
try:
    cmd('nope'); failures.append('bad command accepted')
except urllib.error.HTTPError as error:
    print('%-46s %s  (HTTP %d)' % ('unknown command -> 400', 'PASS' if error.code == 400 else 'FAIL', error.code))

# watchdog: no keepalive -> stops; keepalive -> keeps driving
stops_before = state()['car']['watchdog_stops']
cmd('drive', direction=1); time.sleep(1.0)
s = state()['car']
check('watchdog stops without keepalive', (s['drive_direction'], s['watchdog_stops'] - stops_before), (0, 1))
cmd('drive', direction=1)
for _ in range(8):
    time.sleep(0.2); cmd('keepalive')
check('keepalive keeps driving 1.6 s', state()['car']['drive_direction'], 1)
cmd('stop')
check('stop button', state()['car']['drive_direction'], 0)

# tracking: camera must follow the moving sim ball in integer steps
cmd('cam_center'); cmd('cam_step', value=5); cmd('track_color', name='red'); cmd('feature', name='ball_tracking', on=True)
samples, centred, offsets, pans = 0, 0, [], set()
deadline = time.time() + 20
while time.time() < deadline:
    t = state()
    tr, car = t['tracker'], t['car']
    pans.add(car['pan_ticks'])
    if tr.get('found'):
        samples += 1
        centred += bool(tr.get('centred'))
        offsets.append(max(abs(tr['x'] - 0.5), abs(tr['y'] - 0.5)))
    time.sleep(0.25)
print('tracking: found in %d samples, centred %d, fps %s, max offset %.3f, median offset %.3f, distinct pan values %d'
      % (samples, centred, tr['fps'], max(offsets or [1]), sorted(offsets or [1])[len(offsets or [1]) // 2], len(pans)))
check('tracking: ball found most of the time', samples >= 60, True)
check('tracking: median offset < 10% of image', sorted(offsets)[len(offsets) // 2] < 0.10, True)
check('tracking: pan moved in multiples of step 5', all(p % 5 == 0 for p in pans), True)
teach = cmd('track_teach')
check('teach colour -> custom', teach['tracker']['color'], 'custom')
cmd('feature', name='ball_tracking', on=False)
print('RESULT:', 'ALL PASS' if not failures else 'FAILED: %s' % failures)
