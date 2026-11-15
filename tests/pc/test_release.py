'''Releases and what follows them (tools/release.py, deploy_check.py, project_docs.py, requirements_report.py) and the
voice side of it (deploy summary, rollback by voice, news of a chat deploy, read tools). Git, the car and the tests
inside a push are replaced. Voice environment; run by tools/run_tests.py.'''
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault('CLAUDE_VOICE_MODEL', 'echo')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools'))
import deploy_check  # noqa: E402
import feature_builder  # noqa: E402
import integration_lock  # noqa: E402
import project_docs  # noqa: E402
import release  # noqa: E402
import requirements_report  # noqa: E402
import voice_server  # noqa: E402
import wish_list  # noqa: E402
from remote_control import requirements  # noqa: E402

VOCABULARY_BEFORE = '''SPOKEN = (Spoken('claude_end', 'system', 'conversation_end', {}, ('danke claude',), ('thank you claude',)),
          Spoken('photo', 'media', 'photo', {}, ('foto',), ('photo',)))'''
VOCABULARY_AFTER = '''SPOKEN = (Spoken('claude_stop', 'system', 'voice_server', {}, ('danke claude',), ('stop claude',)),
          Spoken('photo', 'media', 'photo', {}, ('foto',), ('photo',)),
          Spoken('horn', 'drive', 'horn', {}, ('hupe',), ('horn',)))'''


class Folder(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='release_')
        for patch in (mock.patch.object(integration_lock, 'LOCK_FILE', os.path.join(self.folder, 'integration.lock')),
                      mock.patch.object(requirements_report, 'REAL_CAR_FILE', os.path.join(self.folder, 'real_car.jsonl'))):
            patch.start()
            self.addCleanup(patch.stop)


class FakeGit(object):
    '''release._git: answers from a table, records every call.'''
    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, *args, cwd=None):
        self.calls.append(args)
        return next((answer for start, answer in self.answers.items() if args[:len(start)] == start), '')


class ReleaseTest(Folder):
    @requirements.shows(('S.version.tag', 'chat push'))
    def test_tag_names_source_and_time(self):
        git = FakeGit()
        with mock.patch.object(release, '_git', git):
            name = release.tag('chat')
        self.assertRegex(name, r'^deploy/\d{8}-\d{6}-chat$')
        self.assertEqual(git.calls[-1], ('push', '-q', 'origin', name))

    def test_previous_deploy_skips_the_same_commit(self):
        tags = [('deploy/1-chat', 'c1'), ('deploy/2-voice', 'c2'), ('deploy/3-chat', 'c2'), ('deploy/4-voice', 'c3')]
        self.assertEqual(release.previous_deploy('c3', tags), ('deploy/3-chat', 'c2'))
        self.assertEqual(release.previous_deploy('c2', tags), ('deploy/1-chat', 'c1'))
        self.assertIsNone(release.previous_deploy('c1', tags))
        self.assertIsNone(release.previous_deploy('unknown', tags))

    @requirements.shows(('S.version.rollback', 'chat'))
    def test_rollback_moves_only_release(self):
        git = FakeGit({('rev-parse', 'origin/release'): 'c3', ('for-each-ref',): 'deploy/1-chat c2\ndeploy/2-voice c3'})
        after = []
        with mock.patch.object(release, '_git', git), mock.patch.object(release, 'update_car', lambda log: None), \
                mock.patch.object(release, 'after_deploy', lambda source, commit, log: after.append((source, commit))):
            self.assertEqual(release.rollback(lambda text: None), ('deploy/1-chat', 'c2'))
        self.assertIn(('push', '-q', '--force', 'origin', 'c2:refs/heads/release'), git.calls)
        self.assertFalse([call for call in git.calls if 'master' in ' '.join(call)])
        self.assertEqual(after, [('rollback', 'c2')])

    @requirements.shows(('S.check.after_deploy', 'chat push'), ('S.integration.lock', 'chat push'))
    def test_push_tests_first_then_pushes_tags_updates_and_checks(self):
        git, steps = FakeGit({('rev-parse', 'HEAD'): 'c9'}), []
        with mock.patch.object(release, '_git', git), \
                mock.patch.object(release.subprocess, 'run', lambda *args, **kwargs: steps.append('tests') or mock.Mock(returncode=0, stdout='RESULT: ALL PASS')), \
                mock.patch.object(release, 'update_car', lambda log: steps.append('car update')), \
                mock.patch.object(release, 'after_deploy', lambda source, commit, log: steps.append('check %s %s' % (source, commit)) or (True, [])):
            name = release.push('test', lambda text: None)
        pushed = git.calls.index(('push', '-q', 'origin', 'HEAD:master', 'HEAD:release'))
        self.assertLess(git.calls.index(('rebase', 'origin/master')), pushed)
        self.assertTrue(name.endswith('-chat'))
        self.assertEqual(steps, ['tests', 'car update', 'check chat c9'])

    def test_failing_tests_push_nothing(self):
        git = FakeGit()
        with mock.patch.object(release, '_git', git), \
                mock.patch.object(release.subprocess, 'run', lambda *args, **kwargs: mock.Mock(returncode=1, stdout='RESULT: FAILED')):
            with self.assertRaises(release.ReleaseError):
                release.push('test', lambda text: None)
        self.assertFalse([call for call in git.calls if call[0] == 'push'])
        self.assertIsNone(integration_lock.holder())


class VoiceDeployTest(Folder):
    @requirements.shows(('S.integration.rebase', 'clean'), ('S.integration.lock', 'deploy'), ('S.version.tag', 'voice deploy'),
                        ('S.check.after_deploy', 'voice deploy'))
    def test_a_deploy_on_top_of_a_newer_master(self):
        builder = feature_builder.Builder(lambda language, text: None, wish_list.WishList(self.folder))
        builder.propose('Hupe', 'Hupt', ['kurz'], 'de')
        builder.state = 'built'
        builder.request.update(worktree=self.folder, branch='voice/x', log=os.path.join(self.folder, 'build.log'))
        held, steps = [], []

        def run(command, cwd=None, **kwargs):
            held.append(integration_lock.holder()['owner'])
            if command[1] == 'merge-base':
                return 1, ''
            return 0, 'DEPLOY OK: car runs c9' if 'deploy.sh' in ' '.join(command) else ''
        with mock.patch.object(feature_builder, '_run', run), mock.patch.object(feature_builder, '_git', lambda *args, **kwargs: 'c9'), \
                mock.patch.object(feature_builder, 'sim_tests_pass', lambda worktree, log: steps.append('tests again') or (True, '')), \
                mock.patch.object(release, 'tag', lambda source, cwd=None: steps.append('tag ' + source) or 'deploy/x-voice'), \
                mock.patch.object(release, 'after_deploy', lambda source, commit, log: steps.append('check ' + source) or (True, ['ok   x'])):
            builder._deploy()
        self.assertEqual(builder.state, 'deployed')
        self.assertEqual(steps, ['tests again', 'tag voice', 'check voice'])
        self.assertEqual(set(held), {'voice deploy: Hupe'})


class DeployCheckTest(Folder):
    def test_checks_become_real_car_evidence(self):
        state = {'speaker': {'voice': 'natural', 'recordings': {'de': 2}}, 'audio': {'missing_words': {'de': []}}}
        with mock.patch.object(deploy_check, '_on_car', lambda command: 'c9abc\n' if 'rev-parse' in command else '127.0.0.1:8766\n127.0.0.1:8767\n'), \
                mock.patch.object(deploy_check, '_car_state', lambda: state), \
                mock.patch.object(deploy_check, '_recordings_on_pc', lambda: {'de': 2}):
            passed, report = deploy_check.run('c9', 'voice', lambda text: None)
        self.assertTrue(passed, report)
        with open(requirements_report.REAL_CAR_FILE, encoding='utf-8') as evidence:
            shown = [(line['id'], line['variant']) for line in map(json.loads, evidence)]
        self.assertIn(('S.update.remote', 'voice deploy'), shown)
        self.assertIn(('S.speech.on_car', 'voice natural'), shown)

    def test_a_failed_check_is_not_evidence(self):
        state = {'speaker': {'voice': 'espeak', 'recordings': {}}, 'audio': {'missing_words': {'de': ['hupe']}}}
        with mock.patch.object(deploy_check, '_on_car', lambda command: 'c1\n'), mock.patch.object(deploy_check, '_car_state', lambda: state), \
                mock.patch.object(deploy_check, '_recordings_on_pc', lambda: {'de': 2}):
            passed, report = deploy_check.run('c9', 'chat', lambda text: None)
        self.assertFalse(passed)
        self.assertTrue(any('FAIL S.recognition.vocabulary / de: missing words: hupe' in line for line in report))
        self.assertFalse(os.path.exists(requirements_report.REAL_CAR_FILE) and open(requirements_report.REAL_CAR_FILE).read().count('voice natural'))


class DeploySummaryTest(unittest.TestCase):
    def test_changed_phrase_meanings(self):
        changes = feature_builder.changed_meanings(feature_builder.phrase_meanings_at(VOCABULARY_BEFORE), feature_builder.phrase_meanings_at(VOCABULARY_AFTER))
        self.assertEqual(changes, ["de 'danke claude': claude_end -> claude_stop", "de 'hupe': new -> horn", "en 'horn': new -> horn",
                                   "en 'stop claude': new -> claude_stop", "en 'thank you claude': claude_end -> removed"])

    @requirements.shows(('C.deploy.summary', 'switch'), ('C.deploy.summary', 'phrases'))
    def test_a_changed_meaning_needs_a_yes(self):
        builder = feature_builder.Builder(lambda language, text: None, wish_list.WishList(tempfile.mkdtemp()))
        builder.propose('Hupe', 'Hupt', ['kurz'], 'de')
        builder.state = 'built'
        builder.request.update(feature='horn', summary='files: vocabulary.py', redefined=["de 'danke claude': claude_end -> claude_stop"])
        self.assertIn('THESE PHRASES CHANGE MEANING', builder.describe())
        self.assertIn('switch horn', builder.describe())
        self.assertIn('Not deployed: these phrases change meaning', builder.deploy('Deploy.'))
        with mock.patch.object(builder, '_deploy', lambda: None):
            self.assertIn('Deploy started', builder.deploy('Deploy.', phrase_changes_confirmed=True))


class DocumentsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp(prefix='generated_')
        for patch in (mock.patch.object(project_docs, 'GENERATED', folder), mock.patch.object(pc_settings_module(), 'WISHES_DIR', folder),
                      mock.patch.object(release, 'REVIEWS_FILE', os.path.join(folder, 'reviews.json')),
                      mock.patch.object(requirements_report, 'MARKDOWN_FILE', os.path.join(folder, 'REQUIREMENTS.md')),
                      mock.patch.object(requirements_report, 'STATUS_FILE', os.path.join(folder, 'requirements_status.json'))):
            patch.start()
            self.addCleanup(patch.stop)
        self.folder = folder

    @requirements.shows(*[('S.docs.generated', variant) for variant in ('wishes', 'changes', 'car facts', 'voice commands', 'requirements')])
    def test_all_documents_are_built(self):
        project_docs.build(lambda text: None)
        for name in ('CHANGES', 'CAR_FACTS', 'VOICE_COMMANDS'):
            self.assertTrue(os.path.getsize(project_docs.document_path(name)) > 0, name)
        self.assertIn('| date | commit | source |', project_docs.read('CHANGES'))
        wish_list.WishList(self.folder).record('w1', 'proposed', title='Hupe', understanding='Hupt', options=['kurz'], language='de')
        project_docs.build(lambda text: None)
        self.assertIn('### Hupe: proposed', project_docs.read('REQUIREMENTS', 'Hupe'))
        self.assertIn('## W Spoken wishes', project_docs.read('REQUIREMENTS'))

    @requirements.shows(('C.project.read', 'documents'), ('C.project.read', 'status'))
    def test_claude_voice_reads_documents_and_status(self):
        project_docs.build(lambda text: None)
        self.assertIn('kamera links', project_docs.read('VOICE_COMMANDS', 'Kamera'))
        self.assertIn('Unknown document', project_docs.read('SECRETS'))
        status = project_docs.status()
        for expected in ('Car runs:', 'Open wishes:', 'not reviewed', 'Requirements:'):
            self.assertIn(expected, status)

    def test_voice_builds_stay_unreviewed_until_the_chat_reviews(self):
        rows = [{'commit': 'c1', 'short': 'c1', 'date': 'd', 'subject': 'Voice feature horn: Hupe', 'source': 'voice', 'tags': [], 'reviewed': False}]
        self.assertIn('| **no** |', project_docs._changes_markdown(rows, 'c1'))


def pc_settings_module():
    import pc_settings
    return pc_settings


class VoiceServerTest(Folder):
    @requirements.shows(('S.version.rollback', 'voice'))
    def test_rollback_only_when_asked(self):
        with mock.patch.object(voice_server.threading, 'Thread', mock.Mock()):
            self.assertIn('Not started', voice_server.start_rollback('Wie spät ist es?'))
            self.assertIn('Rollback started', voice_server.start_rollback('Bitte zurück zur letzten Version.'))

    @requirements.shows(('C.news.version', 'new version'))
    def test_a_chat_deploy_is_announced(self):
        answers = iter([[('deploy/1-voice', 'Voice feature horn')], [('deploy/1-voice', 'Voice feature horn'), ('deploy/2-chat', 'Natural voice')],
                        [('deploy/1-voice', 'Voice feature horn'), ('deploy/2-chat', 'Natural voice'), ('deploy/3-voice', 'Voice feature light')]])
        said = []
        with mock.patch.object(release, 'deploy_tags_on_origin', lambda: next(answers)), mock.patch.object(voice_server, 'announce', lambda language, text: said.append(text)), \
                mock.patch.object(voice_server.time, 'sleep', mock.Mock(side_effect=[None, None, StopIteration])):
            with self.assertRaises(StopIteration):
                voice_server.announce_chat_deploys()
        self.assertEqual(said, ['Neue Version vom Chat ist auf dem Auto: Natural voice'])

    def test_read_tools_are_offered(self):
        for name, arguments in (('project_status', {}), ('read_doc', {'name': 'REQUIREMENTS', 'search': 'W '}), ('rollback_version', {}),
                                ('deploy_feature', {'phrase_changes_confirmed': False})):
            self.assertTrue(voice_server.claude_chat.valid_tool_input(name, arguments), name)


class ReportTest(unittest.TestCase):
    def test_unknown_evidence_and_owner_voice(self):
        folder = tempfile.mkdtemp(prefix='report_')
        catalog = [{'id': 'V.camera.camera_left', 'title': 't', 'variants': ['de: kamera links'], 'checked_by': 'auto'},
                   {'id': 'S.x.y', 'title': 't', 'variants': ['a'], 'checked_by': 'real car'}]
        paths = {name: os.path.join(folder, name) for name in ('catalog', 'coverage', 'real', 'owner', 'status', 'md')}
        with open(paths['catalog'], 'w') as saved:
            json.dump(catalog, saved)
        with open(paths['coverage'], 'w') as saved:
            saved.write(json.dumps({'id': 'V.camera.typo', 'variant': 'x', 'evidence': 'auto'}) + '\n')
        with open(paths['owner'], 'w', encoding='utf-8') as saved:
            saved.write(json.dumps({'language': 'de', 'phrase': 'kamera links', 'verdict': 'understood', 'time': 'today'}) + '\n')
        with mock.patch.multiple(requirements_report, CATALOG_FILE=paths['catalog'], COVERAGE_FILE=paths['coverage'], REAL_CAR_FILE=paths['real'],
                                 OWNER_VOICE_FILE=paths['owner'], STATUS_FILE=paths['status'], MARKDOWN_FILE=paths['md']):
            rows, missing, unknown = requirements_report.build()
        self.assertEqual(unknown, ['V.camera.typo / x'])
        self.assertEqual(missing, ['V.camera.camera_left (0 of 1 variants)'])
        self.assertEqual(rows[0]['variants'][0]['shown'], {'your voice': 'today'})


if __name__ == '__main__':
    unittest.main()
