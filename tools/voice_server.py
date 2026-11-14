# -*- coding: utf-8 -*-
'''
PC half of the car's Claude conversation: speech -> Whisper (speech_to_text) -> Claude (claude_chat) -> Piper
(text_to_speech) -> speech. The car stays in charge of driving and stop (remote_control/conversation.py); this only talks.
Endpoints, headers and frames: remote_control/remote_control/voice_protocol.py.

Start: tools/voice_launcher.py starts and stops it with the car; tools\\voice_server.bat by hand also works.
Needs the user variable ANTHROPIC_API_KEY (CLAUDE_VOICE_MODEL=echo works without it).
Connection: listens on this PC's loopback only and opens an SSH tunnel, so the car reaches it at its own 127.0.0.1.
No firewall rule, and nobody else on the network can use the key.
Feature requests by voice (propose -> "go" builds -> "deploy" installs): tools/feature_builder.py.
'''
import http.server
import json
import os
import subprocess
import threading
import time
import urllib.parse

import car_tunnel
import claude_chat
import feature_builder
import pc_settings
import speech_to_text
import text_to_speech

protocol = pc_settings.voice_protocol
TUNNEL_RETRY_S = 5
SHUTDOWN_DELAY_S = 0.5      # the reply goes out first
GREETINGS = {'de': 'Hallo.', 'en': 'Hello.'}  # the car plays it after "hey claude"
BUSY_STATES = ('building', 'deploying')
assert set(GREETINGS) == set(protocol.LANGUAGES)


def log(text):
    print('%s %s' % (time.strftime('%H:%M:%S'), text), flush=True)


chat = claude_chat.Chat()
news = []                   # (language, sentence) for the car's next GET /news
news_lock = threading.Lock()


def announce(language, sentence):
    log('news: ' + sentence)
    with news_lock:
        news.append((language, sentence))


builder = feature_builder.Builder(announce)


class Turn(object):
    '''One utterance from the car and the spoken answer, sent as frames while they are made.'''
    def __init__(self, handler, car):
        self.handler, self.car, self.language = handler, car, protocol.LANGUAGES[0]

    def say(self, sentence):
        self.handler.send_text({'sentence': sentence})
        self.handler.send_audio(text_to_speech.synthesize(sentence, self.language, self.handler.playback_rate()))

    def run(self, audio, new_conversation):
        started = time.time()
        with chat.lock:
            if new_conversation:
                chat.forget()
            transcript, self.language = speech_to_text.transcribe(audio, self.car['languages'], feature_builder.AWAITED_WORD.get(builder.state))
            self.handler.send_text({'transcript': transcript, 'language': self.language})
            log('heard (%.2f s for %.1f s, %s): %s' % (time.time() - started, len(audio) / 2.0 / protocol.UTTERANCE_RATE, self.language, transcript))
            if transcript:
                self._answer(transcript, started)

    def _answer(self, transcript, started):
        spoken = []
        try:
            for sentence in chat.sentences(self._status_line(), transcript, lambda name, arguments: self._run_tool(name, arguments, transcript)):
                if not spoken:
                    log('  first sentence after %.2f s' % (time.time() - started))
                self.say(sentence)
                spoken.append(sentence)
        except claude_chat.Refused:
            self.say(claude_chat.SPOKEN_REFUSAL[self.language])
        except claude_chat.anthropic.AnthropicError as error:
            log('  Claude failed: %s %s' % (type(error).__name__, str(error)[:300]))
            self.handler.send_text({'error': type(error).__name__})
            self.say(claude_chat.SPOKEN_ERROR[self.language])
        log('  done after %.2f s: %s' % (time.time() - started, ' '.join(spoken)))

    def _status_line(self):
        switches = '; '.join('%s=%s (%s)' % (name, 'on' if on else 'off', help_text) for name, on, help_text in self.car['features'])
        car_commands = '; '.join('%s (%s)' % (name, description) for name, description in self.car['commands'])
        return '[car: %s]\n[switches: %s]\n[commands: %s]\n[%s]\n' % (self.car['status'], switches, car_commands, builder.describe())

    def _run_tool(self, name, arguments, transcript):
        log('  tool %s %s' % (name, json.dumps(arguments, ensure_ascii=False)[:300]))
        if name == 'set_feature':
            known = [feature[0] for feature in self.car['features']]
            if arguments['name'] not in known:
                return 'Unknown feature. Known: ' + ', '.join(known)
            self.handler.send_text({'set_feature': arguments['name'], 'on': arguments['on']})
            return 'Switched %s %s.' % (arguments['name'], 'on' if arguments['on'] else 'off')
        if name == 'run_command':
            known = [command[0] for command in self.car['commands']]
            if arguments['name'] not in known:
                return 'Unknown command. Known: ' + ', '.join(known)
            self.handler.send_text({'run_command': arguments['name']})
            return 'Ran %s.' % arguments['name']
        if name == 'propose_feature':
            return builder.propose(arguments['title'], arguments['understanding'], arguments['options'], self.language)
        if name == 'start_build':
            return builder.start(arguments['option'], transcript)
        return builder.deploy(transcript)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'  # the reply ends when the connection closes

    def log_message(self, *args):
        pass

    # ---- what the car sent ----
    def playback_rate(self):
        rate = self.headers.get(protocol.PLAYBACK_RATE, '')
        return int(rate) if rate.isdigit() and int(rate) in protocol.PLAYBACK_RATES else protocol.DEFAULT_SPEECH_RATE

    def car(self):
        languages = tuple(name for name in (self.headers.get(protocol.VOICE_LANGUAGES) or '').split(',') if name in protocol.LANGUAGES)
        return {'status': self.headers.get(protocol.STATUS, ''), 'features': json.loads(self.headers.get(protocol.FEATURES) or '[]'),
                'commands': json.loads(self.headers.get(protocol.COMMANDS) or '[]'), 'languages': languages or protocol.LANGUAGES}

    # ---- replies ----
    def start_frames(self):
        self.send_response(200)
        self.send_header(protocol.AUDIO_RATE, str(self.playback_rate()))
        self.end_headers()

    def send_text(self, fields):
        self._send(protocol.frame(protocol.TEXT, json.dumps(fields).encode('ascii')))

    def send_audio(self, audio):
        self._send(protocol.frame(protocol.AUDIO, audio))

    def _send(self, data):
        self.wfile.write(data)
        self.wfile.flush()

    def _reply(self, status, body=b''):
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)

    # ---- endpoints ----
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path == '/hello':
                self._hello((urllib.parse.parse_qs(url.query).get('language') or [''])[0])
            elif url.path == '/news':
                self._news()
            elif url.path == '/health':
                self._reply(200, json.dumps({'model': claude_chat.MODEL, 'whisper': speech_to_text.WHISPER_SIZE, 'build': builder.describe()}).encode('ascii'))
            else:
                self._reply(404)
        except OSError as error:
            log('  car went away: %s' % error)

    def _hello(self, language):
        language = language if language in GREETINGS else protocol.LANGUAGES[0]
        self.start_frames()
        self.send_text({'sentence': GREETINGS[language]})
        self.send_audio(text_to_speech.synthesize(GREETINGS[language], language, self.playback_rate()))

    def _news(self):
        with news_lock:
            waiting, news[:] = list(news), []
        self.start_frames()
        for language, sentence in waiting:
            self.send_text({'sentence': sentence})
            self.send_audio(text_to_speech.synthesize(sentence, language, self.playback_rate()))

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == '/shutdown':
            return self._shutdown()
        audio = self.rfile.read(int(self.headers['Content-Length']))
        if url.path != '/utterance':
            return self._reply(404)
        self.start_frames()
        try:
            Turn(self, self.car()).run(audio, urllib.parse.parse_qs(url.query).get('new') == ['1'])
        except OSError as error:
            log('  car went away: %s' % error)

    def _shutdown(self):
        busy = builder.state in BUSY_STATES
        self._reply(409 if busy else 200, builder.describe().encode('ascii', 'replace'))
        if not busy:
            log('shutdown requested')
            # the whole process tree: the SSH tunnel and Piper must not outlive the server
            threading.Timer(SHUTDOWN_DELAY_S, subprocess.run, (['taskkill', '/T', '/F', '/PID', str(os.getpid())],),
                            {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}).start()


def main():
    print('warming up', flush=True)
    for language in protocol.LANGUAGES:
        speech_to_text.transcribe(text_to_speech.synthesize('Hallo.', language))
    threading.Thread(target=car_tunnel.keep_open, args=(pc_settings.CAR, protocol.VOICE_PORT, TUNNEL_RETRY_S, log), name='tunnel', daemon=True).start()
    print('ready: model %s, whisper %s' % (claude_chat.MODEL, speech_to_text.WHISPER_SIZE), flush=True)
    http.server.ThreadingHTTPServer(('127.0.0.1', protocol.VOICE_PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
