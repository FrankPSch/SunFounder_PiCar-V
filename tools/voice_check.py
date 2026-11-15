'''Learn and check the spoken commands with the car's own speech recogniser (Vosk, the same grammar as the car).
Nothing here moves the car.

  learn      Starts a learning session in the running car service (remote_control/learning.py) and shows each try.
             Same as saying "Befehle lernen". Ctrl-C ends it, like "Lernen beenden".
  replay     Every kept try again through the recognisers: was it understood then, is it now. On the car or the PC.
  checklist  All commands as a Markdown list to read along.

  python3 tools/voice_check.py learn [--language de|en] [--group drive]
  python3 tools/voice_check.py replay [--recordings DIR] [--models DIR]
  python3 tools/voice_check.py checklist
Exit code of learn and replay: 0 = every phrase understood.
'''
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'remote_control'))
from remote_control import phrase_check, vocabulary  # noqa: E402

CAR_URL = 'http://localhost:8000'
POLL_S = 0.5
HTTP_TIMEOUT_S = 10
CAR_DATA_DIR = '/home/pi/picar-data'  # features.DATA_DIR on the car; importing features would write its settings file


# ---- learn: the car service runs the session ----
def car_command(car_url, body):
    request = urllib.request.Request(car_url + '/api/cmd', json.dumps(body).encode('utf-8'), {'Content-Type': 'application/json'})
    urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S).read()


def learning_state(car_url):
    with urllib.request.urlopen(car_url + '/api/state', timeout=HTTP_TIMEOUT_S) as response:
        return json.loads(response.read().decode('utf-8'))['audio']['learning']


def learn(car_url, language, group):
    car_command(car_url, {'cmd': 'learn', 'on': True, 'language': language, 'group': group})
    shown, state = 0, {'active': True, 'results': []}
    try:
        while state['active'] or shown < len(state['results']):
            time.sleep(POLL_S)
            state = learning_state(car_url)
            for result in state['results'][shown:]:
                print_result(result)
            shown = len(state['results'])
    except KeyboardInterrupt:
        car_command(car_url, {'cmd': 'learn', 'on': False})
        print('learning ended')
    if state.get('error'):
        print('error:', state['error'])
    return state['results']


# ---- replay: the kept tries again ----
def load_models(models_dir):
    models = {language: phrase_check.model(os.path.join(models_dir, folder)) for language, folder in vocabulary.MODEL_FOLDERS.items()
              if os.path.isdir(os.path.join(models_dir, folder))}
    if not models:
        raise SystemExit('no Vosk model in %s (run tools/install_extras.sh)' % models_dir)
    return models


def replay(models, recordings_dir):
    '''Each kept try: its result now, with what the car heard when it was recorded as "then".'''
    results = []
    for recorded in phrase_check.recorded_results(recordings_dir):
        if recorded['language'] in models:
            audio = phrase_check.load_wav(os.path.join(recordings_dir, recorded['file']))
            result = dict(phrase_check.hear(models, recorded['language'], recorded['phrase'], audio).as_dict(),
                          condition=recorded['condition'], then=recorded['verdict'])
            print_result(result)
            results.append(result)
    if not results:
        raise SystemExit('no recordings in %s: run learn first' % recordings_dir)
    return results


# ---- report ----
def print_result(result):
    others = ', '.join('%s: %s' % item for item in sorted(result['other_languages'].items()))
    then = ' (then %s)' % result['then'] if result.get('then', result['verdict']) != result['verdict'] else ''
    print('%-7s %-2s  %-16s %-18s %4.2f %6.1f dBFS  %s%s  %s' % (result['condition'], result['language'], result['phrase'], result['heard'] or '-',
                                                              result['confidence'], result['peak_dbfs'], result['verdict'], then, others))


def all_understood(results):
    '''A phrase counts as understood when one of its tries was.'''
    phrases = {(result['language'], result['phrase']) for result in results}
    understood = {(result['language'], result['phrase']) for result in results if result['verdict'] == 'understood'}
    print('%d of %d phrases understood' % (len(understood), len(phrases)))
    return bool(phrases) and understood == phrases


def checklist():
    lines = ['# PiCar voice commands', '', 'Switches: voice_commands for the commands, claude_voice for hey claude and Claude start/stop. Stop always listens.',
             'Practise them all: say "Befehle lernen" (or run tools/voice_check.py learn), end with "Lernen beenden".']
    for group, names in vocabulary.GROUP_NAMES.items():
        lines += ['', '## %s / %s' % (names['de'], names['en']), '', '| Deutsch | English | Was passiert | What happens |', '|---|---|---|---|']
        for spoken in vocabulary.SPOKEN:
            if spoken.group == group:
                lines.append('| %s | %s | %s | %s |' % (', '.join(spoken.de), ', '.join(spoken.en), *vocabulary.EXPLANATIONS[spoken.name]))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('mode', choices=('learn', 'replay', 'checklist'))
    parser.add_argument('--language', choices=vocabulary.LANGUAGES, default='de')
    parser.add_argument('--group', choices=sorted(vocabulary.GROUP_NAMES))
    parser.add_argument('--car', default=CAR_URL, help='car web address, default %s (on the car)' % CAR_URL)
    parser.add_argument('--recordings', default=os.path.join(CAR_DATA_DIR, 'voice_check'))
    parser.add_argument('--models', default=os.path.join(CAR_DATA_DIR, 'models'), help='folder with the Vosk models')
    args = parser.parse_args(argv)
    if args.mode == 'checklist':
        print(checklist())
        return 0
    if args.mode == 'learn':
        results = learn(args.car, args.language, args.group)
    else:
        results = replay(load_models(args.models), args.recordings)
    return 0 if all_understood(results) else 1


if __name__ == '__main__':
    sys.exit(main())
