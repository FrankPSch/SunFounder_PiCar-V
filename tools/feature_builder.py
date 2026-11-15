# -*- coding: utf-8 -*-
'''
Features requested by voice, for tools/voice_server.py. Three spoken steps, each gated by a word the owner must say:
  1. Claude repeats its understanding and the options (propose)       - nothing is built yet
  2. "go"     -> Claude Code builds it on a branch, then this checks and tests it (start)
  3. "deploy" -> master + release are pushed and tools/deploy.sh updates the car (deploy)
A new feature always arrives switched off; the owner turns it on by voice or in the dashboard.
Every step of every wish goes to the wish list (wish_list.py), so a chat on the PC can follow them.
Build and deploy hold integration_lock: the chat does not change master meanwhile. The builder gets the measured
car facts (car_facts.py); a wish that needs software the car lacks ends as "for_chat" instead of a build.

The checks (build_checks.py) do not trust the builder; the simulation tests run on the twin (sim_runner.py).
Builds run in their own clone (WORK_DIR\\repo, git worktrees under WORK_DIR\\builds), never in a checkout people edit.
'''
import json
import os
import re
import subprocess
import threading
import time

import build_checks
import car_facts
import integration_lock
import pc_settings
import release
import requirements_report
import sim_runner
import wish_list

WORK_DIR = sim_runner.WORK_DIR
REPO = os.path.join(WORK_DIR, 'repo')
BUILDS = os.path.join(WORK_DIR, 'builds')
SIM_PYTHON, SIM_PORT = sim_runner.SIM_PYTHON, sim_runner.SIM_PORT
run_sim_tests, sim_tests_pass = sim_runner.run_sim_tests, sim_runner.sim_tests_pass
GIT_BASH = os.environ.get('GIT_BASH', r'C:\Program Files\Git\bin\bash.exe')
# Claude Code without a window. PICAR_BUILDER replaces it with another command (tests: a stand-in that edits files).
BUILDER = os.environ.get('PICAR_BUILDER') or 'claude'
BUILDER_MODEL = os.environ.get('PICAR_BUILDER_MODEL', 'opus')
BUILD_TIMEOUT_S = 40 * 60
FIX_ROUNDS = 2              # test failures go back to the builder this often
LOCK_WAIT_S = 30 * 60       # a chat push takes a few minutes; longer means something hangs
NOT_FEASIBLE = re.compile(r'^NOT FEASIBLE:\s*(.+)$', re.MULTILINE)
COMMIT_AUTHOR = ['-c', 'user.name=FrankPSch', '-c', 'user.email=frank.schikarski@googlemail.com']
CO_AUTHOR = 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'

FEATURES_FILE, COMMANDS_FILE, VOCABULARY_FILE = build_checks.FEATURES_FILE, build_checks.COMMANDS_FILE, build_checks.VOCABULARY_FILE
PROTECTED, BuildError = build_checks.PROTECTED, build_checks.BuildError
phrase_meanings_at, changed_meanings = build_checks.phrase_meanings_at, build_checks.changed_meanings
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
- Do not change existing switches, the stop phrases (Spoken 'stop' in {vocabulary_file}), or these files: {protected}.
- A new spoken command is one Spoken line and one EXPLANATIONS entry in {vocabulary_file} (and if needed one Action
  in {commands_file}); add its phrases with the
  expected effect to tests/test_commands.py. Every word must be in the Vosk vocabulary (German and English small models).
- The code must run on the car with the packages it already has (see tools/install_extras.sh and the imports in
  remote_control/remote_control/) and in the PC simulation (PICAR_SIM=1, remote_control/remote_control/sim/).
- Add a test for the new feature to tests/sim/test_sim_features.py (a method of class Features, numbered before
  test_99, decorated @shows(('K.switch.<name>', 'on'), ('K.switch.<name>', 'off'))): switch it on and off with
  self.cmd('feature', name=..., on=...) and check what it does in the simulation; wait with self.wait_until(...), never
  time.sleep. The test must not make the car drive.
- If the feature should show on the dashboard, use remote_control/static/dashboard.js and dashboard.css.
- Keep the change small and follow the style of the code around it. Do not commit; the build server tests and commits.
- Nothing can be installed on the car. If the chosen option needs software that CAR FACTS below does not show as
  installed, hardware the car lacks, or changes outside remote_control/ and tests/sim/test_sim_features.py, do not edit
  anything and end your answer with one line: NOT FEASIBLE: <one sentence why, in the owner's language ({language})>
End your answer with one line: FEATURE <name of the new switch>

{car_facts}
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
    'waiting': {'de': 'Der Chat ändert gerade den Code. Ich warte, dann geht es mit {title} weiter.',
                'en': 'The chat is changing the code right now. I wait, then {title} goes on.'},
    'for_chat': {'de': 'Den Wunsch {title} gebe ich an Claude im Chat weiter: {reason}',
                 'en': 'I hand the wish {title} to Claude in the chat: {reason}'},
}


def _run(command, cwd=REPO, timeout=600, env=None, stdin_text=None):
    return sim_runner.run_command(command, cwd, timeout, env, stdin_text)


def _git(*args, cwd=REPO):
    code, output = _run(['git'] + COMMIT_AUTHOR + list(args), cwd=cwd)
    if code:
        raise BuildError('git %s: %s' % (' '.join(args), output.strip()[-300:]))
    return output.strip()


class NotFeasible(Exception):
    '''The wish needs what a voice build cannot do: the chat takes it over.'''


class Builder(object):
    def __init__(self, announce, wishes=None):
        self._announce = announce  # announce(language, text): spoken by the car at its next poll
        self._wishes = wishes or wish_list.WishList(pc_settings.WISHES_DIR, requirements_report.rebuild_if_possible)
        self._lock = threading.Lock()
        self.state = 'idle'
        self.request = None        # dict: id, title, understanding, options, language, option, feature, branch, worktree, log
        self.detail = ''

    def _set_state(self, state, detail='', **fields):
        '''The new state, written to the wish list first (with what became known, wish_list.STEP_FIELDS): whoever
        sees the new state finds it in the list too.'''
        self._wishes.record(self.request['id'], state, detail=detail, **fields)
        self.state, self.detail = state, detail

    def describe(self):
        if self.request is None:
            return 'no feature request'
        text = 'feature request "%s": %s' % (self.request['title'], self.state)
        if self.state == 'proposed':
            text += ' (options: %s; waiting for "go")' % '; '.join(self.request['options'])
        if self.state == 'built':
            text += ' (switch %s, waiting for "deploy"; read this deploy summary to the owner first: %s%s)' % (
                self.request['feature'], self.request.get('summary', ''),
                '; THESE PHRASES CHANGE MEANING, ask the owner to confirm them: ' + '; '.join(self.request['redefined']) if self.request.get('redefined') else '')
        return text + (' - ' + self.detail if self.detail else '')

    def propose(self, title, understanding, options, language):
        with self._lock:
            if self.state in ('building', 'deploying'):
                return 'Not saved: "%s" is %s right now.' % (self.request['title'], self.state)
            if self.state == 'proposed':
                self._set_state('dropped', 'replaced by "%s" before "go"' % title)
            self.request = {'id': wish_list.new_id(), 'title': title, 'understanding': understanding, 'options': options, 'language': language}
            self._set_state('proposed', title=title, understanding=understanding, options=options, language=language)
        return 'Saved. Now read your understanding and the numbered options to the owner and ask them to say "go" with the option.'

    def hand_to_chat(self, title, understanding, reason, language):
        '''A wish the voice build cannot do (software, hardware, PC side): written down for the chat, nothing is built.'''
        with self._lock:
            if self.state in ('building', 'deploying'):
                return 'Not saved: "%s" is %s right now.' % (self.request['title'], self.state)
            if self.state == 'proposed':
                self._set_state('dropped', 'replaced by "%s" (for the chat) before "go"' % title)
            self.request = {'id': wish_list.new_id(), 'title': title, 'understanding': understanding, 'options': [], 'language': language}
            self._set_state('for_chat', reason, title=title, understanding=understanding, options=[], language=language)
        return 'Written down for the chat. Tell the owner that Claude in the chat on the PC takes it over and why.'

    def start(self, option, owner_words):
        with self._lock:
            if self.state != 'proposed':
                return 'Not started: there is no proposal waiting (state: %s).' % self.state
            if not GO.search(owner_words):
                return 'Not started: the owner has not said "go". Ask them to say "go".'
            self.request['option'] = option
            self._set_state('building', 'Claude Code is working on it, this takes a few minutes', option=option)
        threading.Thread(target=self._build, daemon=True).start()
        return 'Build started. Tell the owner it takes a few minutes and that the car will say when it is ready.'

    def deploy(self, owner_words, phrase_changes_confirmed=False):
        with self._lock:
            if self.state != 'built':
                return 'Nothing to deploy (state: %s).' % self.state
            if not DEPLOY.search(owner_words):
                return 'Not deployed: the owner has not said "deploy".'
            if self.request.get('redefined') and not phrase_changes_confirmed:
                return 'Not deployed: these phrases change meaning: %s. Read them to the owner and deploy only after a clear yes.' % '; '.join(self.request['redefined'])
            self._set_state('deploying', 'the car restarts in about a minute')
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
        self._locked('voice build', self._build_locked, 'failed')

    def _deploy(self):
        self._locked('voice deploy', self._deploy_locked, 'deploy_failed')

    def _locked(self, purpose, work, failed_state):
        '''work() while holding the integration lock; waits for a running chat push and says so.'''
        try:
            with integration_lock.held('%s: %s' % (purpose, self.request['title']), LOCK_WAIT_S, lambda holder: self._say('waiting')):
                work()
        except integration_lock.Busy as error:
            self._set_state(failed_state, str(error))
            self._say(failed_state)

    def _build_locked(self):
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
            self._set_state('building', 'Claude Code is working on it, from %s' % base[:7], branch=request['branch'])
            session = None
            prompt = BUILDER_PROMPT.format(title=request['title'], understanding=request['understanding'], option=request['option'],
                                           features_file=FEATURES_FILE, commands_file=COMMANDS_FILE, vocabulary_file=VOCABULARY_FILE, protected=', '.join(PROTECTED),
                                           language=request['language'], car_facts=car_facts.summary(with_packages=True))
            for round_number in range(FIX_ROUNDS + 1):
                session, answer = self._run_builder(prompt, session)
                not_feasible = NOT_FEASIBLE.search(answer)
                if not_feasible:
                    raise NotFeasible(not_feasible.group(1).strip())
                problems, feature = build_checks.check_build(request['worktree'], base, _git)
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
            request['summary'], request['redefined'] = build_checks.deploy_summary(request['worktree'], base, _git)
            self._set_state('built', 'commit %s on %s' % (_git('rev-parse', '--short', 'HEAD', cwd=request['worktree']), request['branch']), feature=feature)
            self._log(self.detail)
            self._say('built')
        except NotFeasible as reason:
            self._set_state('for_chat', str(reason)[:300])
            self._log('NOT FEASIBLE: %s' % reason)
            self._say('for_chat', reason=reason)
        except (BuildError, integration_lock.Busy, OSError, subprocess.SubprocessError, ValueError, SyntaxError) as error:
            self._set_state('failed', str(error)[:300])
            self._log('FAILED: %s' % error)
            self._say('failed')

    def _rebase_on_master(self):
        '''The chat changed master since the build: the build goes on top and is tested again; a conflict goes to the chat.'''
        worktree = self.request['worktree']
        code, output = _run(['git', 'rebase', 'origin/master'], cwd=worktree)
        if code:
            _run(['git', 'rebase', '--abort'], cwd=worktree)
            raise NotFeasible('conflicts with a change from the chat (%s)' % (output.strip().splitlines() or ['git rebase failed'])[-1][:150])
        passed, report = sim_tests_pass(worktree, self._log)
        if not passed:
            raise BuildError('tests fail on top of the newer master: ' + report[-200:])
        self._log('rebased on origin/master ' + _git('rev-parse', '--short', 'origin/master'))

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
        return result.get('session_id'), str(result.get('result') or '')

    def _deploy_locked(self):
        request = self.request
        try:
            _git('fetch', '-q', 'origin')
            if _run(['git', 'merge-base', '--is-ancestor', 'origin/master', 'HEAD'], cwd=request['worktree'])[0]:
                self._rebase_on_master()
            _git('push', '-q', 'origin', 'HEAD:master', 'HEAD:release', cwd=request['worktree'])
            code, output = _run([GIT_BASH, 'tools/deploy.sh', 'release'], cwd=request['worktree'], timeout=600)
            self._log('deploy exit %d: %s' % (code, output[-2000:]))
            if code or 'DEPLOY OK' not in output:
                raise BuildError(output.strip().splitlines()[-1] if output.strip() else 'deploy.sh exit %d' % code)
            commit = _git('rev-parse', 'HEAD', cwd=request['worktree'])
            tag = release.tag('voice', cwd=request['worktree'])
            passed, report = release.after_deploy('voice', commit, self._log)
            self._set_state('deployed', 'car runs %s (%s); check on the car: %s' % (request['branch'], tag, 'ok' if passed else '; '.join(
                line for line in report if not line.startswith('ok'))[:200]))
            self._say('deployed')
        except NotFeasible as reason:
            self._set_state('for_chat', str(reason)[:300])
            self._log('DEPLOY HANDED TO THE CHAT: %s' % reason)
            self._say('for_chat', reason=reason)
        except (BuildError, integration_lock.Busy, OSError, subprocess.SubprocessError) as error:
            self._set_state('deploy_failed', str(error)[:300])
            self._log('DEPLOY FAILED: %s' % error)
            self._say('deploy_failed')
