'''Base of the simulation tests. The simulated car (the twin: tools/twin.py SimCar, PICAR_SIM=1) answers at PICAR_SIM_URL,
its picar-data folder is PICAR_DATA. tools/feature_builder.run_sim_tests starts both and runs these tests.
Waiting is always for a condition with a time limit (wait_until); a time the car must keep running is let_run();
samples() polls the state for a while. A bare time.sleep is not allowed in the test files (tests/pc/test_twin.py).
Never run them against the real car: they drive.'''
import json
import os
import sys
import time
import unittest
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, 'remote_control'))
from remote_control import requirements  # noqa: E402,F401  (the tests use requirements.shows)

URL = os.environ.get('PICAR_SIM_URL', 'http://127.0.0.1:8765')
DATA = os.environ.get('PICAR_DATA', '')
POLL_S = 0.25
BAD_REQUEST = 400


class SimCase(unittest.TestCase):
    def cmd(self, command_name, **args):
        request = urllib.request.Request(URL + '/api/cmd', data=json.dumps(dict(cmd=command_name, **args)).encode(), method='POST')
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def state(self):
        with urllib.request.urlopen(URL + '/api/state') as response:
            return json.load(response)

    def get(self, path):
        with urllib.request.urlopen(URL + path) as response:
            return response.read()

    def assert_refused(self, command_name, **args):
        with self.assertRaises(urllib.error.HTTPError) as refused:
            self.cmd(command_name, **args)
        self.assertEqual(refused.exception.code, BAD_REQUEST)

    def wait_until(self, predicate, seconds=8, what='the condition'):
        '''The first state for which predicate holds; fails when none comes within seconds.'''
        end = time.time() + seconds
        while True:
            current = self.state()
            if predicate(current):
                return current
            if time.time() > end:
                tracker, car = current['tracker'], current['car']
                self.fail('%s did not happen within %s s (vision: mode %s, fps %s, found %s, error %s; camera pan %s tilt %s)'
                          % (what, seconds, tracker.get('mode'), tracker.get('fps'), tracker.get('found'), tracker.get('error'),
                             car.get('pan_ticks'), car.get('tilt_ticks')))
            time.sleep(POLL_S)

    def let_run(self, seconds, command=None, every_s=POLL_S):
        '''Lets the car run for seconds (a duration is what is tested), sending command every every_s if given.'''
        end = time.time() + seconds
        while time.time() < end:
            time.sleep(every_s)
            if command:
                self.cmd(command)

    def samples(self, seconds):
        '''The state every POLL_S for seconds.'''
        end = time.time() + seconds
        while time.time() < end:
            yield self.state()
            time.sleep(POLL_S)

    @staticmethod
    def enabled(current, name):
        return next(feature['on'] for feature in current['features'] if feature['name'] == name)
