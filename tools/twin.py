'''The digital twin of the car and its surroundings on the PC: every change is tested on it before the real car.
  hardware    remote_control/remote_control/sim/: servos and motor (fake_picar), camera world (world), sensors,
              microphone, speaker and file owners (hardware)
  car data    seed_car_data(folder): a picar-data folder laid out like the car's, owners as there (root / pi)
  SimCar      the simulated car server (PICAR_SIM=1) on such a folder, started and stopped as a context manager
  FakeCar     the car as the PC tools see it over SSH: answers each command they send from the twin folders
  ssh_to()    routes every ['ssh', ...] subprocess call of the PC tools to a FakeCar (for tests)
  voice dir   seed_voice_dir(folder): a PC voice folder with example wishes, learning results and logs
tools/run_tests.py runs everything on the twin (one command); the real car only gets the read-only deploy check.'''
import contextlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import time
import urllib.request

import pc_settings

CAR_REPO, CAR_DATA = pc_settings.CAR_REPO_DIR, pc_settings.CAR_DATA_DIR
REPO_CALIBRATION = os.path.join(pc_settings.REPO, 'remote_control', 'remote_control', 'driver', 'config')
SIM_START_TRIES = 60
SIM_START_PAUSE_S = 0.5
SIM_STOP_TIMEOUT_S = 10
# picar-data as on the car (2026-11-15): the service (root) wrote these, pi the rest
ROOT_OWNED = ('features.json', 'settings.json', 'db.sqlite3', 'media', 'voice_check')
CAR_FEATURES = {'watchdog': True, 'stuck_detection': True, 'telemetry': True, 'power_warning': True, 'natural_voice': True,
                'voice_commands': True, 'stop_word': True, 'mbrola_voice': True}   # mbrola_voice: a switch the car no longer knows
CAR_SETTINGS = {'voice_languages': 'de,en'}
KEY_PACKAGES = {'apt': ('espeak-ng', 'opencv-data', 'python3-opencv', 'ffmpeg', 'alsa-utils', 'python3-numpy'), 'pip': ('vosk', 'picamera')}


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as target:
        target.write(text)


def seed_car_data(folder):
    '''A picar-data folder like the car's: switches and settings in their old files, calibration, a few media files,
    an empty voice cache, file owners. Returns folder.'''
    _write(os.path.join(folder, 'features.json'), json.dumps(CAR_FEATURES, indent=2, sort_keys=True))
    _write(os.path.join(folder, 'settings.json'), json.dumps(CAR_SETTINGS))
    shutil.copy(REPO_CALIBRATION, os.path.join(folder, 'calibration'))
    for name in ('photo-20261115-120000.jpg', 'video-20261115-120100.avi', 'timelapse-20261115-120200.jpg'):
        _write(os.path.join(folder, 'media', name), 'twin media file')
    for language in ('de', 'en'):
        os.makedirs(os.path.join(folder, 'voice_cache', language), exist_ok=True)
    os.makedirs(os.path.join(folder, 'models'), exist_ok=True)
    owners = {name: 'root' for name in ROOT_OWNED}
    owners.update(calibration='pi', models='pi', voice_cache='pi')
    _write(os.path.join(folder, 'twin_owners.json'), json.dumps(owners, indent=1, sort_keys=True))
    return folder


def seed_voice_dir(folder):
    '''A PC voice folder with Frank's kind of data: two wishes, one learning result, logs. Returns folder.'''
    wishes = [{'id': 'twin-1', 'state': 'proposed', 'title': 'Hupen', 'time': '2026-11-15 10:00:00'},
              {'id': 'twin-2', 'state': 'deployed', 'title': 'Kamera mitte', 'time': '2026-11-15 11:00:00'}]
    _write(os.path.join(folder, 'wishes.jsonl'), ''.join(json.dumps(wish) + '\n' for wish in wishes))
    _write(os.path.join(folder, 'recordings', 'results.jsonl'),
           json.dumps({'language': 'de', 'phrase': 'stopp', 'verdict': 'understood', 'time': '2026-11-15 12:00:00'}) + '\n')
    _write(os.path.join(folder, 'server.log'), '==== twin voice server\n')
    _write(os.path.join(folder, 'launcher.log'), '==== twin launcher\n')
    return folder


class SimCar(object):
    '''The simulated car server on a picar-data folder: with SimCar(code, port, data) as car: car.url ...'''
    def __init__(self, code_dir, port, data_dir, python=None):
        self.url = 'http://127.0.0.1:%d' % port
        self.data_dir, self._code_dir, self._port = data_dir, code_dir, port
        self._python = python or os.path.join(pc_settings.VOICE_DIR, 'simenv', 'Scripts', 'python.exe')
        self.env = dict(os.environ, PICAR_SIM='1', PICAR_DATA=data_dir, PYTHONIOENCODING='utf-8',
                        PATH=os.pathsep.join(pc_settings.DLL_DIRS + (os.environ['PATH'],)))
        self._server, self._log = None, None

    def __enter__(self):
        # a pipe nobody reads would fill up and stall the server
        self._log = open(os.path.join(self.data_dir, 'server.log'), 'wb')
        self._server = subprocess.Popen([self._python, 'manage.py', 'runserver', '--noreload', '127.0.0.1:%d' % self._port],
                                        cwd=os.path.join(self._code_dir, 'remote_control'), env=self.env, stdout=self._log, stderr=subprocess.STDOUT)
        for _ in range(SIM_START_TRIES):
            try:
                urllib.request.urlopen(self.url + '/api/state', timeout=2).read()
                return self
            except OSError:
                if self._server.poll() is not None:
                    break
                time.sleep(SIM_START_PAUSE_S)
        self.__exit__(None, None, None)
        with open(os.path.join(self.data_dir, 'server.log'), encoding='utf-8', errors='replace') as log:
            raise RuntimeError('simulated car did not start:\n' + log.read()[-2000:])

    def state(self):
        with urllib.request.urlopen(self.url + '/api/state', timeout=10) as response:
            return json.loads(response.read().decode('utf-8'))

    def __exit__(self, *exception):
        # the venv's python.exe is a launcher: killing it alone leaves the real server holding the port
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(self._server.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._server.wait(SIM_STOP_TIMEOUT_S)
        self._log.close()


class FakeCar(object):
    '''Answers the SSH commands the PC tools send, from a twin picar-data folder. commands: what was sent.'''
    def __init__(self, data_dir, commit=None, tunnels=True):
        self.data_dir, self.tunnels, self.commands = data_dir, tunnels, []
        self.commit = commit or subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=pc_settings.REPO, stdout=subprocess.PIPE,
                                               universal_newlines=True).stdout.strip()

    def local(self, car_path):
        '''The twin path of a car path under /home/pi/picar-data.'''
        return os.path.join(self.data_dir, *car_path[len(CAR_DATA):].strip('/').split('/'))

    def run(self, command, stdin=b''):
        '''(exit code, stdout bytes) as ssh would give them.'''
        self.commands.append(command)
        if command == 'sh -s':
            return 0, self._facts().encode('utf-8')
        if command == 'git -C %s rev-parse HEAD' % CAR_REPO:
            return 0, (self.commit + '\n').encode()
        if command.startswith('ss -ltn'):
            return 0, (b'127.0.0.1:8766\n127.0.0.1:8767\n' if self.tunnels else b'0.0.0.0:22\n')
        if command.startswith('mkdir -p ') and command.endswith('ls de en'):
            return 0, self._cache_listing(command.split()[2].rsplit('/', 1)[0]).encode()
        if command.startswith('tar xf - -C '):
            with tarfile.open(fileobj=io.BytesIO(stdin), mode='r|') as archive:
                archive.extractall(self.local(command.split()[-1]))
            return 0, b''
        if command.endswith('picar-update.sh release'):
            return 0, ('picar-update: updated twin -> %s\n' % self.commit).encode()
        if command.startswith('sudo -n fuser'):
            return 0, b''
        return 255, ('twin: unknown command %r\n' % command).encode()

    def _cache_listing(self, car_cache):
        parts = []
        for language in ('de', 'en'):
            folder = os.path.join(self.local(car_cache), language)
            os.makedirs(folder, exist_ok=True)
            parts.append('%s:\n%s' % (language, ''.join(name + '\n' for name in sorted(os.listdir(folder)))))
        return '\n'.join(parts)

    def _facts(self):
        cache = self.local(CAR_DATA + '/voice_cache')
        counts = ['%s %d' % (language, len(os.listdir(os.path.join(cache, language))) if os.path.isdir(os.path.join(cache, language)) else 0)
                  for language in ('de', 'en')]
        sections = [('arch', ['armv7l']), ('os', ['Raspbian GNU/Linux 11 (bullseye)']), ('python', ['Python 3.9.2']),
                    ('apt', KEY_PACKAGES['apt']), ('pip', KEY_PACKAGES['pip']), ('sound', ['USB']),
                    ('models', sorted(os.listdir(self.local(CAR_DATA + '/models')))), ('voice_cache', counts),
                    ('disk_free', ['51G']), ('commit', [self.commit[:7] + ' twin'])]
        return ''.join('==== %s\n%s' % (name, ''.join(line + '\n' for line in lines)) for name, lines in sections)


class _CompletedSsh(object):
    def __init__(self, args, code, stdout, text):
        self.args, self.returncode = args, code
        self.stdout = stdout.decode('utf-8', 'replace') if text else stdout
        self.stderr = '' if text else b''


class _SshPopen(object):
    '''Popen(['ssh', ..., 'tar xf - -C ...'], stdin=PIPE): the bytes written go to the FakeCar on close.'''
    def __init__(self, car, command):
        self.returncode = None
        car_ref, popen = car, self

        class _Stdin(io.BytesIO):
            def close(self):
                if not self.closed:
                    popen.returncode = car_ref.run(command, self.getvalue())[0]
                super().close()
        self.stdin = _Stdin()

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode


@contextlib.contextmanager
def ssh_to(car):
    '''Every subprocess ['ssh', ..., host, command] in this process goes to car (a FakeCar) while inside.'''
    real_run, real_popen = subprocess.run, subprocess.Popen

    def fake_run(args, *more, **options):
        if not (isinstance(args, list) and args and args[0] == 'ssh'):
            return real_run(args, *more, **options)
        if '-N' in args:            # a tunnel: the twin car has its tunnels already
            return _CompletedSsh(args, 0, b'', False)
        stdin = options.get('input') or b''
        code, stdout = car.run(args[-1], stdin.encode() if isinstance(stdin, str) else stdin)
        return _CompletedSsh(args, code, stdout, options.get('universal_newlines') or options.get('text'))

    def fake_popen(args, *more, **options):
        if isinstance(args, list) and args and args[0] == 'ssh':
            return _SshPopen(car, args[-1])
        return real_popen(args, *more, **options)

    subprocess.run, subprocess.Popen = fake_run, fake_popen
    try:
        yield car
    finally:
        subprocess.run, subprocess.Popen = real_run, real_popen
