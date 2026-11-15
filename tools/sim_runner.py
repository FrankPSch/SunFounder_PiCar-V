'''The simulation tests of a checkout (tests/sim/) on the digital twin (tools/twin.py): a fresh simulated car on a
car-like picar-data folder, up to SIM_TEST_TRIES runs, one clean run counts. Used by tools/run_tests.py and the voice
builder (tools/feature_builder.py).'''
import os
import shutil
import subprocess
import tempfile

import pc_settings
import twin

WORK_DIR = os.environ.get('PICAR_WORK_DIR', pc_settings.VOICE_DIR)
SIM_PYTHON = os.path.join(WORK_DIR, 'simenv', 'Scripts', 'python.exe')
SIM_SUITE = os.path.join('tests', 'sim')
SIM_SUITE_TIMEOUT_S = 600
SIM_TEST_TRIES = 3          # some checks depend on timing (ball tracking under load): one clean run of three counts
SIM_PORT = 8790


def run_command(command, cwd, timeout=600, env=None, stdin_text=None):
    '''(exit code, output with errors) of a command.'''
    result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, env=env,
                            input=stdin_text.encode('utf-8') if stdin_text is not None else None)
    return result.returncode, result.stdout.decode('utf-8', 'replace')


def run_sim_tests(worktree):
    '''The simulation tests (tests/sim/) against a fresh simulated car on a car-like picar-data folder (the twin).
    Returns (passed, a short report: the summary, or the failures).'''
    data_dir = twin.seed_car_data(tempfile.mkdtemp(prefix='simdata_', dir=WORK_DIR))
    try:
        with twin.SimCar(worktree, SIM_PORT, data_dir, SIM_PYTHON) as car:
            code, output = run_command([SIM_PYTHON, '-m', 'unittest', 'discover', '-s', SIM_SUITE], cwd=worktree, timeout=SIM_SUITE_TIMEOUT_S,
                                env=dict(car.env, PICAR_SIM_URL=car.url))
            summary = ' | '.join(line for line in output.splitlines() if line.startswith(('Ran ', 'OK', 'FAILED')))
            if code == 0:
                return True, 'tests/sim: ' + summary
            failures = [line for line in output.splitlines() if line.startswith(('FAIL:', 'ERROR:', 'AssertionError', 'urllib.error'))]
            return False, 'tests/sim: %s\n%s' % (summary, '\n'.join(failures[-15:]) or output[-1500:])
    except RuntimeError as error:
        return False, str(error)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)  # a temporary folder this function made


def sim_tests_pass(worktree, log):
    '''run_sim_tests up to SIM_TEST_TRIES times; one clean run counts. log(report) for every run. Returns (passed, last report).'''
    for _ in range(SIM_TEST_TRIES):
        passed, report = run_sim_tests(worktree)
        log(report)
        if passed:
            break
    return passed, report
