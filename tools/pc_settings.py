'''Where things are on the owner's PC, and the protocol shared with the car (remote_control/remote_control/voice_protocol.py).'''
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE_DIR = os.environ.get('PICAR_VOICE_DIR', r'D:\a_ClaudeCowork\PiCar\voice')  # Whisper, Piper, environments: not in git
PICAR_DIR = os.environ.get('PICAR_DIR', os.path.dirname(VOICE_DIR))  # project notes, wish list
VOICE_PYTHON = os.path.join(VOICE_DIR, 'env', 'Scripts', 'python.exe')
CAR_HOST = os.environ.get('PICAR_HOST', '192.168.178.131')
CAR = 'pi@' + CAR_HOST
DLL_DIRS = (os.path.join(VOICE_DIR, 'cublas'), r'C:\ProgramData\Anaconda3\Library\bin')  # CUDA for Whisper; sqlite, ssl

sys.path.insert(0, os.path.join(REPO, 'remote_control'))
from remote_control import voice_protocol  # noqa: E402,F401  (re-exported for the PC tools)


def add_dll_dirs():
    missing = [folder for folder in DLL_DIRS if folder not in os.environ['PATH'].split(os.pathsep)]
    os.environ['PATH'] = os.pathsep.join(missing + [os.environ['PATH']])


add_dll_dirs()  # on import: the launcher starts the voice server without them, and ssl (anthropic) fails without its DLLs
