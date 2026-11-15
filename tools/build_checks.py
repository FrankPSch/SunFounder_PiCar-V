'''The checks a voice build must pass before it is tested, and what a deploy changes (for tools/feature_builder.py).
They do not trust the builder: protected files unchanged, existing switches and stop words unchanged, exactly one new
switch with default off, used outside features.py. git(*args, cwd) is the builder's git (tests replace it).'''
import ast
import os

FEATURES_FILE = 'remote_control/remote_control/features.py'
COMMANDS_FILE = 'remote_control/remote_control/commands.py'
VOCABULARY_FILE = 'remote_control/remote_control/vocabulary.py'
PROTECTED = ('tools/deploy.sh', 'tools/picar-update.sh', 'tools/systemd/', 'tools/voice_server.py', 'tools/voice_server.bat',
             'tools/feature_builder.py', 'tools/build_checks.py', 'tools/sim_runner.py', 'tools/twin.py', 'tools/wish_list.py',
             'tools/voice_launcher.py', 'tools/car_tunnel.py', 'tools/car_ssh.py', 'tools/install_voice_launcher.bat',
             'remote_control/remote_control/voice_protocol.py')


class BuildError(Exception):
    pass


def _features_at(source):
    '''FEATURES of a features.py source as {name: default}.'''
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(getattr(target, 'id', None) == 'FEATURES' for target in node.targets):
            return {key.value: value.elts[0].value for key, value in zip(node.value.keys, node.value.values)}
    raise BuildError('FEATURES not found in ' + FEATURES_FILE)


def _stop_phrases_at(source):
    '''The German and English phrases of Spoken('stop', ...) in a vocabulary.py source.'''
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'Spoken' and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'stop'):
            return [ast.literal_eval(argument) for argument in node.args[4:6]]
    raise BuildError("Spoken('stop', ...) not found in " + VOCABULARY_FILE)


def phrase_meanings_at(source):
    '''{(language, phrase): command name} of every Spoken(...) in a vocabulary.py source.'''
    meanings = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'Spoken' and len(node.args) >= 6:
            name = ast.literal_eval(node.args[0])
            for language, argument in zip(('de', 'en'), node.args[4:6]):
                meanings.update(((language, phrase), name) for phrase in ast.literal_eval(argument))
    return meanings


def changed_meanings(before, after):
    '''Spoken changes, for the deploy summary: ["de 'danke claude': claude_end -> claude_stop", "de 'hupe': new -> horn", ...].'''
    return ['%s %r: %s -> %s' % (language, phrase, before.get((language, phrase), 'new'), after.get((language, phrase), 'removed'))
            for language, phrase in sorted(set(before) | set(after)) if before.get((language, phrase)) != after.get((language, phrase))]


def deploy_summary(worktree, base, git):
    '''(summary text for Claude voice, phrases whose meaning changes) of a build against its base.'''
    files = git('diff', '--name-only', base, 'HEAD', cwd=worktree).split()
    meanings = changed_meanings(phrase_meanings_at(git('show', '%s:%s' % (base, VOCABULARY_FILE), cwd=worktree)),
                                phrase_meanings_at(git('show', 'HEAD:' + VOCABULARY_FILE, cwd=worktree)))
    redefined = [change for change in meanings if ': new ->' not in change]
    text = 'files: %s; spoken phrases: %s' % (', '.join(files) or 'none', '; '.join(meanings) or 'unchanged')
    return text, redefined


def check_build(worktree, base, git):
    '''Problems as text lines (empty = accepted) and the name of the new switch.'''
    problems = []
    changed = git('diff', '--name-only', base, cwd=worktree).split() + git('ls-files', '--others', '--exclude-standard', cwd=worktree).split()
    problems += ['protected file changed: ' + name for name in changed if name.startswith(PROTECTED)]
    old = _features_at(git('show', '%s:%s' % (base, FEATURES_FILE), cwd=worktree))
    with open(os.path.join(worktree, FEATURES_FILE), encoding='utf-8') as source:
        new = _features_at(source.read())
    problems += ['existing switch changed or removed: ' + name for name in old if new.get(name) != old[name]]
    added = [name for name in new if name not in old]
    if len(added) != 1:
        problems.append('expected exactly one new switch in FEATURES, found %d: %s' % (len(added), added))
    problems += ['new switch %s must default to False' % name for name in added if new[name] is not False]
    with open(os.path.join(worktree, VOCABULARY_FILE), encoding='utf-8') as source:
        if _stop_phrases_at(source.read()) != _stop_phrases_at(git('show', '%s:%s' % (base, VOCABULARY_FILE), cwd=worktree)):
            problems.append('stop phrases changed')
    if added and not any(_uses(worktree, name, added[0]) for name in changed if name.endswith(('.py', '.js')) and name != FEATURES_FILE):
        problems.append('the new switch %s is not used anywhere outside features.py' % added[0])
    return problems, (added[0] if len(added) == 1 else None)


def _uses(worktree, name, feature):
    path = os.path.join(worktree, name)
    if not os.path.exists(path):
        return False
    with open(path, encoding='utf-8', errors='replace') as source:
        return feature in source.read()
