'''Records everything the car says with fixed words (spoken_texts.sentences()) in the natural Piper voice of the PC and
copies what the car does not have yet to /home/pi/picar-data/voice_cache (voice_cache.py). The voice server runs it at
every start, so a new command gets its voice by itself; the car speaks espeak-ng for anything not recorded.
Usage: python tools/prerender_voice.py        (voice environment)'''
import os
import subprocess
import sys
import tarfile
import wave

import car_ssh
import car_tunnel
import pc_settings
import text_to_speech
from remote_control import spoken_texts, voice_cache

LOCAL_CACHE = os.path.join(pc_settings.VOICE_DIR, 'voice_cache')
CAR_CACHE = '/home/pi/picar-data/voice_cache'
VOICE_RATE = 22050          # Piper's own rate: the car converts to its playback rate while playing
SAMPLE_WIDTH = 2
SSH_TIMEOUT_S = 120


def render_missing(log):
    '''Records each sentence that has no recording yet. Returns how many were recorded.'''
    recorded = 0
    for language, text in sorted(spoken_texts.sentences()):
        path = voice_cache.path(LOCAL_CACHE, language, text)
        if os.path.isfile(path):
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        audio = text_to_speech.synthesize(text, language, rate=VOICE_RATE)
        with wave.open(path + '.part', 'wb') as recording:
            recording.setnchannels(1)
            recording.setsampwidth(SAMPLE_WIDTH)
            recording.setframerate(VOICE_RATE)
            recording.writeframes(audio)
        os.replace(path + '.part', path)
        recorded += 1
    log('voice cache: %d new recordings, %d sentences' % (recorded, len(spoken_texts.sentences())))
    return recorded


def _ssh(command, **options):
    return subprocess.run(['ssh'] + car_tunnel.SSH_OPTIONS + [pc_settings.CAR, command], timeout=SSH_TIMEOUT_S, **options)  # legacy ssh (PC switch car_ssh_module off)


def local_recordings():
    '''{language: [file names]} of the recordings on the PC.'''
    return {language: sorted(name for name in os.listdir(os.path.join(LOCAL_CACHE, language)) if name.endswith('.wav'))
            for language in ('de', 'en') if os.path.isdir(os.path.join(LOCAL_CACHE, language))}


def copy_missing_to_car(log):
    '''Copies the recordings the car does not have (by file name). Returns how many, or None when the car is not reachable.'''
    list_command = 'mkdir -p {0}/de {0}/en && cd {0} && ls de en'.format(CAR_CACHE)
    if pc_settings.switched_on('car_ssh_module'):
        result = car_ssh.run(list_command, timeout_s=SSH_TIMEOUT_S)
        listing = subprocess.CompletedProcess([], result.code, result.text)
    else:
        listing = _ssh(list_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
    if listing.returncode:
        log('voice cache: car not reachable, copy later')
        return None
    on_car, folder = set(), None
    for line in listing.stdout.splitlines():
        if line.endswith(':'):
            folder = line[:-1]
        elif line.strip():
            on_car.add('%s/%s' % (folder, line.strip()))
    missing = ['%s/%s' % (language, name) for language, names in local_recordings().items() for name in names if '%s/%s' % (language, name) not in on_car]
    if missing and pc_settings.switched_on('car_ssh_module'):
        if car_ssh.copy_tar({name: os.path.join(LOCAL_CACHE, name) for name in missing}, CAR_CACHE, SSH_TIMEOUT_S):
            log('voice cache: copy to the car failed')
            return None
    elif missing:
        untar = subprocess.Popen(['ssh'] + car_tunnel.SSH_OPTIONS + [pc_settings.CAR, 'tar xf - -C ' + CAR_CACHE], stdin=subprocess.PIPE)  # legacy ssh (PC switch car_ssh_module off)
        with tarfile.open(fileobj=untar.stdin, mode='w|') as archive:
            for name in missing:
                archive.add(os.path.join(LOCAL_CACHE, name), arcname=name)
        untar.stdin.close()
        if untar.wait(SSH_TIMEOUT_S):
            log('voice cache: copy to the car failed')
            return None
    log('voice cache: %d recordings copied to the car' % len(missing))
    return len(missing)


def update(log):
    render_missing(log)
    return copy_missing_to_car(log)


if __name__ == '__main__':
    sys.exit(0 if update(print) is not None else 1)
