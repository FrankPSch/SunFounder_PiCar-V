'''Where things are on the owner's PC, and the protocol shared with the car (remote_control/remote_control/voice_protocol.py).'''
import json
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE_DIR = os.environ.get('PICAR_VOICE_DIR', r'D:\a_ClaudeCowork\PiCar\voice')  # Whisper, Piper, environments: not in git
VOICE_PYTHON = os.path.join(VOICE_DIR, 'env', 'Scripts', 'python.exe')
CAR_HOST = os.environ.get('PICAR_HOST', '192.168.178.131')
CAR = 'pi@' + CAR_HOST
CAR_SSH_KEY = os.path.expanduser('~/.ssh/picar_ed25519')
CAR_DATA_DIR = '/home/pi/picar-data'            # features.DATA_DIR on the car (tests/pc/test_twin.py checks it)
CAR_REPO_DIR = '/home/pi/SunFounder_PiCar-V'

# Switches of the PC side, like features.py on the car: off by default, on in <voice dir>\pc_switches.json ({"name": true}).
# Each one chooses between the old and the new way of one architecture item (project/WORKPLAN.md, backlog 2026-11-15).
SWITCHES = {
    'car_ssh_module': 'SSH to the car only through tools/car_ssh.py',
    'voice_folder_by_kind': 'Voice folder by kind: data (Frank\'s, back it up), logs, generated (rebuildable); the rest is the runtime',
}
SWITCHES_FILE = os.path.join(VOICE_DIR, 'pc_switches.json')
VOICE_KINDS = ('data', 'logs', 'generated')


def switched_on(name):
    if name not in SWITCHES:
        raise KeyError('unknown PC switch %r' % name)
    try:
        with open(SWITCHES_FILE, encoding='utf-8') as stored:
            return bool(json.load(stored).get(name, False))
    except (OSError, ValueError):
        return False


def voice_path(kind, *name):
    '''A file or folder of the voice folder by its kind (VOICE_KINDS). Switch voice_folder_by_kind on: in <voice dir>\\<kind>,
    copied there from its old place the first time (the old copy stays until Frank removes it); off: the old place.
    Read when a tool starts: the voice server and launcher take a new setting at their next start.'''
    if kind not in VOICE_KINDS:
        raise KeyError('unknown voice folder kind %r' % kind)
    old = os.path.join(VOICE_DIR, *name)
    if not switched_on('voice_folder_by_kind'):
        return old
    path = os.path.join(VOICE_DIR, kind, *name)
    if not name:                # the kind's folder itself: never a copy of the whole voice folder
        os.makedirs(path, exist_ok=True)
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path) and os.path.exists(old):
        (shutil.copytree if os.path.isdir(old) else shutil.copy2)(old, path)
    return path


WISHES_DIR = os.environ.get('PICAR_WISHES_DIR') or os.path.dirname(voice_path('data', 'wishes.jsonl'))   # wishes.jsonl: data, not in git (view: REQUIREMENTS.md, W)
DLL_DIRS = (os.path.join(VOICE_DIR, 'cublas'), r'C:\ProgramData\Anaconda3\Library\bin')  # CUDA for Whisper; sqlite, ssl

sys.path.insert(0, os.path.join(REPO, 'remote_control'))
from remote_control import voice_protocol  # noqa: E402,F401  (re-exported for the PC tools)


def add_dll_dirs():
    missing = [folder for folder in DLL_DIRS if folder not in os.environ['PATH'].split(os.pathsep)]
    os.environ['PATH'] = os.pathsep.join(missing + [os.environ['PATH']])


add_dll_dirs()  # on import: the launcher starts the voice server without them, and ssl (anthropic) fails without its DLLs
