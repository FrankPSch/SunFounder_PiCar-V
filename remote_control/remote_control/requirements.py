'''Requirements = tests. Every requirement has a stable id <level>.<category>.<name> and variants; evidence for a variant
comes from a test that calls covered(), the post-deploy check on the real car, or Frank's own voice (learning sessions).
tools/requirements_report.py puts them together: project/generated/REQUIREMENTS.md, and it fails the test run when a
requirement checked "auto" has no passing test.

  S system  hand-written below        V voice  generated: every spoken command (vocabulary.SPOKEN), each phrase a variant
  C Claude  hand-written below        K click  generated: every dashboard switch, setting and web command
No hardware here; the generated part needs the registries passed in (tests/test_requirements.py writes the catalog).'''
import dataclasses
import functools
import json
import os
from typing import Tuple

from . import vocabulary

COVERAGE_ENV = 'PICAR_COVERAGE'      # file the tests append their evidence to (tools/run_tests.py sets it)
EVIDENCE = ('auto', 'real car', 'your voice', 'manual')


@dataclasses.dataclass(frozen=True)
class Requirement:
    id: str
    title: str
    variants: Tuple[str, ...]
    checked_by: str                  # one of EVIDENCE: where it must be shown


def _r(requirement_id, title, variants, checked_by):
    return Requirement(requirement_id, title, tuple(variants), checked_by)


SYSTEM = (
    _r('S.update.remote', 'A new release reaches the car, it restarts and runs it', ('update timer', 'chat push', 'voice deploy'), 'real car'),
    _r('S.update.messages', 'The car says "Update startet" and "Update fertig"', ('natural voice', 'espeak'), 'manual'),  # a sound: by ear
    _r('S.update.rollback', 'An unhealthy release is rolled back and announced', ('automatic',), 'manual'),
    _r('S.start.message', 'Every start of the car software says "Ich bin bereit"', ('boot', 'restart'), 'manual'),
    _r('S.version.tag', 'Every deploy gets a version tag deploy/<date>-<time>-<source>', ('chat push', 'voice deploy'), 'auto'),
    _r('S.version.rollback', 'The previous version goes back on the car', ('chat', 'voice'), 'auto'),
    _r('S.check.after_deploy', 'After every deploy the real car is checked without driving', ('chat push', 'voice deploy'), 'auto'),
    _r('S.voice_server.start', 'The PC voice server starts as the launcher starts it', ('clean PATH',), 'auto'),
    _r('S.voice_server.reachable', 'The car reaches the voice server and the launcher through its tunnels', ('8766', '8767'), 'real car'),
    _r('S.voice_server.car_off', 'The voice server stops when the car is off for 2 minutes', ('car off',), 'auto'),
    _r('S.speech.natural', 'The car speaks with the recorded natural voice', ('all sentences recorded', 'stitched numbers', 'espeak fallback'), 'auto'),
    _r('S.speech.on_car', 'The recordings are on the car and it uses them', ('recordings', 'voice natural'), 'real car'),
    _r('S.recognition.vocabulary', 'Every phrase word is in the car\'s speech model', ('de', 'en'), 'real car'),
    _r('S.recognition.piper', 'Every phrase spoken by Piper is understood by the car\'s model', ('de', 'en'), 'auto'),
    _r('S.recognition.noise_word', 'A noise word before a phrase is ignored, except for driving phrases', ('command', 'driving'), 'auto'),
    _r('S.learning.session', 'A learning session: tries, feedback, kept recordings, end by voice or silence', ('parked', 'driving'), 'auto'),
    _r('S.integration.lock', 'Only one of voice builder and chat changes master at a time', ('build', 'deploy', 'chat push'), 'auto'),
    _r('S.integration.rebase', 'A voice deploy goes on top of a newer master, a conflict goes to the chat', ('clean', 'conflict'), 'auto'),
    _r('S.docs.generated', 'The generated project documents are rebuilt', ('wishes', 'changes', 'requirements', 'car facts', 'voice commands'), 'auto'),
)
CLAUDE = (
    _r('C.conversation.greeting', '"hey claude": the PC says hello in the car\'s language', ('de', 'en'), 'auto'),
    _r('C.conversation.answer', 'A sentence goes to Claude and the answer is spoken in the language heard', ('de', 'en'), 'auto'),
    _r('C.conversation.end', '"danke claude" or a minute of silence ends the conversation, the server keeps running', ('danke claude', 'silence'), 'manual'),
    _r('C.tool.set_feature', 'Claude switches a feature on or off', ('known', 'unknown refused'), 'auto'),
    _r('C.tool.run_command', 'Claude runs a spoken command that does not drive', ('allowed', 'driving refused'), 'auto'),
    _r('C.wish.flow', 'A wish: proposal, "go", build, "deploy"', ('proposed', 'built', 'deployed'), 'auto'),
    _r('C.wish.for_chat', 'A wish the voice build cannot do goes to the chat', ('by Claude voice', 'by the builder', 'deploy conflict'), 'auto'),
    _r('C.wish.list', 'Every step of every wish is in the wish list', ('proposed', 'dropped', 'deployed', 'failed'), 'auto'),
    _r('C.project.read', 'Claude voice answers from the project documents and status', ('status', 'documents', 'car facts'), 'auto'),
    _r('C.deploy.summary', 'Before "deploy" Claude voice hears what changes, including changed phrase meanings', ('switch', 'phrases'), 'auto'),
    _r('C.news.version', 'A new version from the chat is announced on the car', ('new version',), 'auto'),
)


def voice():
    return tuple(_r('V.%s.%s' % (spoken.group, spoken.name), '%s: %s' % (vocabulary.GROUP_NAMES[spoken.group]['en'], vocabulary.EXPLANATIONS[spoken.name][1]),
                    ['%s: %s' % (language, phrase) for language in vocabulary.LANGUAGES for phrase in spoken.phrases(language)], 'auto')
                 for spoken in vocabulary.SPOKEN)


def click(feature_names, setting_names, api_names):
    return (tuple(_r('K.switch.' + name, 'Dashboard switch ' + name, ('on', 'off'), 'auto') for name in feature_names)
            + tuple(_r('K.setting.' + name, 'Dashboard setting ' + name, ('set',), 'auto') for name in setting_names)
            + tuple(_r('K.command.' + name, 'Web command ' + name + ' (dashboard button or key)', ('run',), 'auto') for name in api_names))


def catalog(feature_names, setting_names, api_names):
    requirements = SYSTEM + CLAUDE + voice() + click(feature_names, setting_names, api_names)
    ids = [requirement.id for requirement in requirements]
    assert len(ids) == len(set(ids)), 'requirement id used twice'
    return requirements


def voice_requirement(language, phrase):
    '''(id, variant) of a spoken phrase.'''
    spoken = vocabulary.phrases(language)[phrase]
    return 'V.%s.%s' % (spoken.group, spoken.name), '%s: %s' % (language, phrase)


def covered(requirement_id, variant, evidence='auto'):
    '''A test showed this variant. Written to the coverage file when the run collects evidence.'''
    path = os.environ.get(COVERAGE_ENV)
    if path:
        with open(path, 'a', encoding='utf-8') as coverage:
            coverage.write(json.dumps({'id': requirement_id, 'variant': variant, 'evidence': evidence}, ensure_ascii=False) + '\n')


def shows(*evidence):
    """Decorator for a test: when it passes, each (id, variant) counts as shown."""
    def wrap(test):
        @functools.wraps(test)
        def run(*args, **kwargs):
            result = test(*args, **kwargs)
            for requirement_id, variant in evidence:
                covered(requirement_id, variant)
            return result
        return run
    return wrap


def write_catalog(path, requirements):
    with open(path, 'w', encoding='utf-8') as saved:
        json.dump([dataclasses.asdict(requirement) for requirement in requirements], saved, ensure_ascii=False, indent=1)
