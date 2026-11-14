# -*- coding: utf-8 -*-
'''
PC half of the car's Claude conversation: speech -> Whisper -> Claude -> Piper -> speech.
The car stays in charge of steering and stop (remote_control/conversation.py); this only talks.

Start: tools\\voice_server.bat (needs the user variable ANTHROPIC_API_KEY; CLAUDE_VOICE_MODEL=echo works without it).
Connection: the server listens on this PC's loopback only and opens an SSH tunnel, so the car reaches it at its own
127.0.0.1:8766. No firewall rule, and nobody else on the network can use it.

  GET  /health               {"model": ..., "whisper": ..., "build": ...}
  POST /utterance?new=0|1    body: 16 kHz mono 16-bit audio, headers X-Car-Status: JSON, X-Car-Features: [[name, on, help], ...]
       reply: frames of kind (1 byte) + length (4 bytes) + payload; 'T' = JSON text, 'A' = 16 kHz mono 16-bit audio
       T fields: transcript + language, sentence, error, set_feature + on (the car flips that switch)
  GET  /news                 announcements waiting for the car (build finished, ...), same frames; empty when none
Feature requests by voice (propose -> "go" builds -> "deploy" installs): tools/feature_builder.py.
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
import wave

VOICE_DIR = os.environ.get('PICAR_VOICE_DIR', r'D:\a_ClaudeCowork\PiCar\voice')
os.environ['PATH'] = os.pathsep.join((os.path.join(VOICE_DIR, 'cublas'), r'C:\ProgramData\Anaconda3\Library\bin', os.environ['PATH']))

import anthropic
import numpy as np
from faster_whisper import WhisperModel

import feature_builder

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
    segments, info = whisper_model.transcribe(samples, language=language, beam_size=1, vad_filter=True, hotwords='Claude, go, deploy', condition_on_previous_text=False)
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
MAX_TOKENS = 4000           # thinking counts too
MAX_HISTORY_MESSAGES = 80
SENTENCE_END = re.compile(r'(?<=[.!?:;])\s+')
SYSTEM_PROMPT = (
    'You are the voice of a small robot car (SunFounder PiCar-V) talking with its owner in the room. '
    'Everything you write is read aloud by a speech synthesizer, so answer in one or two short spoken sentences, '
    'in the language the owner used (German or English). No lists, markdown, emoji or abbreviations. '
    'What the owner says comes from speech recognition and can contain mistakes; go by the likely meaning. '
    'Each message starts with the current car status in square brackets. Mention it only when it matters. '
    'You can talk and read the status, but you cannot move the car: the owner steers with single spoken words such as '
    '"links", "rechts", "vorwärts" and "stopp". Say so if asked to drive. '
    'You can switch the car\'s features on and off with set_feature. Before switching on a feature that makes the car drive '
    '(tracking_drive, room_navigation), ask once whether the car has room. '
    'New features: when the owner asks for something the car cannot do yet, say in one or two sentences what you understood, '
    'offer two or three short numbered options for how to build it, call propose_feature, and ask the owner to say "go" with '
    'the number. Call start_build only after the owner said "go". When a build is ready and the owner says "deploy", '
    'call deploy_feature. The status line tells you the state of the current feature request.')
SPOKEN_ERROR = {'de': 'Ich kann Claude gerade nicht erreichen.', 'en': 'I cannot reach Claude right now.'}
SPOKEN_REFUSAL = {'de': 'Dazu kann ich nichts sagen.', 'en': 'I cannot help with that.'}
client = None if MODEL == 'echo' else anthropic.Anthropic()


def _tool(name, description, properties, required):
    return {'name': name, 'description': description, 'eager_input_streaming': True,
            'input_schema': {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}}


TOOLS = [
    _tool('set_feature', 'Switch one of the car\'s features on or off. The names and current states are in the status line.',
          {'name': {'type': 'string'}, 'on': {'type': 'boolean'}}, ['name', 'on']),
    _tool('propose_feature', 'Save a new feature request with your understanding and the options, before asking for "go".',
          {'title': {'type': 'string', 'description': 'two to five words'}, 'understanding': {'type': 'string'},
           'options': {'type': 'array', 'items': {'type': 'string'}, 'description': 'two or three ways to build it'}},
          ['title', 'understanding', 'options']),
    _tool('start_build', 'Build the proposed feature. Only after the owner said "go".',
          {'option': {'type': 'string', 'description': 'the option the owner chose, in full'}}, ['option']),
    _tool('deploy_feature', 'Install the built feature on the car. Only after the owner said "deploy".', {}, []),
]
TOOL_TYPES = {tool['name']: {key: value['type'] for key, value in tool['input_schema']['properties'].items()} for tool in TOOLS}
JSON_TYPES = {'string': str, 'boolean': bool, 'array': list}


class Refused(Exception):
    pass


def _valid(name, arguments):
    '''Tool input streams unchecked (eager_input_streaming): check it here before running the tool.'''
    types = TOOL_TYPES.get(name)
    return (types is not None and isinstance(arguments, dict) and set(arguments) == set(types)
            and all(isinstance(arguments[key], JSON_TYPES[kind]) for key, kind in types.items()))


def claude_sentences(turn, run_tool):
    '''Streams Claude's answer sentence by sentence; runs the tools it calls and goes on until it is done.
    turn: history + this turn's user message; every new message is appended to it.'''
    options = {'output_config': {'effort': EFFORT}} if EFFORT else {}
    while True:
        with client.messages.stream(model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT, messages=turn, tools=TOOLS,
                                    cache_control={'type': 'ephemeral'}, **options) as stream:
            unfinished = ''
            for text in stream.text_stream:
                parts = SENTENCE_END.split(unfinished + text)
                unfinished = parts.pop()
                for sentence in parts:
                    yield sentence
            if unfinished.strip():
                yield unfinished
            message = stream.get_final_message()
        if message.stop_reason == 'refusal':
            raise Refused()
        turn.append({'role': 'assistant', 'content': message.content})
        if message.stop_reason != 'tool_use':  # max_tokens with a cut-off tool call: do not run it
            return
        results = []
        for block in message.content:
            if block.type == 'tool_use':
                if _valid(block.name, block.input):
                    results.append({'type': 'tool_result', 'tool_use_id': block.id, 'content': run_tool(block.name, block.input)})
                else:
                    results.append({'type': 'tool_result', 'tool_use_id': block.id, 'is_error': True,
                                    'content': json.dumps({'INVALID_JSON': json.dumps(block.input)})})
        turn.append({'role': 'user', 'content': results})


## Text to speech
VOICES = {'de': 'de_DE-thorsten-low', 'en': 'en_US-lessac-low'}
PIPER = os.path.join(VOICE_DIR, 'piper', 'piper', 'piper.exe')
assert set(VOICES) == set(LANGUAGES)
for _voice in VOICES.values():
    with open(os.path.join(VOICE_DIR, 'piper', _voice + '.onnx.json'), encoding='utf-8') as _config:
        assert json.load(_config)['audio']['sample_rate'] == 16000, 'the car plays 16 kHz only: ' + _voice


SPEECH_DIR = os.path.join(feature_builder.WORK_DIR, 'tmp')  # not on D:, which ran full
os.makedirs(SPEECH_DIR, exist_ok=True)
piper_processes = {}


def synthesize(sentence, language):
    '''Piper stays loaded (starting it costs about 1 s): each input line becomes a wav file, whose path it prints when done.'''
    process = piper_processes.get(language)
    if process is None or process.poll() is not None:
        process = piper_processes[language] = subprocess.Popen(
            [PIPER, '--model', os.path.join(VOICE_DIR, 'piper', VOICES[language] + '.onnx'), '--output_dir', SPEECH_DIR],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    process.stdin.write((' '.join(sentence.replace('*', ' ').split()) + '\n').encode('utf-8'))
    process.stdin.flush()
    path = process.stdout.readline().decode('utf-8').strip()
    with wave.open(path, 'rb') as speech:
        audio = speech.readframes(speech.getnframes())
    os.remove(path)
    return audio


## One conversation: the car has one microphone
history = []
history_lock = threading.Lock()
news = []                   # (language, sentence) for the car's next GET /news
news_lock = threading.Lock()


def announce(language, sentence):
    print('%s news: %s' % (time.strftime('%H:%M:%S'), sentence), flush=True)
    with news_lock:
        news.append((language, sentence))


builder = feature_builder.Builder(announce)


def answer(pcm, new_conversation, car_status, car_features, send_text, send_audio):
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

        def run_tool(name, arguments):
            print('  tool %s %s' % (name, json.dumps(arguments, ensure_ascii=False)[:300]), flush=True)
            if name == 'set_feature':
                known = [feature[0] for feature in car_features]
                if arguments['name'] not in known:
                    return 'Unknown feature. Known: ' + ', '.join(known)
                send_text({'set_feature': arguments['name'], 'on': arguments['on']})
                return 'Switched %s %s.' % (arguments['name'], 'on' if arguments['on'] else 'off')
            if name == 'propose_feature':
                return builder.propose(arguments['title'], arguments['understanding'], arguments['options'], language)
            if name == 'start_build':
                return builder.start(arguments['option'], transcript)
            return builder.deploy(transcript)

        spoken = []
        try:
            if client is None:
                say(transcript)
                spoken.append(transcript)
            else:
                switches = '; '.join('%s=%s (%s)' % (name, 'on' if on else 'off', help_text) for name, on, help_text in car_features)
                status = '[car: %s]\n[switches: %s]\n[%s]\n' % (car_status, switches, builder.describe())
                turn = history + [{'role': 'user', 'content': status + transcript}]
                for sentence in claude_sentences(turn, run_tool):
                    if not spoken:
                        print('  first sentence after %.2f s' % (time.time() - started), flush=True)
                    say(sentence)
                    spoken.append(sentence)
                history[:] = turn  # append-only, so the thinking blocks in it stay valid
                while len(history) > MAX_HISTORY_MESSAGES or (history and not isinstance(history[0]['content'], str)):
                    del history[0]
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
        if self.path == '/news':
            with news_lock:
                waiting, news[:] = list(news), []
            self.send_response(200)
            self.end_headers()
            try:
                for language, sentence in waiting:
                    self._frame(b'T', json.dumps({'sentence': sentence}).encode('ascii'))
                    self._frame(b'A', synthesize(sentence, language))
            except OSError as error:
                print('  car went away during news:', error, flush=True)
            return
        body = json.dumps({'model': MODEL, 'whisper': WHISPER_SIZE, 'build': builder.describe()}).encode('ascii')
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
                   json.loads(self.headers.get('X-Car-Features') or '[]'), lambda fields: self._frame(b'T', json.dumps(fields).encode('ascii')), lambda audio: self._frame(b'A', audio))
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
    for warm_up_language in LANGUAGES:
        transcribe(synthesize('Hallo.', warm_up_language))
    threading.Thread(target=keep_tunnel_open, daemon=True).start()
    print('ready: model %s, whisper %s' % (MODEL, WHISPER_SIZE), flush=True)
    http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
