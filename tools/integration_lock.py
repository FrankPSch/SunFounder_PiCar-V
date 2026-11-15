'''One writer at a time on master: the voice builder (build and deploy) and the chat (tools/push.py) run on the same
PC and take this lock, a file in the voice folder, before they change master. Whoever does not get it waits.
A lock whose process is gone, or older than STALE_AFTER_S, is taken over.'''
import contextlib
import ctypes
import json
import os
import time

import pc_settings

LOCK_FILE = os.path.join(pc_settings.VOICE_DIR, 'integration.lock')
STALE_AFTER_S = 2 * 3600        # longer than a build (feature_builder.BUILD_TIMEOUT_S + fix rounds) and a deploy
POLL_S = 5
STILL_ACTIVE = 259              # GetExitCodeProcess: the process runs
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class Busy(Exception):
    pass


def _process_runs(pid):
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def holder():
    '''{"owner", "pid", "since"} of the current holder, or None (free, or a stale lock).'''
    try:
        with open(LOCK_FILE, encoding='utf-8') as lock:
            held = json.load(lock)
    except (OSError, ValueError):
        return None
    if time.time() - held['since'] > STALE_AFTER_S or not _process_runs(held['pid']):
        return None
    return held


def _try_take(owner):
    if holder() is None and os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)  # stale
    try:
        descriptor = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, 'w', encoding='utf-8') as lock:
        json.dump({'owner': owner, 'pid': os.getpid(), 'since': time.time()}, lock)
    return True


@contextlib.contextmanager
def held(owner, wait_s=0, waiting=None):
    '''Holds the lock for the block. Waits up to wait_s, calling waiting(holder) once; raises Busy after that.'''
    deadline, told = time.time() + wait_s, False
    while not _try_take(owner):
        current = holder()
        if time.time() >= deadline:
            raise Busy('master is locked by %s' % (current or {}).get('owner', 'someone'))
        if waiting and not told and current:
            waiting(current)
            told = True
        time.sleep(POLL_S)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(LOCK_FILE)
