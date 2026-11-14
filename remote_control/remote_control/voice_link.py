'''The car's connection to the PC (voice_protocol.py): the voice server and its launcher, both through SSH tunnels.
Calls raise LinkError when the PC does not answer; play_reply() plays a reply's speech and hands its text fields on.'''
import http.client
import json
import time

from . import speaker, voice_protocol as protocol

HOST = '127.0.0.1'
QUICK_TIMEOUT_S = 6         # health, greeting, launcher
ANSWER_TIMEOUT_S = 30       # Claude thinking
SERVER_READY_S = 90         # Whisper and Piper warm up on the PC
READY_POLL_S = 2
CONNECTION_ERRORS = (OSError, http.client.HTTPException, ValueError)
playback_headers = {protocol.PLAYBACK_RATE: str(speaker.PLAYBACK_RATE)}
voice_server_port, launcher_port = protocol.VOICE_PORT, protocol.LAUNCHER_PORT


class LinkError(Exception):
    pass


def _request(port, method, path, timeout_s, body=None, headers=None):
    try:
        connection = http.client.HTTPConnection(HOST, port, timeout=timeout_s)
        connection.request(method, path, body, headers or {})
        return connection.getresponse()
    except CONNECTION_ERRORS as error:
        raise LinkError(str(error))


def greeting(language):
    '''The reply to play after "hey claude", or None from a voice server that has no greeting yet.'''
    response = _request(voice_server_port, 'GET', '/hello?language=' + language, QUICK_TIMEOUT_S, headers=playback_headers)
    if response.status != 200:
        response.read()
        return None
    return response


def utterance(audio, new_conversation, car_headers):
    response = _request(voice_server_port, 'POST', '/utterance?new=%d' % new_conversation, ANSWER_TIMEOUT_S, audio,
                        dict(playback_headers, **car_headers))
    if response.status != 200:
        raise LinkError('HTTP %d' % response.status)
    return response


def news():
    '''Announcements waiting on the PC (a feature build finished, ...), or None.'''
    response = _request(voice_server_port, 'GET', '/news', ANSWER_TIMEOUT_S, headers=playback_headers)
    return response if response.status == 200 else None


def voice_server_answers():
    try:
        return _request(voice_server_port, 'GET', '/health', QUICK_TIMEOUT_S).status == 200
    except LinkError:
        return False


def switch_voice_server(action):
    '''action "start" or "stop" through the launcher. Returns None when done, else why not.'''
    try:
        response = _request(launcher_port, 'POST', '/' + action, QUICK_TIMEOUT_S)
        server_state = json.loads(response.read().decode('ascii') or '{}').get('server')
    except (LinkError,) + CONNECTION_ERRORS as error:
        return 'PC launcher not reachable (%s)' % error
    if response.status == 409:
        return 'voice server not stopped: a feature build is running'
    if action == 'stop':
        return None
    deadline = time.time() + SERVER_READY_S
    while server_state != protocol.SERVER_RUNNING and time.time() < deadline:
        time.sleep(READY_POLL_S)
        if voice_server_answers():
            server_state = protocol.SERVER_RUNNING
    return None if server_state == protocol.SERVER_RUNNING else 'voice server did not start within %d s' % SERVER_READY_S


def play_reply(response, on_text, on_speaking=lambda: None):
    '''Plays the reply's audio frames and calls on_text(fields) for its text frames, in order.'''
    stream = None
    rate = int(response.getheader(protocol.AUDIO_RATE) or protocol.DEFAULT_SPEECH_RATE)
    try:
        for kind, payload in protocol.read_frames(response):
            if kind == protocol.TEXT:
                on_text(json.loads(payload.decode('ascii')))
            elif kind == protocol.AUDIO:
                if stream is None:
                    on_speaking()
                    stream = speaker.open_stream(rate)
                stream.write(payload)
    finally:
        if stream is not None:
            stream.close()
