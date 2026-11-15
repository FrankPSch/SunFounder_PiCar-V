'''How Claude voice, the voice builder and the chat work together (CLAUDE.md): the integration lock, the measured car
facts, wishes handed to the chat, a deploy on top of a newer master, Claude voice's instructions and tools.
Git, Claude Code and the car are replaced. Voice environment; run by tools/run_tests.py.'''
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault('CLAUDE_VOICE_MODEL', 'echo')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools'))
import car_facts  # noqa: E402
import claude_chat  # noqa: E402
import feature_builder  # noqa: E402
import integration_lock  # noqa: E402
import wish_list  # noqa: E402

FACTS_OUTPUT = '''==== arch
armv7l
==== os
Raspbian GNU/Linux 11 (bullseye)
==== python
Python 3.9.2
==== apt
espeak-ng
alsa-utils
==== pip
vosk
==== sound
USB
==== models
vosk-model-small-de-0.15
==== voice_cache
de 194
en 203
==== disk_free
51G
==== commit
440b884 deploy.sh waits
'''


class TempFolderTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='collaboration_')
        for patch in (mock.patch.object(integration_lock, 'LOCK_FILE', os.path.join(self.folder, 'integration.lock')),
                      mock.patch.object(integration_lock, 'POLL_S', 0.01),
                      mock.patch.object(car_facts, 'FACTS_FILE', os.path.join(self.folder, 'car_facts.json'))):
            patch.start()
            self.addCleanup(patch.stop)


class LockTest(TempFolderTest):
    def test_one_writer_at_a_time(self):
        with integration_lock.held('chat'):
            self.assertEqual(integration_lock.holder()['owner'], 'chat')
            with self.assertRaises(integration_lock.Busy) as busy:
                with integration_lock.held('voice build'):
                    pass
            self.assertIn('chat', str(busy.exception))
        self.assertIsNone(integration_lock.holder())

    def test_waiting_is_told_once_then_gives_up(self):
        told = []
        with integration_lock.held('chat'):
            with self.assertRaises(integration_lock.Busy):
                with integration_lock.held('voice build', wait_s=0.05, waiting=told.append):
                    pass
        self.assertEqual([holder['owner'] for holder in told], ['chat'])

    def test_a_lock_of_a_finished_process_is_taken_over(self):
        finished = subprocess.Popen([sys.executable, '-c', 'pass'])
        finished.wait()
        with open(integration_lock.LOCK_FILE, 'w', encoding='utf-8') as lock:
            json.dump({'owner': 'crashed chat', 'pid': finished.pid, 'since': 0}, lock)
        with integration_lock.held('voice build'):
            self.assertEqual(integration_lock.holder()['owner'], 'voice build')


class CarFactsTest(TempFolderTest):
    def test_measured_and_summarised(self):
        with mock.patch.object(car_facts.subprocess, 'run', lambda *args, **kwargs: mock.Mock(returncode=0, stdout=FACTS_OUTPUT)):
            facts = car_facts.measure(lambda text: None)
        self.assertEqual((facts['arch'], facts['apt'], facts['voice_cache']), ('armv7l', ['espeak-ng', 'alsa-utils'], ['de 194', 'en 203']))
        summary = car_facts.summary(with_packages=True)
        for expected in ('armv7l', 'espeak-ng yes', 'mbrola NO', 'vosk yes', 'apt packages: espeak-ng alsa-utils', 'not feasible'):
            self.assertIn(expected, summary)

    def test_car_off_keeps_the_last_measurement(self):
        with mock.patch.object(car_facts.subprocess, 'run', lambda *args, **kwargs: mock.Mock(returncode=255, stdout='')):
            self.assertIsNone(car_facts.measure(lambda text: None))
        self.assertIn('not measured yet', car_facts.summary())


class BuilderTest(TempFolderTest):
    def setUp(self):
        super().setUp()
        self.said, self.wishes = [], wish_list.WishList(self.folder)
        self.builder = feature_builder.Builder(lambda language, text: self.said.append(text), self.wishes)
        for patch in (mock.patch.object(feature_builder, 'BUILDS', self.folder),
                      mock.patch.object(feature_builder, '_git', lambda *args, **kwargs: 'abc1234')):
            patch.start()
            self.addCleanup(patch.stop)
        self.builder.propose('Bessere Stimme', 'Piper auf dem Pi', ['Piper installieren'], 'de')
        self.builder.request['option'] = 'Piper installieren'

    def test_not_feasible_goes_to_the_chat(self):
        with mock.patch.object(self.builder, '_run_builder', lambda prompt, session: ('s1', 'NOT FEASIBLE: Piper ist auf dem Auto nicht installiert.')):
            self.builder._build()
        self.assertEqual((self.builder.state, self.builder.detail), ('for_chat', 'Piper ist auf dem Auto nicht installiert.'))
        self.assertEqual(self.said, ['Den Wunsch Bessere Stimme gebe ich an Claude im Chat weiter: Piper ist auf dem Auto nicht installiert.'])
        self.assertEqual(list(self.wishes.wishes().values())[0]['state'], 'for_chat')

    def test_the_prompt_carries_the_car_facts(self):
        prompts = []
        with mock.patch.object(car_facts, 'summary', lambda with_packages=False: 'CAR FACTS: armv7l, mbrola NO'), \
                mock.patch.object(self.builder, '_run_builder', lambda prompt, session: prompts.append(prompt) or ('s1', 'NOT FEASIBLE: x')):
            self.builder._build()
        self.assertIn('CAR FACTS: armv7l, mbrola NO', prompts[0])
        self.assertIn("NOT FEASIBLE: <one sentence why, in the owner's language (de)>", prompts[0])

    def test_a_build_waits_for_the_chat_and_says_so(self):
        with integration_lock.held('chat'), mock.patch.object(feature_builder, 'LOCK_WAIT_S', 0.05):
            self.builder._build()
        self.assertEqual(self.builder.state, 'failed')
        self.assertEqual(self.said[0], 'Der Chat ändert gerade den Code. Ich warte, dann geht es mit Bessere Stimme weiter.')

    def test_a_deploy_conflicting_with_the_chat_goes_to_the_chat(self):
        self.builder.state, self.builder.request['worktree'], self.builder.request['branch'] = 'built', self.folder, 'voice/x'
        self.builder.request['log'] = os.path.join(self.folder, 'build.log')
        commands = []

        def run(command, cwd=None, **kwargs):
            commands.append(command[1:3])
            if command[1] == 'merge-base':
                return 1, ''          # master moved since the build
            if command[1:3] == ['rebase', 'origin/master']:
                return 1, 'CONFLICT (content): Merge conflict in remote_control/remote_control/vocabulary.py'
            return 0, ''
        with mock.patch.object(feature_builder, '_run', run):
            self.builder._deploy()
        self.assertEqual(self.builder.state, 'for_chat')
        self.assertIn('vocabulary.py', self.builder.detail)
        self.assertIn(['rebase', '--abort'], commands)
        self.assertNotIn(['push', '-q'], commands)

    def test_hand_to_chat_by_claude_voice(self):
        answer = self.builder.hand_to_chat('Piper auf dem Pi', 'Natürliche Stimme', 'braucht ein Paket', 'de')
        self.assertIn('Written down for the chat', answer)
        states = [(wish['title'], wish['state']) for wish in self.wishes.wishes().values()]
        self.assertEqual(states, [('Bessere Stimme', 'dropped'), ('Piper auf dem Pi', 'for_chat')])


class ClaudeVoiceTest(unittest.TestCase):
    def test_instructions_come_from_claude_md(self):
        self.assertIn('hand_to_chat', claude_chat.project_instructions())
        self.assertIn('Project rules (from CLAUDE.md)', claude_chat.INSTRUCTIONS)

    def test_new_tools_are_checked(self):
        self.assertTrue(claude_chat.valid_tool_input('car_facts', {}))
        self.assertTrue(claude_chat.valid_tool_input('hand_to_chat', {'title': 'x', 'understanding': 'y', 'reason': 'z'}))
        self.assertFalse(claude_chat.valid_tool_input('hand_to_chat', {'title': 'x'}))


if __name__ == '__main__':
    unittest.main()
