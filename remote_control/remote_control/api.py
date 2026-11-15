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

from django.http import FileResponse, Http404, HttpResponseBadRequest, JsonResponse, StreamingHttpResponse
from django.shortcuts import render_to_response

from . import audio, commands, events, features, media, rooms, speaker, telemetry
from .car import car, SIM
from .tracker import tracker

STARTUP_MESSAGE = 'Ich bin bereit.'  # every start of the car software: boot, restart, update

telemetry.start(tracker.describe)
if not SIM:
    speaker.say_later(STARTUP_MESSAGE, 'de')


def _apply_auto_update(name, is_on):
    if name == 'auto_update' and not SIM:
        subprocess.run(['systemctl', 'enable' if is_on else 'disable', '--now', 'picar-update.timer'])


features.on_change(_apply_auto_update)

mbrola_check = {'said': None}  # feature mbrola_check: the last voice report, for the dashboard state


def _check_mbrola(name, is_on):
    '''Feature mbrola_check: the soft voice becomes the default (mbrola_voice on) and the car says which voice speaks.'''
    if name not in ('mbrola_check', 'mbrola_voice') or not features.enabled('mbrola_check'):
        if name == 'mbrola_check':
            mbrola_check['said'] = None
        return
    if name == 'mbrola_check' and not features.enabled('mbrola_voice'):
        features.set_enabled('mbrola_voice', True)  # calls this listener again for mbrola_voice, which reports
        return
    voice_name = speaker.mbrola_voice('de')
    if voice_name:
        text = 'Ich spreche mit der weichen MBROLA Stimme.'
    elif features.enabled('mbrola_voice'):
        text = 'Die weiche Stimme fehlt, ich spreche mit espeak.'
    else:
        text = 'Die weiche Stimme ist aus, ich spreche mit espeak.'
    mbrola_check['said'] = text
    events.add('speaker', 'voice check: %s (%s)' % (voice_name or 'espeak-ng', speaker.describe()['mbrola']))
    speaker.say_later(text, 'de')


features.on_change(_check_mbrola)
if features.enabled('mbrola_check'):
    _check_mbrola('mbrola_check', True)


def _stream_url(request):
    if SIM:
        return '/sim/stream'
    return 'http://%s:8080/?action=stream' % request.get_host().split(':')[0]


STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')


def dashboard(request):
    # Version stamp from file times, so browsers reload CSS/JS after every update.
    asset_version = int(max(os.path.getmtime(os.path.join(STATIC_DIR, name)) for name in ('dashboard.css', 'dashboard.js')))
    return render_to_response('dashboard.html', {'sim': SIM, 'asset_version': asset_version})


def state(request):
    return JsonResponse({
        'car': car.state(), 'tracker': tracker.describe(), 'features': features.describe(),
        'telemetry': telemetry.latest, 'stream_url': _stream_url(request),
        'audio': audio.microphone.describe(), 'speaker': speaker.describe(), 'mbrola_check': mbrola_check, 'media': media.describe(), 'rooms': rooms.describe(),
        'events': events.recent(), 'settings': {name: events.settings().get(name) for name in events.EDITABLE_SETTINGS},
    })


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
    return state(request)


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
