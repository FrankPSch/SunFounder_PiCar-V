# Usage: python tools/sim_track.py   (sim server on 127.0.0.1:8765)
"""Tracking quality per camera step size (sim ball moves up to ~87 px/s, 1 tick = 2 px)."""
import json, time, urllib.request
BASE = 'http://127.0.0.1:8765'
def cmd(command_name, **args):
    request = urllib.request.Request(BASE + '/api/cmd', data=json.dumps(dict(cmd=command_name, **args)).encode(), method='POST')
    return json.load(urllib.request.urlopen(request))
for step in (1, 2, 5, 10):
    cmd('feature', name='ball_tracking', on=False); cmd('cam_center'); cmd('cam_step', value=step)
    cmd('track_color', name='red'); cmd('feature', name='ball_tracking', on=True)
    time.sleep(4)  # settle
    offsets, centred = [], 0
    for _ in range(48):
        tracker = json.load(urllib.request.urlopen(BASE + '/api/state'))['tracker']
        if tracker.get('found'):
            offsets.append(max(abs(tracker['x'] - 0.5), abs(tracker['y'] - 0.5)))
            centred += bool(tracker.get('centred'))
        time.sleep(0.25)
    offsets.sort()
    print('step %2d ticks: found %2d/48, centred %2d, median offset %.3f, max %.3f'
          % (step, len(offsets), centred, offsets[len(offsets) // 2], offsets[-1]))
cmd('feature', name='ball_tracking', on=False); cmd('cam_step', value=1)
