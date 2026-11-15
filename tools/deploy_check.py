'''After every deploy (chat push, voice deploy, rollback): the real car checked without driving. Each check is a
requirement variant (requirements.py) shown on the real car; the results go to <voice dir>/real_car_results.jsonl
for tools/requirements_report.py.  Usage: python tools/deploy_check.py [expected commit] [chat|voice|rollback]'''
import json
import subprocess
import sys
import time
import urllib.request

import car_ssh
import car_tunnel
import pc_settings
import prerender_voice
import requirements_report

STATE_URL = 'http://%s:8000/api/state' % pc_settings.CAR_HOST
HTTP_TIMEOUT_S = 10
SSH_TIMEOUT_S = 30
SOURCE_VARIANT = {'chat': 'chat push', 'voice': 'voice deploy', 'timer': 'update timer'}


def _car_state():
    with urllib.request.urlopen(STATE_URL, timeout=HTTP_TIMEOUT_S) as response:
        return json.loads(response.read().decode('utf-8'))


def _on_car(command):
    if pc_settings.switched_on('car_ssh_module'):
        return car_ssh.run(command, timeout_s=SSH_TIMEOUT_S).text
    result = subprocess.run(['ssh'] + car_tunnel.SSH_OPTIONS + [pc_settings.CAR, command], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,  # legacy ssh (PC switch car_ssh_module off)
                            timeout=SSH_TIMEOUT_S)
    return result.stdout.decode('utf-8', 'replace')


def _recordings_on_pc():
    return {language: len(names) for language, names in prerender_voice.local_recordings().items()}


def checks(expected_commit, source):
    '''[(requirement id, variant, passed, what was seen)] for the car as it is now.'''
    running = _on_car('git -C /home/pi/SunFounder_PiCar-V rev-parse HEAD').strip()
    listening = _on_car("ss -ltn | awk '{print $4}'")
    state = _car_state()
    speaker, audio = state.get('speaker', {}), state.get('audio', {})
    wanted_recordings = _recordings_on_pc()
    results = [
        ('S.update.remote', SOURCE_VARIANT.get(source, 'chat push'), running.startswith(expected_commit) if expected_commit else bool(running), 'car runs %s' % running[:7]),
        ('S.speech.on_car', 'recordings', speaker.get('recordings') == wanted_recordings, 'car %s, PC %s' % (speaker.get('recordings'), wanted_recordings)),
        ('S.speech.on_car', 'voice natural', speaker.get('voice') == 'natural', 'voice %s' % speaker.get('voice')),
        ('S.voice_server.reachable', '8766', '127.0.0.1:8766' in listening, 'tunnel to the voice server'),
        ('S.voice_server.reachable', '8767', '127.0.0.1:8767' in listening, 'tunnel to the launcher'),
    ]
    for language, missing in (audio.get('missing_words') or {}).items():
        results.append(('S.recognition.vocabulary', language, not missing, 'missing words: %s' % (', '.join(missing) or 'none')))
    return results


def run(expected_commit='', source='chat', log=print):
    '''Checks the car and keeps the passed ones as evidence. Returns (all passed, report lines).'''
    try:
        results = checks(expected_commit, source)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        log('deploy check: car not reachable (%s)' % error)
        return False, ['car not reachable: %s' % error]
    when = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(requirements_report.REAL_CAR_FILE, 'a', encoding='utf-8') as evidence:
        for requirement_id, variant, passed, seen in results:
            if passed:
                evidence.write(json.dumps({'id': requirement_id, 'variant': variant, 'evidence': 'real car', 'when': when}) + '\n')
    report = ['%s %s / %s: %s' % ('ok  ' if passed else 'FAIL', requirement_id, variant, seen) for requirement_id, variant, passed, seen in results]
    for line in report:
        log('deploy check: ' + line)
    return all(passed for _, _, passed, _ in results), report


if __name__ == '__main__':
    sys.exit(0 if run(*sys.argv[1:3])[0] else 1)
