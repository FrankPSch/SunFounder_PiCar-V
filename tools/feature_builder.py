# -*- coding: utf-8 -*-
'''
Features requested by voice, for tools/voice_server.py. Three spoken steps, each gated by a word the owner must say:
  1. Claude repeats its understanding and the options (propose)       - nothing is built yet
  2. "go"     -> Claude Code builds it on a branch, then this checks and tests it (start)
  3. "deploy" -> master + release are pushed and tools/deploy.sh updates the car (deploy)
A new feature always arrives switched off; the owner turns it on by voice or in the dashboard.

The checks do not trust the builder: protected files unchanged, existing switches and stop words unchanged,
exactly one new switch with default off, both simulation test suites pass.
Builds run in their own clone (WORK_DIR\\repo, git worktrees under WORK_DIR\\builds), never in a checkout people edit.
'''
import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request

import pc_settings

WORK_DIR = os.environ.get('PICAR_WORK_DIR', pc_settings.VOICE_DIR)
REPO = os.path.join(WORK_DIR, 'repo')
BUILDS = os.path.join(WORK_DIR, 'builds')
SIM_PYTHON = os.path.join(WORK_DIR, 'simenv', 'Scripts', 'python.exe')
GIT_BASH = os.environ.get('GIT_BASH', r'C:\Program Files\Git\bin\bash.exe')
# Claude Code without a window. PICAR_BUILDER replaces it with another command (tests: a stand-in that edits files).
BUILDER = os.environ.get('PICAR_BUILDER') or 'claude'
BUILDER_MODEL = os.environ.get('PICAR_BUILDER_MODEL', 'opus')
BUILD_TIMEOUT_S = 40 * 60
FIX_ROUNDS = 2              # test failures go back to the builder this often
SIM_TEST_TRIES = 3          # some checks depend on timing (ball tracking under load): one clean run of three counts
SIM_PORT = 8790
COMMIT_AUTHOR = ['-c', 'user.name=FrankPSch', '-c', 'user.email=frank.schikarski@googlemail.com']
CO_AUTHOR = 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'

FEATURES_FILE = 'remote_control/remote_control/features.py'
COMMANDS_FILE = 'remote_control/remote_control/commands.py'
PROTECTED = ('tools/deploy.sh', 'tools/picar-update.sh', 'tools/systemd/', 'tools/voice_server.py', 'tools/voice_server.bat',
             'tools/feature_builder.py', 'tools/voice_launcher.py', 'tools/car_tunnel.py', 'tools/install_voice_launcher.bat',
             'remote_control/remote_control/voice_protocol.py')
GO = re.compile(r'\b(go+m?|goh|gou)\b', re.IGNORECASE)  # Whisper also writes a German "go" as "Goom"
DEPLOY = re.compile(r'd[ei]e?p?l(oy|oi|eu|ey)', re.IGNORECASE)
AWAITED_WORD = {'proposed': (GO, 'Go, Option eins.'), 'built': (DEPLOY, 'Deploy.')}  # state -> (pattern, Whisper priming)

BUILDER_PROMPT = '''You build one new feature for the SunFounder PiCar-V robot car in this repository
(Raspberry Pi 3 B+, Raspbian Bullseye, Python 3.9, Django 2.0; the car runs remote_control/ as a systemd service).
The owner asked for it by voice and has said go: build it now. Nobody can answer questions during the build.

Request: {title}
Understanding: {understanding}
Chosen option: {option}

Rules (a script checks them afterwards and rejects the build otherwise):
- Add exactly one new switch to FEATURES in {features_file}, default False, with a short English help text.
  While the switch is off the feature does nothing (check features.enabled(name) or use features.on_change).
- Do not change existing switches, the stop phrases (Spoken 'stop' in {commands_file}), or these files: {protected}.
- A new spoken command is one Spoken line (and if needed one Action) in {commands_file}; add its phrases with the
  expected effect to tests/test_commands.py. Every word must be in the Vosk vocabulary (German and English small models).
- The code must run on the car with the packages it already has (see tools/install_extras.sh and the imports in
  remote_control/remote_control/) and in the PC simulation (PICAR_SIM=1, remote_control/remote_control/sim/).
- Add checks for the new feature to tools/sim_test_features.py: switch it on and off through
  POST /api/cmd {{"cmd": "feature", "name": ..., "on": ...}} and check what it does in the simulation.
  The checks must not make the car drive.
- If the feature should show on the dashboard, use remote_control/static/dashboard.js and dashboard.css.
- Keep the change small and follow the style of the code around it. Do not commit; the build server tests and commits.
End your answer with one line: FEATURE <name of the new switch>
'''
FIX_PROMPT = '''The build server rejected the build. Fix it, same rules as before. Do not commit.
{problems}
End your answer with one line: FEATURE <name of the new switch>
'''
SAY = {
    'built': {'de': 'Das Feature {title} ist gebaut und getestet. Sag: hey Claude, deploy, dann installiere ich es. Der Schalter bleibt aus.',
              'en': 'The feature {title} is built and tested. Say: hey Claude, deploy, and I will install it. Its switch stays off.'},
    'failed': {'de': 'Das Feature {title} konnte ich nicht bauen.', 'en': 'I could not build the feature {title}.'},
    'deployed': {'de': 'Das Feature {title} ist installiert. Der Schalter {feature} ist aus.',
                 'en': 'The feature {title} is installed. The switch {feature} is off.'},
    'deploy_failed': {'de': 'Die Installation von {title} ist fehlgeschlagen. Das Auto läuft mit der alten Version.',
                      'en': 'Installing {title} failed. The car runs the previous version.'},
}


def _run(command, cwd=REPO, timeout=600, env=None, stdin_text=None):
    result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, env=env,
                            input=stdin_text.encode('utf-8') if stdin_text is not None else None)
    return result.returncode, result.stdout.decode('utf-8', 'replace')


def _git(*args, cwd=REPO):
    code, output = _run(['git'] + COMMIT_AUTHOR + list(args), cwd=cwd)
    if code:
        raise BuildError('git %s: %s' % (' '.join(args), output.strip()[-300:]))
    return output.strip()


class BuildError(Exception):
    pass


def _features_at(source):
    '''FEATURES of a features.py source as {name: default}.'''
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(getattr(target, 'id', None) == 'FEATURES' for target in node.targets):
            return {key.value: value.elts[0].value for key, value in zip(node.value.keys, node.value.values)}
    raise BuildError('FEATURES not found in ' + FEATURES_FILE)


def _stop_phrases_at(source):
    '''The German and English phrases of Spoken('stop', ...) in a commands.py source.'''
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'Spoken' and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'stop'):
            return [ast.literal_eval(argument) for argument in node.args[4:6]]
    raise BuildError("Spoken('stop', ...) not found in " + COMMANDS_FILE)


def check_build(worktree, base):
    '''Problems as text lines (empty = accepted) and the name of the new switch.'''
    problems = []
    changed = _git('diff', '--name-only', base, cwd=worktree).split() + _git('ls-files', '--others', '--exclude-standard', cwd=worktree).split()
    problems += ['protected file changed: ' + name for name in changed if name.startswith(PROTECTED)]
    old = _features_at(_git('show', '%s:%s' % (base, FEATURES_FILE), cwd=worktree))
    with open(os.path.join(worktree, FEATURES_FILE), encoding='utf-8') as source:
        new = _features_at(source.read())
    problems += ['existing switch changed or removed: ' + name for name in old if new.get(name) != old[name]]
    added = [name for name in new if name not in old]
    if len(added) != 1:
        problems.append('expected exactly one new switch in FEATURES, found %d: %s' % (len(added), added))
    problems += ['new switch %s must default to False' % name for name in added if new[name] is not False]
    with open(os.path.join(worktree, COMMANDS_FILE), encoding='utf-8') as source:
        if _stop_phrases_at(source.read()) != _stop_phrases_at(_git('show', '%s:%s' % (base, COMMANDS_FILE), cwd=worktree)):
            problems.append('stop phrases changed')
    if added and not any(_uses(worktree, name, added[0]) for name in changed if name.endswith(('.py', '.js')) and name != FEATURES_FILE):
        problems.append('the new switch %s is not used anywhere outside features.py' % added[0])
    return problems, (added[0] if len(added) == 1 else None)


def _uses(worktree, name, feature):
    path = os.path.join(worktree, name)
    if not os.path.exists(path):
        return False
    with open(path, encoding='utf-8', errors='replace') as source:
        return feature in source.read()


def run_sim_tests(worktree):
    '''Both simulation suites against a fresh simulated car. Returns (passed, last lines of the output).'''
    data_dir = tempfile.mkdtemp(prefix='simdata_', dir=WORK_DIR)
    env = dict(os.environ, PICAR_SIM='1', PICAR_DATA=data_dir, PYTHONIOENCODING='utf-8',
               PATH=os.pathsep.join((r'C:\ProgramData\Anaconda3\Library\bin', os.environ['PATH'])))
    server_log = open(os.path.join(data_dir, 'server.log'), 'wb')  # a pipe nobody reads would fill up and stall the server
    server = subprocess.Popen([SIM_PYTHON, 'manage.py', 'runserver', '--noreload', '127.0.0.1:%d' % SIM_PORT],
                              cwd=os.path.join(worktree, 'remote_control'), env=env, stdout=server_log, stderr=subprocess.STDOUT)
    try:
        base_url = 'http://127.0.0.1:%d' % SIM_PORT
        for _ in range(60):
            try:
                urllib.request.urlopen(base_url + '/api/state', timeout=2).read()
                break
            except OSError:
                if server.poll() is not None:
                    server_log.close()
                    with open(server_log.name, encoding='utf-8', errors='replace') as log:
                        return False, 'simulated car did not start:\n' + log.read()[-2000:]
                time.sleep(0.5)
        report = []
        for test in ('tools/sim_test.py', 'tools/sim_test_features.py'):
            code, output = _run([SIM_PYTHON, test, base_url], cwd=worktree, timeout=300, env=env)
            if code == 0 and 'ALL PASS' in output:
                report.append(test + ': ALL PASS')
                continue
            failed_lines = [line for line in output.splitlines() if 'FAIL' in line or 'Error' in line or 'Traceback' in line]
            report.append('%s: FAILED\n%s' % (test, '\n'.join(failed_lines[-15:]) or output[-1500:]))
            return False, '\n'.join(report)
        return True, '\n'.join(report)
    finally:
        # The venv's python.exe is a launcher: killing it alone leaves the real server holding the port
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(server.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        server_log.close()
        shutil.rmtree(data_dir, ignore_errors=True)  # a temporary folder this function made


def sim_tests_pass(worktree, log):
    '''run_sim_tests up to SIM_TEST_TRIES times; one clean run counts. log(report) for every run. Returns (passed, last report).'''
    for _ in range(SIM_TEST_TRIES):
        passed, report = run_sim_tests(worktree)
        log(report)
        if passed:
            break
    return passed, report


class Builder(object):
    def __init__(self, announce):
        self._announce = announce  # announce(language, text): spoken by the car at its next poll
        self._lock = threading.Lock()
        self.state = 'idle'
        self.request = None        # dict: title, understanding, options, language, option, feature, branch, worktree, log
        self.detail = ''

    def describe(self):
        if self.request is None:
            return 'no feature request'
        text = 'feature request "%s": %s' % (self.request['title'], self.state)
        if self.state == 'proposed':
            text += ' (options: %s; waiting for "go")' % '; '.join(self.request['options'])
        if self.state == 'built':
            text += ' (switch %s, waiting for "deploy")' % self.request['feature']
        return text + (' - ' + self.detail if self.detail else '')

    def propose(self, title, understanding, options, language):
        with self._lock:
            if self.state in ('building', 'deploying'):
                return 'Not saved: "%s" is %s right now.' % (self.request['title'], self.state)
            self.request = {'title': title, 'understanding': understanding, 'options': options, 'language': language}
            self.state, self.detail = 'proposed', ''
        return 'Saved. Now read your understanding and the numbered options to the owner and ask them to say "go" with the option.'

    def start(self, option, owner_words):
        with self._lock:
            if self.state != 'proposed':
                return 'Not started: there is no proposal waiting (state: %s).' % self.state
            if not GO.search(owner_words):
                return 'Not started: the owner has not said "go". Ask them to say "go".'
            self.request['option'] = option
            self.state, self.detail = 'building', 'Claude Code is working on it, this takes a few minutes'
        threading.Thread(target=self._build, daemon=True).start()
        return 'Build started. Tell the owner it takes a few minutes and that the car will say when it is ready.'

    def deploy(self, owner_words):
        with self._lock:
            if self.state != 'built':
                return 'Nothing to deploy (state: %s).' % self.state
            if not DEPLOY.search(owner_words):
                return 'Not deployed: the owner has not said "deploy".'
            self.state, self.detail = 'deploying', 'the car restarts in about a minute'
        threading.Thread(target=self._deploy, daemon=True).start()
        return 'Deploy started. Tell the owner the car restarts and will say when the update is installed.'

    # ---- background work ----
    def _say(self, key, **extra):
        language = self.request['language']
        self._announce(language, SAY[key][language].format(title=self.request['title'], feature=(self.request.get('feature') or '').replace('_', ' '), **extra))

    def _log(self, text):
        with open(self.request['log'], 'a', encoding='utf-8') as log:
            log.write('%s %s\n' % (time.strftime('%H:%M:%S'), text))
        print('  build:', text.splitlines()[0][:200] if text else '', flush=True)

    def _build(self):
        request = self.request
        slug = re.sub(r'[^a-z0-9]+', '-', request['title'].lower()).strip('-')[:40] or 'feature'
        request['branch'] = 'voice/%s-%s' % (time.strftime('%Y%m%d-%H%M%S'), slug)
        request['worktree'] = os.path.join(BUILDS, request['branch'].split('/')[1])
        os.makedirs(BUILDS, exist_ok=True)
        request['log'] = request['worktree'] + '.log'
        try:
            _git('fetch', '-q', 'origin')
            _git('worktree', 'add', '-q', '-b', request['branch'], request['worktree'], 'origin/master')
            base = _git('rev-parse', 'origin/master')
            self._log('build %s from %s' % (request['branch'], base[:7]))
            session = None
            prompt = BUILDER_PROMPT.format(title=request['title'], understanding=request['understanding'], option=request['option'],
                                           features_file=FEATURES_FILE, commands_file=COMMANDS_FILE, protected=', '.join(PROTECTED))
            for round_number in range(FIX_ROUNDS + 1):
                session = self._run_builder(prompt, session)
                problems, feature = check_build(request['worktree'], base)
                if not problems:
                    passed, report = sim_tests_pass(request['worktree'], self._log)
                    if passed:
                        break
                    problems = ['simulation tests failed:', report]
                self._log('round %d rejected:\n%s' % (round_number + 1, '\n'.join(problems)))
                prompt = FIX_PROMPT.format(problems='\n'.join(problems))
            else:
                raise BuildError('still rejected after %d fix rounds: %s' % (FIX_ROUNDS, problems[0]))
            request['feature'] = feature
            _git('add', '-A', cwd=request['worktree'])
            _git('commit', '-q', '-m', 'Voice feature %s: %s\n\n%s\nOption: %s\nNew switch %s, off by default. Built by Claude Code from a spoken request.\n\n%s'
                 % (feature, request['title'], request['understanding'], request['option'], feature, CO_AUTHOR), cwd=request['worktree'])
            self.state, self.detail = 'built', 'commit %s on %s' % (_git('rev-parse', '--short', 'HEAD', cwd=request['worktree']), request['branch'])
            self._log(self.detail)
            self._say('built')
        except (BuildError, OSError, subprocess.SubprocessError, ValueError, SyntaxError) as error:
            self.state, self.detail = 'failed', str(error)[:300]
            self._log('FAILED: %s' % error)
            self._say('failed')

    def _run_builder(self, prompt, session):
        # The prompt goes in on stdin: a multi-line argument does not survive a .cmd/.bat wrapper on Windows
        command = [BUILDER, '-p', '--output-format', 'json', '--model', BUILDER_MODEL, '--permission-mode', 'acceptEdits',
                   '--allowedTools', 'Read,Edit,Write,Glob,Grep', '--disallowedTools', 'Bash,WebFetch,WebSearch,Agent']
        if session:
            command += ['--resume', session]
        code, output = _run(command, cwd=self.request['worktree'], timeout=BUILD_TIMEOUT_S, stdin_text=prompt)
        self._log('builder exit %d: %s' % (code, output[-3000:]))
        try:
            result = json.loads(output[output.index('{'):])
        except ValueError:
            raise BuildError('builder gave no result (exit %d): %s' % (code, output.strip()[-200:]))
        if result.get('is_error'):
            raise BuildError('builder failed: %s' % str(result.get('result'))[:200])
        return result.get('session_id')

    def _deploy(self):
        request = self.request
        try:
            _git('fetch', '-q', 'origin')
            if _run(['git', 'merge-base', '--is-ancestor', 'origin/master', 'HEAD'], cwd=request['worktree'])[0]:
                raise BuildError('master changed since the build; build it again')
            _git('push', '-q', 'origin', 'HEAD:master', 'HEAD:release', cwd=request['worktree'])
            code, output = _run([GIT_BASH, 'tools/deploy.sh', 'release'], cwd=request['worktree'], timeout=600)
            self._log('deploy exit %d: %s' % (code, output[-2000:]))
            if code or 'DEPLOY OK' not in output:
                raise BuildError(output.strip().splitlines()[-1] if output.strip() else 'deploy.sh exit %d' % code)
            self.state, self.detail = 'deployed', 'car runs %s' % request['branch']
            self._say('deployed')
        except (BuildError, OSError, subprocess.SubprocessError) as error:
            self.state, self.detail = 'deploy_failed', str(error)[:300]
            self._log('DEPLOY FAILED: %s' % error)
            self._say('deploy_failed')
