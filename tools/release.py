'''Releases: the chat's only way to master, version tags, rollback, reviews, and what follows every deploy.
  python tools/release.py push "reason"   lock -> fetch -> rebase -> all tests -> push master + release -> tag -> car update -> check
  python tools/release.py rollback        the previous deploy tag goes onto the car (release branch); master stays ahead
  python tools/release.py review <commit> a voice build checked by the chat on the real car (CHANGES.md: reviewed)
Every deploy (chat push, voice deploy, rollback) gets a tag deploy/<YYYYMMDD-HHMMSS>-<chat|voice|rollback>, then after_deploy():
car facts measured, voice recordings copied, post-deploy check on the real car (deploy_check.py), project documents rebuilt.'''
import json
import os
import subprocess
import sys
import time

import car_facts
import car_ssh
import car_tunnel
import deploy_check
import integration_lock
import pc_settings
import prerender_voice

TAG_PREFIX = 'deploy/'
SOURCES = ('chat', 'voice', 'rollback')
REVIEWS_FILE = pc_settings.voice_path('data', 'reviews.json')
UPDATE_TIMEOUT_S = 300
UPDATE_COMMAND = 'sudo /home/pi/SunFounder_PiCar-V/tools/picar-update.sh release'
TESTS_TIMEOUT_S = 1800


class ReleaseError(Exception):
    pass


def _git(*args, cwd=pc_settings.REPO):
    result = subprocess.run(['git'] + list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    if result.returncode:
        raise ReleaseError('git %s: %s' % (' '.join(args), result.stdout.strip()[-300:]))
    return result.stdout.strip()


def tag(source, cwd=pc_settings.REPO):
    '''Tags HEAD as a deploy from source and pushes the tag. Returns the tag name.'''
    assert source in SOURCES, source
    name = '%s%s-%s' % (TAG_PREFIX, time.strftime('%Y%m%d-%H%M%S'), source)
    _git('tag', '-a', name, '-m', 'deploy from %s' % source, cwd=cwd)
    _git('push', '-q', 'origin', name, cwd=cwd)
    return name


def deploy_tags(cwd=pc_settings.REPO):
    '''[(tag, commit)] oldest first.'''
    listing = _git('for-each-ref', '--sort=creatordate', '--format=%(refname:short) %(*objectname)', 'refs/tags/' + TAG_PREFIX.rstrip('/'), cwd=cwd)
    return [tuple(line.split()) for line in listing.splitlines() if line.strip()]


def deploy_tags_on_origin(cwd=pc_settings.REPO):
    '''[(tag, subject of its commit)] after fetching the tags, oldest first.'''
    _git('fetch', '-q', '--tags', 'origin', cwd=cwd)
    return [(name, _git('log', '-1', '--format=%s', commit, cwd=cwd)) for name, commit in deploy_tags(cwd)]


def previous_deploy(running_commit, tags):
    '''The deploy before the one the car runs (skipping tags of the same commit), or None.'''
    newest_running = max((index for index, (_, commit) in enumerate(tags) if commit == running_commit), default=None)
    if newest_running is None:
        return None
    return next(((name, commit) for name, commit in reversed(tags[:newest_running]) if commit != running_commit), None)


def update_car(log):
    '''The car pulls the release branch now (picar-update.sh: restart, health check, rollback when unhealthy).'''
    if pc_settings.switched_on('car_ssh_module'):
        output = car_ssh.run(UPDATE_COMMAND, timeout_s=UPDATE_TIMEOUT_S, with_errors=True).text
    else:
        output = subprocess.run(['ssh'] + car_tunnel.SSH_OPTIONS + [pc_settings.CAR, UPDATE_COMMAND],  # legacy ssh (PC switch car_ssh_module off)
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=UPDATE_TIMEOUT_S).stdout.decode('utf-8', 'replace')
    log('car update: ' + (output.strip().splitlines() or ['no output'])[-1])


def after_deploy(source, commit, log):
    '''What follows every deploy. Returns (check passed, report lines).'''
    car_facts.measure(log)
    prerender_voice.update(log)
    passed, report = deploy_check.run(commit, source, log)
    _rebuild_documents(log)
    return passed, report


def _rebuild_documents(log):
    import project_docs  # it reads this module's tags and reviews
    project_docs.build(log)


def push(reason, log=print):
    '''The chat's way to master. Returns the tag.'''
    with integration_lock.held('chat push: ' + reason, wait_s=600, waiting=lambda holder: log('waiting for %s' % holder['owner'])):
        if _git('status', '--porcelain', '--untracked-files=no'):
            raise ReleaseError('uncommitted changes: commit first')
        _git('fetch', '-q', 'origin')
        _git('rebase', 'origin/master')
        tests = subprocess.run([pc_settings.VOICE_PYTHON, os.path.join(pc_settings.REPO, 'tools', 'run_tests.py')], cwd=pc_settings.REPO,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=TESTS_TIMEOUT_S)
        log('\n'.join(line for line in tests.stdout.splitlines() if line.startswith(('tests', 'tools/', 'requirements', 'RESULT'))))
        if tests.returncode:
            raise ReleaseError('tests failed: nothing pushed')
        _git('push', '-q', 'origin', 'HEAD:master', 'HEAD:release')
        name = tag('chat')
        update_car(log)
        after_deploy('chat', _git('rev-parse', 'HEAD'), log)
        return name


def rollback(log=print, cwd=pc_settings.REPO):
    '''The previous deploy back on the car. Returns (tag rolled back to, commit) or raises ReleaseError.'''
    with integration_lock.held('rollback', wait_s=600):
        _git('fetch', '-q', '--tags', 'origin', cwd=cwd)
        running = _git('rev-parse', 'origin/release', cwd=cwd)
        target = previous_deploy(running, deploy_tags(cwd))
        if target is None:
            raise ReleaseError('no earlier deploy to go back to')
        _git('push', '-q', '--force', 'origin', '%s:refs/heads/release' % target[1], cwd=cwd)  # release only; master keeps the newer work
        update_car(log)
        after_deploy('rollback', target[1], log)
        return target


def reviews():
    try:
        with open(REVIEWS_FILE, encoding='utf-8') as saved:
            return json.load(saved)
    except (OSError, ValueError):
        return {}


def review(commit):
    full = _git('rev-parse', commit)
    done = dict(reviews(), **{full: time.strftime('%Y-%m-%d %H:%M:%S')})
    with open(REVIEWS_FILE, 'w', encoding='utf-8') as saved:
        json.dump(done, saved, indent=1)
    return full


def main(argv):
    command, rest = argv[0], argv[1:]
    try:
        if command == 'push':
            print('pushed, tag', push(' '.join(rest) or 'chat change'))
        elif command == 'rollback':
            print('car goes back to %s (%s)' % rollback())
        elif command == 'review':
            print('reviewed', review(rest[0]))
        else:
            raise ReleaseError(__doc__)
    except (ReleaseError, integration_lock.Busy) as error:
        print(error)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
