'''
JSON API and the dashboard page.
  GET  /                      dashboard (camera left, diagnosis top right, controls bottom right)
  GET  /api/state             car + tracker + features + latest telemetry
  GET  /api/history?minutes=N telemetry history (columns + rows)
  POST /api/cmd               {"cmd": <name>, ...} -> runs one command, returns /api/state
  GET  /media/<name>          download a photo/video/sound file from DATA_DIR/media
'''
import json
import os
import subprocess

from django.http import FileResponse, Http404, HttpResponseBadRequest, JsonResponse, StreamingHttpResponse
from django.shortcuts import render_to_response

from . import audio, events, features, media, rooms, telemetry
from .car import car, SIM
from .tracker import tracker

telemetry.start(tracker.describe)


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


def state(request):
    return JsonResponse({
        'car': car.state(), 'tracker': tracker.describe(), 'features': features.describe(),
        'telemetry': telemetry.latest, 'stream_url': _stream_url(request),
        'audio': audio.microphone.describe(), 'media': media.describe(), 'rooms': rooms.describe(),
        'events': events.recent(), 'settings': {name: events.settings().get(name) for name in events.EDITABLE_SETTINGS},
    })


def history(request):
    minutes = int(request.GET.get('minutes', 60))
    rows = list(telemetry.history)[-minutes * 60 // telemetry.SAMPLE_SECONDS:]
    return JsonResponse({'columns': telemetry.HISTORY_COLUMNS, 'rows': rows})


COMMANDS = {
    'drive':        lambda args: car.drive(args['direction']),
    'keepalive':    lambda args: car.keepalive(),
    'stop':         lambda args: car.stop('stop button'),
    'speed':        lambda args: car.set_speed(args['value'] if 'value' in args else car.speed + args['delta']),
    'steer':        lambda args: car.set_steer(args['value'] if 'value' in args else car.steer_angle + args['delta']),
    'steer_hold':   lambda args: car.hold_steer(args['side']),
    'cam_move':     lambda args: car.move_cam(args.get('pan', 0), args.get('tilt', 0), args.get('step')),
    'cam_set':      lambda args: car.set_cam(args['pan'], args['tilt']),
    'cam_center':   lambda args: car.center_cam(),
    'cam_step':     lambda args: car.set_cam_step(args['value'] if 'value' in args else car.cam_step + args['delta']),
    'ready':        lambda args: car.ready(),
    'feature':      lambda args: features.set_enabled(args['name'], args['on']),
    'track_color':  lambda args: tracker.set_color(args['name']),
    'track_teach':  lambda args: tracker.teach(),
    'track_animal': lambda args: tracker.set_animal(args['name']),
    'photo':        lambda args: media.snapshot(),
    'record':       lambda args: media.start_recording() if args['on'] else media.stop_recording(),
    'setting':      lambda args: events.update_setting(args['name'], args['value']),
    'room_name':    lambda args: rooms.name_marker(args['id'], args.get('name', '')),
    'room_goto':    lambda args: rooms.goto(args['name']),
    'room_cancel':  lambda args: rooms.cancel(),
    'treasure_next': lambda args: tracker._next_treasure(),
}


def command(request):
    try:
        args = json.loads(request.body.decode('utf-8') or '{}')
        COMMANDS[args['cmd']](args)
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
