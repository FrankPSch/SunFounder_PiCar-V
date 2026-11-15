'''Every SSH call from the PC to the car, in one place (PC switch car_ssh_module; host and key in pc_settings.py).
Bytes in and out: Windows text mode would send CRLF line ends, which the car's shell cannot read.
  run(command)            a command on the car -> Result(code, output); .text is the output as text
  run_script(script)      a shell script on the car (sh -s, the script as bytes)
  copy_tar(files, folder) files {name on the car: PC path} unpacked into a car folder -> exit code
  tunnel(port)            the ssh command line of a reverse tunnel car 127.0.0.1:port -> PC 127.0.0.1:port
tests/pc/test_twin.py runs it against the twin's fake car and checks that no other module builds ssh calls itself.'''
import collections
import subprocess
import tarfile

import pc_settings

OPTIONS = ['-i', pc_settings.CAR_SSH_KEY, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15',
           '-o', 'ServerAliveCountMax=2']
TIMEOUT_S = 60


class Result(collections.namedtuple('Result', 'code output')):
    @property
    def text(self):
        return self.output.decode('utf-8', 'replace')


def _command(remote):
    return ['ssh'] + OPTIONS + [pc_settings.CAR, remote]


def run(command, stdin=b'', timeout_s=TIMEOUT_S, with_errors=False):
    '''with_errors: the car's error output is part of output (for a log).'''
    result = subprocess.run(_command(command), input=stdin, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT if with_errors else subprocess.DEVNULL, timeout=timeout_s)
    return Result(result.returncode, result.stdout or b'')


def run_script(script, timeout_s=TIMEOUT_S):
    return run('sh -s', script.encode('utf-8'), timeout_s)


def copy_tar(files, car_folder, timeout_s=TIMEOUT_S):
    untar = subprocess.Popen(_command('tar xf - -C ' + car_folder), stdin=subprocess.PIPE)
    with tarfile.open(fileobj=untar.stdin, mode='w|') as archive:
        for name, path in sorted(files.items()):
            archive.add(path, arcname=name)
    untar.stdin.close()
    return untar.wait(timeout_s)


def tunnel(port):
    return ['ssh', '-N'] + OPTIONS + ['-o', 'ExitOnForwardFailure=yes', '-R', '%d:127.0.0.1:%d' % (port, port), pc_settings.CAR]
