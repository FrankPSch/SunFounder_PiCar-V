'''The digital twin (tools/twin.py) itself, and what follows a deploy run on it end to end: car facts, the voice
recordings copied to the car, the update and the post-deploy check, all over the fake SSH car and against the
simulated car server on a car-like picar-data folder. Voice environment; run by tools/run_tests.py.'''
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault('CLAUDE_VOICE_MODEL', 'echo')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools'))
import car_facts  # noqa: E402
import deploy_check  # noqa: E402
import pc_settings  # noqa: E402
import prerender_voice  # noqa: E402
import release  # noqa: E402
import twin  # noqa: E402

TWIN_PORT = 8791        # feature_builder.SIM_PORT + 1: a voice build may run its own sim meanwhile


def _lines(path):
    with open(path, encoding='utf-8') as source:
        return source.read().splitlines()


class TwinFolders(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='twin_')
        self.addCleanup(shutil.rmtree, self.folder, True)

    def test_car_data_is_laid_out_like_the_car(self):
        data = twin.seed_car_data(os.path.join(self.folder, 'picar-data'))
        for name in ('features.json', 'settings.json', 'calibration', 'media', 'voice_cache/de', 'models'):
            self.assertTrue(os.path.exists(os.path.join(data, name)), name)
        with open(os.path.join(data, 'twin_owners.json')) as owners:
            self.assertEqual(json.load(owners)['features.json'], 'root')

    def test_voice_dir_has_wishes_results_and_logs(self):
        voice = twin.seed_voice_dir(os.path.join(self.folder, 'voice'))
        for name in ('wishes.jsonl', 'recordings/results.jsonl', 'server.log', 'launcher.log'):
            self.assertTrue(os.path.isfile(os.path.join(voice, name)), name)

    def test_unknown_command_fails_like_ssh(self):
        car = twin.FakeCar(twin.seed_car_data(os.path.join(self.folder, 'picar-data')), commit='abc1234')
        with twin.ssh_to(car):
            result = release.subprocess.run(['ssh', 'pi@car', 'rm -rf /'], stdout=release.subprocess.PIPE)
        self.assertEqual((result.returncode, car.commands), (255, ['rm -rf /']))


class SimTestsWaitForConditions(unittest.TestCase):
    def test_no_bare_sleep_in_the_simulation_tests(self):
        '''A sleep before a check is a timing guess that fails on a busy PC: wait_until / let_run / samples instead.'''
        folder = os.path.join(pc_settings.REPO, 'tests', 'sim')
        sleeping = [name for name in sorted(os.listdir(folder)) if name.startswith('test_') and name.endswith('.py')
                    and any('sleep(' in line for line in _lines(os.path.join(folder, name)))]
        self.assertEqual(sleeping, [])


class AfterDeployOnTheTwin(unittest.TestCase):
    '''release.after_deploy without the documents: every SSH call and the car's web state come from the twin.'''
    def setUp(self):
        folder = tempfile.mkdtemp(prefix='twin_')
        self.addCleanup(shutil.rmtree, folder, True)
        self.data = twin.seed_car_data(os.path.join(folder, 'picar-data'))
        self.car = twin.FakeCar(self.data)
        for patch in (mock.patch.object(car_facts, 'FACTS_FILE', os.path.join(folder, 'car_facts.json')),
                      mock.patch.object(deploy_check.requirements_report, 'REAL_CAR_FILE', os.path.join(folder, 'real_car.jsonl'))):
            patch.start()
            self.addCleanup(patch.stop)

    def _after_deploy(self):
        log = []
        with twin.SimCar(pc_settings.REPO, TWIN_PORT, self.data) as sim, twin.ssh_to(self.car),                 mock.patch.object(deploy_check, 'STATE_URL', sim.url + '/api/state'):
            facts = car_facts.measure(log.append)
            copied = prerender_voice.copy_missing_to_car(log.append)
            release.update_car(log.append)
            passed, report = deploy_check.run(self.car.commit[:7], 'chat', log.append)
        self.assertEqual((facts['arch'], facts['commit'][:7]), ('armv7l', self.car.commit[:7]))
        self.assertEqual(copied, sum(len(names) for names in prerender_voice.local_recordings().values()))
        self.assertIn('car update: picar-update: updated twin -> ' + self.car.commit, log)
        self.assertTrue(passed, report)

    def test_facts_recordings_update_and_check(self):
        self._after_deploy()

    def test_the_same_through_car_ssh(self):
        with mock.patch.object(pc_settings, 'switched_on', lambda name: name == 'car_ssh_module'):
            self._after_deploy()


class PcSwitches(unittest.TestCase):
    def test_off_by_default_and_unknown_names_refused(self):
        with mock.patch.object(pc_settings, 'SWITCHES_FILE', os.path.join(tempfile.mkdtemp(prefix='twin_'), 'pc_switches.json')):
            self.assertEqual([name for name in pc_settings.SWITCHES if pc_settings.switched_on(name)], [])
            with self.assertRaises(KeyError):
                pc_settings.switched_on('no_such_switch')

    def test_voice_folder_by_kind_copies_and_keeps_the_old_files(self):
        voice = twin.seed_voice_dir(tempfile.mkdtemp(prefix='twin_voice_'))
        self.addCleanup(shutil.rmtree, voice, True)
        switches = os.path.join(voice, 'pc_switches.json')
        with mock.patch.object(pc_settings, 'VOICE_DIR', voice), mock.patch.object(pc_settings, 'SWITCHES_FILE', switches):
            self.assertEqual(pc_settings.voice_path('data', 'wishes.jsonl'), os.path.join(voice, 'wishes.jsonl'), 'off: the old place')
            with open(switches, 'w') as stored:
                json.dump({'voice_folder_by_kind': True}, stored)
            kinds = {'wishes.jsonl': 'data', 'recordings/results.jsonl': 'data', 'server.log': 'logs', 'car_facts.json': 'generated'}
            places = {name: pc_settings.voice_path(kind, *name.split('/')) for name, kind in kinds.items()}
            self.assertEqual(pc_settings.voice_path('data'), os.path.join(voice, 'data'))
        self.assertEqual(places, {name: os.path.join(voice, kind, *name.split('/')) for name, kind in kinds.items()})
        for name in ('wishes.jsonl', 'recordings/results.jsonl', 'server.log'):
            self.assertTrue(os.path.isfile(os.path.join(voice, *name.split('/'))), 'the old copy stays: ' + name)
            with open(places[name], encoding='utf-8') as moved, open(os.path.join(voice, *name.split('/')), encoding='utf-8') as old:
                self.assertEqual(moved.read(), old.read(), name)
        self.assertEqual(sorted(name for name in os.listdir(voice) if os.path.isdir(os.path.join(voice, name))), ['data', 'generated', 'logs', 'recordings'],
                         'only the kinds are new, nothing else is copied')

    def test_only_car_ssh_builds_ssh_calls(self):
        """Every other ssh call is the old way, kept while the switch car_ssh_module is off, and marked so."""
        tools = os.path.join(pc_settings.REPO, 'tools')
        unmarked = ['%s:%d' % (name, number) for name in sorted(os.listdir(tools)) if name.endswith('.py') and name not in ('car_ssh.py', 'twin.py')
                    for number, line in enumerate(_lines(os.path.join(tools, name)), 1)
                    if "['ssh'" in line and 'legacy ssh (PC switch car_ssh_module off)' not in line]
        self.assertEqual(unmarked, [])


if __name__ == '__main__':
    unittest.main()
