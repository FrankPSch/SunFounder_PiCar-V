'''What the car and the PC agree on for the Claude conversation. Imported by both: the car's voice_link.py and
conversation.py, the PC's tools/voice_server.py and tools/voice_launcher.py. Plain Python, no other imports.

The PC reaches the car through SSH reverse tunnels, so on the car both live on 127.0.0.1:
  VOICE_PORT     voice server:  GET /health, GET /hello?language=, GET /news, POST /utterance?new=0|1, POST /shutdown
  LAUNCHER_PORT  launcher:      GET /status, POST /start, POST /stop
Replies with speech are frames: kind (1 byte) + length (4 bytes, big endian) + payload.
  TEXT  JSON fields: transcript + language, sentence, error, set_feature + on, run_command
  AUDIO 16-bit mono audio at the rate the reply header AUDIO_RATE names'''
import struct

VOICE_PORT = 8766
LAUNCHER_PORT = 8767
LANGUAGES = ('de', 'en')
UTTERANCE_RATE = 16000          # what the car records and Whisper reads
DEFAULT_SPEECH_RATE = 16000     # speech for a car that does not send PLAYBACK_RATE
PLAYBACK_RATES = (16000, 48000)

STATUS = 'X-Car-Status'         # JSON: car state and telemetry
FEATURES = 'X-Car-Features'     # JSON: [[name, on, help], ...]
COMMANDS = 'X-Car-Commands'     # JSON: [[name, help], ...] that Claude may run (none of them drives)
VOICE_LANGUAGES = 'X-Car-Languages'  # "de" or "de,en": with one language Whisper does not guess
PLAYBACK_RATE = 'X-Car-Playback-Rate'
AUDIO_RATE = 'X-Audio-Rate'

TEXT = b'T'
AUDIO = b'A'
_FRAME_HEADER = struct.Struct('>cI')

SERVER_RUNNING, SERVER_STARTING, SERVER_OFF = 'running', 'starting', 'off'


def frame(kind, payload):
    return _FRAME_HEADER.pack(kind, len(payload)) + payload


def read_frames(response):
    '''(kind, payload) until the reply ends.'''
    while True:
        header = response.read(_FRAME_HEADER.size)
        if len(header) < _FRAME_HEADER.size:
            return
        kind, length = _FRAME_HEADER.unpack(header)
        yield kind, response.read(length)
