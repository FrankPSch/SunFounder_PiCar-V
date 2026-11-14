'''All PiCar tests in one command, each with the Python environment it needs (on the owner's PC):
  car unit tests  tests/          sim environment (Django, OpenCV)
  PC unit tests   tests/pc/       voice environment (Whisper, Piper, anthropic), echo mode: no API key used
  sim suites      tools/sim_test.py, tools/sim_test_features.py against a simulated car server (one clean run of three)
Usage: python tools/run_tests.py        exit code 0 = all passed'''
import os
import subprocess
import sys

import feature_builder
import pc_settings

SUMMARY_PREFIXES = ('Ran ', 'OK', 'FAILED', 'FAIL:', 'ERROR:')
FAILURE_TAIL_CHARS = 4000


def run_unit_tests(python, folder, extra_env):
    env = dict(os.environ, PATH=os.pathsep.join(pc_settings.DLL_DIRS + (os.environ['PATH'],)), **extra_env)
    result = subprocess.run([python, '-m', 'unittest', 'discover', '-s', os.path.join(pc_settings.REPO, folder)], cwd=pc_settings.REPO, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    print('%s: %s' % (folder, ' | '.join(line for line in result.stdout.splitlines() if line.startswith(SUMMARY_PREFIXES))))
    if result.returncode:
        print(result.stdout[-FAILURE_TAIL_CHARS:])
    return result.returncode == 0


def main():
    results = [run_unit_tests(feature_builder.SIM_PYTHON, 'tests', {}),
               run_unit_tests(pc_settings.VOICE_PYTHON, os.path.join('tests', 'pc'), {'CLAUDE_VOICE_MODEL': 'echo'}),
               feature_builder.sim_tests_pass(pc_settings.REPO, print)[0]]
    print('RESULT:', 'ALL PASS' if all(results) else 'FAILED')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
