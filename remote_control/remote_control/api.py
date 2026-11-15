'''
JSON API and the dashboard page.
  GET  /                      dashboard (camera left, diagnosis top right, controls bottom right)
  GET  /api/state             car + tracker + features + latest telemetry
  GET  /api/history?minutes=N telemetry history (columns + rows)
  POST /api/cmd               {"cmd": <name>, ...} -> runs one command (commands.ACTIONS), returns /api/state
  GET  /media/<name>          download a photo/video/sound file from DATA_DIR/media
'''
import json
import os
import subprocess
import threading
import time

from django.http import FileResponse, Http404, HttpResponseBadRequest, JsonResponse, StreamingHttpResponse
from django.shortcuts import render_to_response

from . import audio, commands, events, features, media, rooms, services, speaker, spoken_texts, telemetry
from .car import car, SIM
from .tracker import tracker


telemetry.start(tracker.describe)
speaker.say_later(spoken_texts.STARTUP[1], spoken_texts.STARTUP[0])  # every start: boot, restart, update (the twin writes it down)


def _apply_auto_update(name, is_on):
    if name == 'auto_update' and not SIM:
        subprocess.run(['systemctl', 'enable' if is_on else 'disable', '--now', 'picar-update.timer'])


features.on_change(_apply_auto_update)

def _stream_url(request):
    if SIM:
        return '/sim/stream'
    return 'http://%s:8080/?action=stream' % request.get_host().split(':')[0]


STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')


def dashboard(request):
    # Version stamp from file times, so browsers reload CSS/JS after every update.
    asset_version = int(max(os.path.getmtime(os.path.join(STATIC_DIR, name)) for name in ('dashboard.css', 'dashboard.js')))
    return render_to_response('dashboard.html', {'sim': SIM, 'asset_version': asset_version})


SLOW_SECTION_S = 3          # switch state_sections: how old a slow section may be in a poll
# The sections of /api/state: name -> (how it is made, slow). Slow sections read files or folders; with the switch
# state_sections on, a poll gets them at most SLOW_SECTION_S old, and every command reads all of them fresh.
STATE_SECTIONS = {
    'car': (car.state, False), 'tracker': (tracker.describe, False), 'features': (features.describe, False),
    'telemetry': (lambda: telemetry.latest, False), 'audio': (audio.microphone.describe, False), 'events': (events.recent, False),
    'speaker': (speaker.describe, True), 'media': (media.describe, True), 'rooms': (rooms.describe, True),
    'settings': (lambda: {name: events.settings().get(name) for name in events.EDITABLE_SETTINGS}, True),
    'setting_types': (lambda: {name: {'type': kind.__name__, 'help': events.SETTING_HELP[name]} for name, kind in events.EDITABLE_SETTINGS.items()}, True),
    'services': (services.describe, True), 'calibration': (commands.calibration, True),
}
_slow_sections = {'made': 0.0, 'values': {}}
_slow_lock = threading.Lock()


def _sections(fresh):
    if not features.enabled('state_sections'):
        return {name: make() for name, (make, _) in STATE_SECTIONS.items()}
    with _slow_lock:
        if fresh or time.time() - _slow_sections['made'] > SLOW_SECTION_S:
            _slow_sections.update(made=time.time(), values={name: make() for name, (make, slow) in STATE_SECTIONS.items() if slow})
        slow_values = dict(_slow_sections['values'])
    return dict({name: make() for name, (make, slow) in STATE_SECTIONS.items() if not slow}, **slow_values)


def state(request, fresh=False):
    return JsonResponse(dict(_sections(fresh), stream_url=_stream_url(request)))


def history(request):
    minutes = int(request.GET.get('minutes', 60))
    rows = list(telemetry.history)[-minutes * 60 // telemetry.SAMPLE_SECONDS:]
    return JsonResponse({'columns': telemetry.HISTORY_COLUMNS, 'rows': rows})


def command(request):
    try:
        args = json.loads(request.body.decode('utf-8') or '{}')
        commands.run(args['cmd'], args)
    except (KeyError, ValueError, TypeError) as error:
        return HttpResponseBadRequest('bad command: %s' % error)
    return state(request, fresh=True)


def media_file(request, name):
    try:
        response = FileResponse(open(media.safe_path(name), 'rb'))  # Django 2.0: no as_attachment yet
    except FileNotFoundError:
        raise Http404(name)
    if not name.endswith('.jpg'):
        response['Content-Disposition'] = 'attachment; filename="%s"' % os.path.basename(name)
    return response


def sim_stream(request):
    from .sim import world
    return StreamingHttpResponse(world.mjpeg_frames(), content_type='multipart/x-mixed-replace; boundary=frame')
