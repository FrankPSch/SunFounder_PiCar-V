'''All PiCar tests in one command, each with the Python environment it needs (on the owner's PC):
  car unit tests  tests/          sim environment (Django, OpenCV)
  PC unit tests   tests/pc/       voice environment (Whisper, Piper, anthropic), echo mode: no API key used
  sim suites      tools/sim_test.py, tools/sim_test_features.py against a simulated car server
Usage: python tools/run_tests.py        exit code 0 = all passed'''
import os
import subprocess
import sys

import feature_builder

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE_PYTHON = os.path.join(feature_builder.WORK_DIR, 'env', 'Scripts', 'python.exe')
DLL_PATH = r'C:\ProgramData\Anaconda3\Library\bin'  # sqlite and ssl for the Anaconda-based environments


def run_unit_tests(python, folder, extra_env):
    env = dict(os.environ, PATH=DLL_PATH + os.pathsep + os.environ['PATH'], **extra_env)
    result = subprocess.run([python, '-m', 'unittest', 'discover', '-s', os.path.join(REPO, folder)], cwd=REPO, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    summary = [line for line in result.stdout.splitlines() if line.startswith(('Ran ', 'OK', 'FAILED', 'FAIL:', 'ERROR:'))]
    print('%s: %s' % (folder, ' | '.join(summary)))
    if result.returncode:
        print(result.stdout[-4000:])
    return result.returncode == 0


def main():
    results = [run_unit_tests(feature_builder.SIM_PYTHON, 'tests', {})]
    if os.path.isdir(os.path.join(REPO, 'tests', 'pc')):
        results.append(run_unit_tests(VOICE_PYTHON, os.path.join('tests', 'pc'), {'CLAUDE_VOICE_MODEL': 'echo'}))
    results.append(feature_builder.sim_tests_pass(REPO, print)[0])
    print('RESULT:', 'ALL PASS' if all(results) else 'FAILED')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
