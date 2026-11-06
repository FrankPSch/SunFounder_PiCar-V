# -*- coding: utf-8 -*-
'''
PC half of the car's Claude conversation: speech -> Whisper -> Claude -> Piper -> speech.
The car stays in charge of steering and stop (remote_control/conversation.py); this only talks.

Start: tools\\voice_server.bat (needs the user variable ANTHROPIC_API_KEY; CLAUDE_VOICE_MODEL=echo works without it).
Connection: the server listens on this PC's loopback only and opens an SSH tunnel, so the car reaches it at its own
127.0.0.1:8766. No firewall rule, and nobody else on the network can use it.

  GET  /health               {"model": ..., "whisper": ...}
  POST /utterance?new=0|1    body: 16 kHz mono 16-bit audio, header X-Car-Status: JSON
       reply: frames of kind (1 byte) + length (4 bytes) + payload; 'T' = JSON text, 'A' = 16 kHz mono 16-bit audio
'''
import http.server
import json
import os
import re
import struct
import subprocess
import threading
import time
import urllib.parse

VOICE_DIR = os.environ.get('PICAR_VOICE_DIR', r'D:\a_ClaudeCowork\PiCar\voice')
os.environ['PATH'] = os.pathsep.join((os.path.join(VOICE_DIR, 'cublas'), r'C:\ProgramData\Anaconda3\Library\bin', os.environ['PATH']))

import anthropic
import numpy as np
from faster_whisper import WhisperModel

CAR = 'pi@' + os.environ.get('PICAR_HOST', '192.168.178.131')
SSH_KEY = os.path.expanduser('~/.ssh/picar_ed25519')
PORT = 8766
TUNNEL_RETRY_S = 5

## Speech to text
WHISPER_SIZE = os.environ.get('WHISPER_SIZE', 'small')  # GTX 1070: small 0.8 s, medium 1.5 s for a 4.5 s sentence
LANGUAGES = ('de', 'en')
NOT_SPEECH_PROBABILITY = 0.6
whisper_model = WhisperModel(WHISPER_SIZE, device='cuda', compute_type='int8_float32', download_root=os.path.join(VOICE_DIR, 'models'), local_files_only=True)


def _whisper(samples, language=None):
    segments, info = whisper_model.transcribe(samples, language=language, beam_size=1, vad_filter=True, hotwords='Claude', condition_on_previous_text=False)
    return ' '.join(segment.text.strip() for segment in segments if segment.no_speech_prob < NOT_SPEECH_PROBABILITY), info.language


def transcribe(pcm):
    samples = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    text, language = _whisper(samples)
    if language not in LANGUAGES:  # short sentences are sometimes taken for a third language
        language = LANGUAGES[0]
        text, _ = _whisper(samples, language)
    return text, language


## Claude
MODEL = os.environ.get('CLAUDE_VOICE_MODEL', 'claude-opus-5-5')  # 'echo' repeats the sentence, for tests without an API key
EFFORT = os.environ.get('CLAUDE_VOICE_EFFORT', 'low')  # empty for a model without the effort setting
MAX_TOKENS = 1000
MAX_HISTORY_MESSAGES = 40
SENTENCE_END = re.compile(r'(?<=[.!?:;])\s+')
SYSTEM_PROMPT = (
    'You are the voice of a small robot car (SunFounder PiCar-V) talking with its owner in the room. '
    'Everything you write is read aloud by a speech synthesizer, so answer in one or two short spoken sentences, '
    'in the language the owner used (German or English). No lists, markdown, emoji or abbreviations. '
    'What the owner says comes from speech recognition and can contain mistakes; go by the likely meaning. '
    'Each message starts with the current car status in square brackets. Mention it only when it matters. '
    'You can talk and read the status, but you cannot move the car: the owner steers with single spoken words such as '
    '"links", "rechts", "los" and "stopp". Say so if asked to drive.')
SPOKEN_ERROR = {'de': 'Ich kann Claude gerade nicht erreichen.', 'en': 'I cannot reach Claude right now.'}
SPOKEN_REFUSAL = {'de': 'Dazu kann ich nichts sagen.', 'en': 'I cannot help with that.'}
client = None if MODEL == 'echo' else anthropic.Anthropic()


class Refused(Exception):
    pass


def claude_sentences(messages):
    options = {'output_config': {'effort': EFFORT}} if EFFORT else {}
    with client.messages.stream(model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT, messages=messages, **options) as stream:
        unfinished = ''
        for text in stream.text_stream:
            parts = SENTENCE_END.split(unfinished + text)
            unfinished = parts.pop()
            for sentence in parts:
                yield sentence
        if unfinished.strip():
            yield unfinished
        if stream.get_final_message().stop_reason == 'refusal':
            raise Refused()


## Text to speech
VOICES = {'de': 'de_DE-thorsten-low', 'en': 'en_US-lessac-low'}
PIPER = os.path.join(VOICE_DIR, 'piper', 'piper', 'piper.exe')
assert set(VOICES) == set(LANGUAGES)
for _voice in VOICES.values():
    with open(os.path.join(VOICE_DIR, 'piper', _voice + '.onnx.json'), encoding='utf-8') as _config:
        assert json.load(_config)['audio']['sample_rate'] == 16000, 'the car plays 16 kHz only: ' + _voice


def synthesize(sentence, language):
    line = ' '.join(sentence.replace('*', ' ').split()) + '\n'
    return subprocess.run([PIPER, '--model', os.path.join(VOICE_DIR, 'piper', VOICES[language] + '.onnx'), '--output-raw'],
                          input=line.encode('utf-8'), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout


## One conversation: the car has one microphone
history = []
history_lock = threading.Lock()


def answer(pcm, new_conversation, car_status, send_text, send_audio):
    started = time.time()
    with history_lock:
        if new_conversation:
            del history[:]
        transcript, language = transcribe(pcm)
        heard_at = time.time()
        send_text({'transcript': transcript, 'language': language})
        print('%s heard (%.2f s for %.1f s, %s): %s' % (time.strftime('%H:%M:%S'), heard_at - started, len(pcm) / 32000.0, language, transcript), flush=True)
        if not transcript:
            return

        def say(sentence):
            send_text({'sentence': sentence})
            send_audio(synthesize(sentence, language))

        spoken = []
        try:
            sentences = iter([transcript]) if client is None else claude_sentences(history + [{'role': 'user', 'content': '[%s]\n%s' % (car_status, transcript)}])
            for sentence in sentences:
                if not spoken:
                    print('  first sentence after %.2f s' % (time.time() - started), flush=True)
                say(sentence)
                spoken.append(sentence)
            history.extend(({'role': 'user', 'content': transcript}, {'role': 'assistant', 'content': ' '.join(spoken)}))
            del history[:-MAX_HISTORY_MESSAGES]
        except Refused:
            say(SPOKEN_REFUSAL[language])
        except anthropic.AnthropicError as error:
            print('  Claude failed:', type(error).__name__, str(error)[:300], flush=True)
            send_text({'error': type(error).__name__})
            say(SPOKEN_ERROR[language])
        print('  done after %.2f s: %s' % (time.time() - started, ' '.join(spoken)), flush=True)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'  # the reply ends when the connection closes

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({'model': MODEL, 'whisper': WHISPER_SIZE}).encode('ascii')
        self.send_response(200 if self.path == '/health' else 404)
        self.end_headers()
        self.wfile.write(body)

    def _frame(self, kind, payload):
        self.wfile.write(kind + struct.pack('>I', len(payload)) + payload)
        self.wfile.flush()

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        pcm = self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200 if url.path == '/utterance' else 404)
        self.end_headers()
        try:
            answer(pcm, urllib.parse.parse_qs(url.query).get('new') == ['1'], self.headers.get('X-Car-Status', ''),
                   lambda fields: self._frame(b'T', json.dumps(fields).encode('ascii')), lambda audio: self._frame(b'A', audio))
        except OSError as error:
            print('  car went away:', error, flush=True)


def keep_tunnel_open():
    command = ['ssh', '-N', '-i', SSH_KEY, '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15',
               '-o', 'ServerAliveCountMax=2', '-R', '%d:127.0.0.1:%d' % (PORT, PORT), CAR]
    while True:
        print('tunnel to', CAR, flush=True)
        subprocess.run(command)
        print('tunnel closed, next try in %d s' % TUNNEL_RETRY_S, flush=True)
        time.sleep(TUNNEL_RETRY_S)


if __name__ == '__main__':
    print('warming up', flush=True)
    transcribe(synthesize('Hallo.', 'de'))
    threading.Thread(target=keep_tunnel_open, daemon=True).start()
    print('ready: model %s, whisper %s' % (MODEL, WHISPER_SIZE), flush=True)
    http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
