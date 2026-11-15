'''The wish list (tools/wish_list.py) through the feature builder's steps: proposed, dropped, building, built, deployed,
failed. Claude Code and the deploy are replaced. Voice environment; run by tools/run_tests.py.'''
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools'))
import feature_builder  # noqa: E402
import wish_list  # noqa: E402
from remote_control import requirements  # noqa: E402

OPTIONS = ['Nach Stille', 'Mit Schlusswort', 'Beides']
STEP_TIMEOUT_S = 5


class WishListTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='wishes_')
        self.wishes = wish_list.WishList(self.folder)
        self.builder = feature_builder.Builder(lambda language, text: None, self.wishes)
        self.ids = iter(['20261115-010000', '20261115-010100'])
        patch = mock.patch.object(wish_list, 'new_id', lambda: next(self.ids))
        patch.start()
        self.addCleanup(patch.stop)

    def wait_for(self, state):
        deadline = time.time() + STEP_TIMEOUT_S
        while self.builder.state != state and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.builder.state, state)

    def build_and_deploy(self, build_result):
        def build(builder):
            builder._set_state('building', 'from 811819d', branch='voice/20261115-pause')
            state, detail, fields = build_result
            builder._set_state(state, detail, **fields)
        with mock.patch.object(feature_builder.Builder, '_build', build), \
                mock.patch.object(feature_builder.Builder, '_deploy', lambda builder: builder._set_state('deployed', 'car runs voice/20261115-pause')):
            self.builder.start('Beides', 'Go, drei')
            self.wait_for(build_result[0])
            if build_result[0] == 'built':
                self.builder.deploy('Deploy.')
                self.wait_for('deployed')

    @requirements.shows(('C.wish.flow', 'proposed'), ('C.wish.flow', 'built'), ('C.wish.flow', 'deployed'), ('C.wish.list', 'proposed'), ('C.wish.list', 'dropped'), ('C.wish.list', 'deployed'))
    def test_a_wish_from_proposal_to_the_car(self):
        self.builder.propose('Hupen', 'Hupt beim Losfahren', ['Kurz', 'Lang'], 'de')
        self.builder.propose('Sprachbefehle im Dialog pausieren', 'Im Gespräch keine Befehle', OPTIONS, 'de')
        self.build_and_deploy(('built', 'commit abc1234 on voice/20261115-pause', {'feature': 'pause_commands'}))
        wishes = self.wishes.wishes()
        self.assertEqual([(wish['title'], wish['state']) for wish in wishes.values()],
                         [('Hupen', 'dropped'), ('Sprachbefehle im Dialog pausieren', 'deployed')])
        pause = wishes['20261115-010100']
        self.assertEqual([state for _, state in pause['history']], ['proposed', 'building', 'building', 'built', 'deploying', 'deployed'])
        self.assertEqual((pause['option'], pause['branch'], pause['feature']), ('Beides', 'voice/20261115-pause', 'pause_commands'))
        with open(os.path.join(self.folder, wish_list.MARKDOWN_FILE), encoding='utf-8') as markdown:
            text = markdown.read()
        self.assertLess(text.index('## Sprachbefehle im Dialog pausieren: deployed'), text.index('## Hupen: dropped'))
        for expected in ('- Option 3: Beides  **(chosen)**', '- Switch: pause_commands', '- Understood: Im Gespräch keine Befehle',
                         'Detail: replaced by "Sprachbefehle im Dialog pausieren" before "go"'):
            self.assertIn(expected, text)

    @requirements.shows(('C.wish.list', 'failed'))
    def test_a_failed_build_keeps_the_reason(self):
        self.builder.propose('Hupen', 'Hupt beim Losfahren', ['Kurz'], 'de')
        self.build_and_deploy(('failed', 'still rejected after 2 fix rounds: simulation tests failed:', {}))
        wish = self.wishes.wishes()['20261115-010000']
        self.assertEqual((wish['state'], wish['detail']), ('failed', 'still rejected after 2 fix rounds: simulation tests failed:'))

    def test_unknown_field_is_refused(self):
        with self.assertRaises(AssertionError):
            self.wishes.record('x', 'proposed', colour='red')


if __name__ == '__main__':
    unittest.main()
