'''SSH reverse tunnel from this PC to the car (car 127.0.0.1:port -> PC 127.0.0.1:port), kept open; used by
voice_server.py (8766) and voice_launcher.py (8767). A connection that died without the car noticing keeps the
car's port bound for minutes ("remote port forwarding failed", 75 tries on 2026-11-14): then the stale sshd
session holding it is ended, and the next try gets the port.'''
import os
import subprocess
import time

SSH_KEY = os.path.expanduser('~/.ssh/picar_ed25519')
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
SSH_OPTIONS = ['-i', SSH_KEY, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2']


def _free_car_port(car, port):
    '''Ends the car process listening on 127.0.0.1:port: only called when our own tunnel is not up, so it is a stale one.'''
    # sudo: sshd sessions are not dumpable, so without root neither ss -p nor fuser can see who holds the port
    command = 'sudo -n fuser -k -n tcp %d' % port
    subprocess.run(['ssh'] + SSH_OPTIONS + [car, command], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, creationflags=NO_WINDOW, timeout=30)


def keep_open(car, port, retry_s, log, wanted=lambda: True):
    '''Never returns. wanted(): whether to try now (the launcher skips while the car is off).'''
    command = ['ssh', '-N'] + SSH_OPTIONS + ['-o', 'ExitOnForwardFailure=yes', '-R', '%d:127.0.0.1:%d' % (port, port), car]
    while True:
        if wanted():
            started = time.time()
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
            if b'forwarding failed' in result.stderr:
                log('car port %d still held by an old connection: freeing it' % port)
                try:
                    _free_car_port(car, port)
                except subprocess.TimeoutExpired:
                    pass
            elif time.time() - started > 60:
                log('tunnel %d to the car closed' % port)
        time.sleep(retry_s)
