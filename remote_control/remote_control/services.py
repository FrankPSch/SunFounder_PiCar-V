'''The car's long-running loops in one list, in the order the car software starts them (the import order behind api.py).
Each starts through start(name, loop), which keeps for every loop: running or not, when it started, how it ended (the
last error). Switch service_list: the list is shown in /api/state "services" and on the dashboard, so a loop that died
is seen. The loops are daemon threads: they end with the car software. tests/sim/ checks the list against what runs.'''
import threading
import time
import traceback

from . import features

SERVICES = ('watchdog', 'timelapse', 'vision', 'voice-news', 'microphone', 'telemetry')   # as they start (measured on the twin)
ERROR_TAIL_CHARS = 300

_status = {}
_lock = threading.Lock()


def start(name, loop):
    if name not in SERVICES:
        raise KeyError('service %r is not in services.SERVICES' % name)

    def run():
        try:
            loop()
            ended = 'ended'
        except Exception:                       # recorded, then raised as before (the traceback goes to the log)
            ended = traceback.format_exc()[-ERROR_TAIL_CHARS:]
            raise
        finally:
            with _lock:
                _status[name].update(running=False, ended=time.strftime('%H:%M:%S'), error=ended)
    with _lock:
        _status[name] = {'running': True, 'started': time.strftime('%H:%M:%S'), 'ended': None, 'error': None,
                         'order': len(_status)}
    threading.Thread(target=run, name=name, daemon=True).start()


def describe():
    '''{name: status} in start order, or None while the switch service_list is off.'''
    if not features.enabled('service_list'):
        return None
    with _lock:
        return {name: dict(_status[name]) for name in sorted(_status, key=lambda item: _status[item]['order'])}
