'''What is really on the car, measured over SSH (never assumed): processor, OS, Python, installed apt and pip packages,
sound devices, models, voice recordings, free disk, running commit. Saved to <voice dir>/car_facts.json; the voice
builder gets summary() in its prompt and Claude voice reads it, so a wish that needs missing software goes to the chat.
Measured at every voice server start and after every deploy.  Usage: python tools/car_facts.py'''
import json
import os
import subprocess
import time

import car_ssh
import car_tunnel
import pc_settings

FACTS_FILE = pc_settings.voice_path('generated', 'car_facts.json')
SSH_TIMEOUT_S = 60
SECTIONS = ('arch', 'os', 'python', 'apt', 'pip', 'sound', 'models', 'voice_cache', 'disk_free', 'commit')
REMOTE_SCRIPT = '''
section() { echo "==== $1"; }
section arch; uname -m
section os; . /etc/os-release; echo "$PRETTY_NAME"
section python; python3 --version 2>&1
section apt; dpkg-query -W -f='${db:Status-Abbrev} ${Package}\\n' | awk '$1 == "ii" {print $2}'
section pip; pip3 list --format=freeze 2>/dev/null | cut -d= -f1
section sound; aplay -l 2>/dev/null | sed -n 's/^card [0-9]*: \\([A-Za-z0-9_]*\\) .*/\\1/p' | sort -u
section models; ls /home/pi/picar-data/models 2>/dev/null
section voice_cache; for language in de en; do echo "$language $(ls /home/pi/picar-data/voice_cache/$language 2>/dev/null | wc -l)"; done
section disk_free; df -h / | awk 'NR == 2 {print $4}'
section commit; git -C /home/pi/SunFounder_PiCar-V log -1 --format='%h %s'
'''
KEY_SOFTWARE = ('espeak-ng', 'mbrola', 'opencv-data', 'python3-opencv', 'ffmpeg', 'alsa-utils', 'python3-numpy', 'vosk', 'picamera', 'piper')


def measure(log=print):
    '''Measures and saves the facts. Returns them, or None when the car is not reachable (the last ones stay).'''
    # bytes, not text: Windows text mode would send CRLF line ends, which the car's shell cannot read
    if pc_settings.switched_on('car_ssh_module'):
        result = car_ssh.run_script(REMOTE_SCRIPT, SSH_TIMEOUT_S)
        result = subprocess.CompletedProcess([], result.code, result.output)
    else:
        result = subprocess.run(['ssh'] + car_tunnel.SSH_OPTIONS + [pc_settings.CAR, 'sh -s'], input=REMOTE_SCRIPT.encode('utf-8'), stdout=subprocess.PIPE,  # legacy ssh (PC switch car_ssh_module off)
                                stderr=subprocess.DEVNULL, timeout=SSH_TIMEOUT_S)
    output = result.stdout.decode('utf-8', 'replace') if isinstance(result.stdout, bytes) else result.stdout
    if result.returncode or '==== commit' not in output:
        log('car facts: car not reachable, keeping the last measurement')
        return None
    facts, section = {'measured': time.strftime('%Y-%m-%d %H:%M:%S')}, None
    for line in output.splitlines():
        if line.startswith('==== '):
            section = line[5:]
            facts[section] = []
        elif section and line.strip():
            facts[section].append(line.strip())
    facts.update((name, (facts.get(name) or [''])[0]) for name in ('arch', 'os', 'python', 'disk_free', 'commit'))
    with open(FACTS_FILE, 'w', encoding='utf-8') as saved:
        json.dump(facts, saved, indent=1)
    log('car facts: %s, %s, %d apt and %d pip packages' % (facts['arch'], facts['os'], len(facts['apt']), len(facts['pip'])))
    return facts


def load():
    try:
        with open(FACTS_FILE, encoding='utf-8') as saved:
            return json.load(saved)
    except (OSError, ValueError):
        return None


def installed(facts, package):
    return package in facts.get('apt', []) or package.lower() in (name.lower() for name in facts.get('pip', []))


def summary(facts=None, with_packages=False):
    '''A few lines for a prompt; with_packages adds every installed package name.'''
    facts = facts or load()
    if not facts:
        return 'CAR FACTS: not measured yet (car never reached). Assume nothing beyond what tools/install_extras.sh installs.'
    key = ', '.join('%s %s' % (name, 'yes' if installed(facts, name) else 'NO') for name in KEY_SOFTWARE)
    lines = ['CAR FACTS (measured %s on the car):' % facts['measured'],
             '- %s, %s, %s, %s free, running %s' % (facts['arch'], facts['os'], facts['python'], facts['disk_free'], facts['commit']),
             '- key software installed: ' + key,
             '- sound cards: %s; models: %s; voice recordings: %s' % (', '.join(facts['sound']), ', '.join(facts['models']), ', '.join(facts['voice_cache'])),
             '- nothing can be installed during a build or deploy: a wish that needs software marked NO or not listed is not feasible']
    if with_packages:
        lines += ['- apt packages: ' + ' '.join(facts['apt']), '- pip packages: ' + ' '.join(facts['pip'])]
    return '\n'.join(lines)


if __name__ == '__main__':
    print(summary(measure()))
