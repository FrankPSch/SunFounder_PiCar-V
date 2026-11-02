'''
Photos, video recording, timelapse. Files go to DATA_DIR/media (on the Pi's SD card).
Video is the camera's MJPEG stream stored as-is (.mjpg, no re-encoding: the Pi 3 can't spare the CPU).
Play it with VLC, or convert on the PC: ffmpeg -i video.mjpg video.mp4
With feature record_audio, the microphone is recorded to a .wav with the same name.
'''
import os
import threading
import time
import urllib.request

from . import events, features
from .car import SIM

MEDIA_DIR = os.path.join(features.DATA_DIR, 'media')
SNAPSHOT_URL = 'http://127.0.0.1:8080/?action=snapshot'
STREAM_URL = 'http://127.0.0.1:8080/?action=stream'
DEFAULT_TIMELAPSE_SECONDS = 10

_recording = {'path': None, 'started': None, 'bytes': 0, 'stop': None}


def _stamp():
    return time.strftime('%Y%m%d-%H%M%S')


def jpeg_bytes():
    if SIM:
        import cv2
        from .sim import world
        return cv2.imencode('.jpg', world.render())[1].tobytes()
    with urllib.request.urlopen(SNAPSHOT_URL, timeout=3) as response:
        return response.read()


def snapshot(prefix='photo', folder=''):
    target_dir = os.path.join(MEDIA_DIR, folder)
    os.makedirs(target_dir, exist_ok=True)
    name = os.path.join(folder, '%s-%s.jpg' % (prefix, _stamp())).replace('\\', '/')
    with open(os.path.join(MEDIA_DIR, name), 'wb') as photo:
        photo.write(jpeg_bytes())
    if prefix == 'photo':
        events.add('media', 'photo saved: ' + name)
    return name


def start_recording():
    if _recording['path']:
        return _recording['path']
    os.makedirs(MEDIA_DIR, exist_ok=True)
    name = 'video-%s.mjpg' % _stamp()
    stop_flag = threading.Event()
    _recording.update(path=name, started=time.time(), bytes=0, stop=stop_flag)
    threading.Thread(target=_record_loop, args=(os.path.join(MEDIA_DIR, name), stop_flag), name='recorder', daemon=True).start()
    if features.enabled('record_audio'):
        from . import audio
        audio.start_wav(os.path.join(MEDIA_DIR, name.replace('.mjpg', '.wav')))
    events.add('media', 'recording started: ' + name, say='recording')
    return name


def stop_recording():
    if not _recording['path']:
        return None
    _recording['stop'].set()
    from . import audio
    audio.stop_wav()
    name, seconds = _recording['path'], int(time.time() - _recording['started'])
    events.add('media', 'recording stopped: %s (%d s, %.1f MB)' % (name, seconds, _recording['bytes'] / 1e6))
    _recording.update(path=None, started=None, stop=None)
    return name


def _record_loop(path, stop_flag):
    with open(path, 'wb') as video:
        if SIM:
            from .sim import world
            frames = world.mjpeg_frames()
            while not stop_flag.is_set():
                chunk = next(frames)
                video.write(chunk)
                _recording['bytes'] += len(chunk)
            return
        with urllib.request.urlopen(STREAM_URL, timeout=5) as stream:
            while not stop_flag.is_set():
                chunk = stream.read(16384)
                if not chunk:
                    break
                video.write(chunk)
                _recording['bytes'] += len(chunk)


def _timelapse_loop():
    while True:
        seconds = max(2, int(events.settings().get('timelapse_seconds', DEFAULT_TIMELAPSE_SECONDS)))
        if features.enabled('timelapse'):
            try:
                snapshot('tl', folder='timelapse-' + time.strftime('%Y%m%d'))
            except OSError as error:
                events.add('media', 'timelapse photo failed: %s' % error)
        time.sleep(seconds)


def list_files(limit=30):
    found = []
    for root, _, names in os.walk(MEDIA_DIR):
        for name in names:
            path = os.path.join(root, name)
            found.append({'name': os.path.relpath(path, MEDIA_DIR).replace('\\', '/'),
                          'bytes': os.path.getsize(path), 'mtime': os.path.getmtime(path)})
    return sorted(found, key=lambda item: item['mtime'], reverse=True)[:limit]


def safe_path(name):
    path = os.path.realpath(os.path.join(MEDIA_DIR, name))
    if not path.startswith(os.path.realpath(MEDIA_DIR) + os.sep) or not os.path.isfile(path):
        raise FileNotFoundError(name)
    return path


def describe():
    recording = None
    if _recording['path']:
        recording = {'name': _recording['path'], 'seconds': int(time.time() - _recording['started']),
                     'mb': round(_recording['bytes'] / 1e6, 1)}
    return {'recording': recording, 'files': list_files(),
            'timelapse_seconds': int(events.settings().get('timelapse_seconds', DEFAULT_TIMELAPSE_SECONDS))}


threading.Thread(target=_timelapse_loop, name='timelapse', daemon=True).start()
