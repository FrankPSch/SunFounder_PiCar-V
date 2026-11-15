# -*- coding: utf-8 -*-
'''
Runs the PC voice server (tools/voice_server.py) only while the car is on, and lets the car start and stop it by voice.
Starts with Windows (tools/install_voice_launcher.bat), no window; log in VOICE_DIR\\launcher.log, server output in server.log.

  car reachable (SSH port)           -> start the voice server, unless it was stopped by voice
  car gone for CAR_GONE_S            -> stop the voice server (not during a feature build); the next car start starts it again
  car says "Claude starten"/"beenden" -> POST /start, /stop on 127.0.0.1:8767, which the car reaches through our own SSH tunnel

  GET  /status   {"server": "running" | "starting" | "off", "car": bool, "stopped_by_voice": bool}
  POST /start    clears the voice stop and starts the server
  POST /stop     stops the server until the car is switched off and on again, or "Claude starten"; 409 while a build runs
A server started by hand (voice_server.bat) is seen through its /health and left running.
'''
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import car_tunnel
import pc_settings

HERE = os.path.dirname(os.path.abspath(__file__))
VOICE_DIR, PYTHON, CAR_HOST = pc_settings.VOICE_DIR, pc_settings.VOICE_PYTHON, pc_settings.CAR_HOST
PORT = pc_settings.voice_protocol.LAUNCHER_PORT
VOICE_SERVER_URL = 'http://127.0.0.1:%d' % pc_settings.voice_protocol.VOICE_PORT
WATCH_S = 15
CAR_GONE_S = 120
TUNNEL_RETRY_S = 10
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: also hides ssh and Piper, which inherit the hidden console

if sys.stdout is None:  # pythonw
    sys.stdout = sys.stderr = open(os.path.join(VOICE_DIR, 'launcher.log'), 'a', buffering=1, encoding='utf-8')


def log(text):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), text, flush=True)


def car_reachable():
    try:
        socket.create_connection((CAR_HOST, 22), timeout=3).close()
        return True
    except OSError:
        return False


def _voice_server(path, method='GET'):
    '''(HTTP status, body) of the voice server, or (None, error) when it does not answer.'''
    try:
        with urllib.request.urlopen(urllib.request.Request(VOICE_SERVER_URL + path, method=method, data=b'' if method == 'POST' else None), timeout=3) as reply:
            return reply.status, reply.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode('utf-8', 'replace')
    except OSError as error:
        return None, str(error)


class Launcher(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.stopped_by_voice = False
        self.car_up = False
        self.car_seen = 0.0

    def server_state(self):
        if _voice_server('/health')[0] == 200:
            return 'running'
        if self.process is not None and self.process.poll() is None:
            return 'starting'  # Whisper and Piper warm up for about 20 s before the server answers
        return 'off'

    def start(self, reason):
        if self.server_state() != 'off':
            return
        log('starting voice server (%s)' % reason)
        with open(os.path.join(VOICE_DIR, 'server.log'), 'a', encoding='utf-8') as server_log:  # kept: the conversations so far
            server_log.write('==== %s voice server start (%s)\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), reason))
            server_log.flush()
            self.process = subprocess.Popen([PYTHON, '-u', os.path.join(HERE, 'voice_server.py')], cwd=HERE, stdout=server_log,
                                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
                                            env=dict(os.environ, PYTHONIOENCODING='utf-8'))  # the log is read as UTF-8, not cp1252

    def stop(self, reason):
        '''HTTP status: 200 stopped (or was off), 409 a feature build or deploy runs, 502 the server refused.'''
        status, body = _voice_server('/shutdown', 'POST')
        if status in (409, 404):  # 404: a server from before /shutdown existed
            log('not stopped (%s): HTTP %d %s' % (reason, status, body[:200]))
            return 409 if status == 409 else 502
        for _ in range(10):  # the server ends itself 0.5 s after answering
            if status is None or _voice_server('/health')[0] is None:
                break
            time.sleep(0.5)
        if status is None and self.process is not None and self.process.poll() is None:  # still warming up
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(self.process.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if status is not None or self.process is not None:
            log('voice server stopped (%s)' % reason)
        self.process = None
        return 200

    def describe(self):
        return {'server': self.server_state(), 'car': self.car_up, 'stopped_by_voice': self.stopped_by_voice}

    def watch(self):
        while True:
            car_up = car_reachable()
            with self.lock:
                if car_up != self.car_up:
                    log('car ' + ('on' if car_up else 'off'))
                self.car_up = car_up
                if car_up:
                    self.car_seen = time.time()
                    if not self.stopped_by_voice:
                        self.start('car is on')
                elif time.time() - self.car_seen > CAR_GONE_S:
                    self.stopped_by_voice = False
                    if self.server_state() != 'off':
                        self.stop('car off for %d s' % CAR_GONE_S)
            time.sleep(WATCH_S)


launcher = Launcher()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, status, fields):
        body = json.dumps(fields).encode('ascii')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != '/status':
            return self._reply(404, {})
        self._reply(200, launcher.describe())

    def do_POST(self):
        with launcher.lock:
            if self.path == '/start':
                launcher.stopped_by_voice = False
                launcher.start('car asked')
                return self._reply(200, launcher.describe())
            if self.path == '/stop':
                status = launcher.stop('car asked')
                if status == 200:
                    launcher.stopped_by_voice = True
                return self._reply(status, launcher.describe())
        self._reply(404, {})


def keep_tunnel_open():
    car_tunnel.keep_open(pc_settings.CAR, PORT, TUNNEL_RETRY_S, log, lambda: launcher.car_up)


if __name__ == '__main__':
    server = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)  # a second launcher fails here and exits
    log('launcher ready, car %s' % CAR_HOST)
    threading.Thread(target=launcher.watch, daemon=True).start()
    threading.Thread(target=keep_tunnel_open, daemon=True).start()
    server.serve_forever()
