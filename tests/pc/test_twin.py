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

    def test_facts_recordings_update_and_check(self):
        log = []
        with twin.SimCar(pc_settings.REPO, TWIN_PORT, self.data) as sim, twin.ssh_to(self.car), \
                mock.patch.object(deploy_check, 'STATE_URL', sim.url + '/api/state'):
            facts = car_facts.measure(log.append)
            copied = prerender_voice.copy_missing_to_car(log.append)
            release.update_car(log.append)
            passed, report = deploy_check.run(self.car.commit[:7], 'chat', log.append)
        self.assertEqual((facts['arch'], facts['commit'][:7]), ('armv7l', self.car.commit[:7]))
        self.assertEqual(copied, sum(len(names) for names in prerender_voice.local_recordings().values()))
        self.assertIn('car update: picar-update: updated twin -> ' + self.car.commit, log)
        self.assertTrue(passed, report)


if __name__ == '__main__':
    unittest.main()
