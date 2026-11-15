'''Claude voice's tools in one registry (PC switch voice_tool_registry): name, description, input, side-effect level and
the handler, together. claude_chat sends the schemas to Claude, voice_server runs the handler with the turn.
  read    answers only                     car     changes the car, never drives it
  build   starts work on the PC            deploy  puts other code on the car
A handler gets the turn (voice_server.Turn: car, handler, language, transcript, builder, start_rollback) and the input
claude_chat checked against the schema. tests/pc/test_voice_server.py checks that every tool is complete.'''
import collections

import car_facts
import project_docs

LEVELS = ('read', 'car', 'build', 'deploy')
Tool = collections.namedtuple('Tool', 'name description properties required level run')
TITLE = {'type': 'string', 'description': 'two to five words'}


def _set_feature(turn, arguments):
    known = [feature[0] for feature in turn.car['features']]
    if arguments['name'] not in known:
        return 'Unknown feature. Known: ' + ', '.join(known)
    turn.handler.send_text({'set_feature': arguments['name'], 'on': arguments['on']})
    return 'Switched %s %s.' % (arguments['name'], 'on' if arguments['on'] else 'off')


def _run_command(turn, arguments):
    known = [command[0] for command in turn.car['commands']]
    if arguments['name'] not in known:
        return 'Unknown command. Known: ' + ', '.join(known)
    turn.handler.send_text({'run_command': arguments['name']})
    return 'Ran %s.' % arguments['name']


TOOLS = (
    Tool('set_feature', 'Switch one of the car\'s features on or off. The names and current states are in the status line.',
         {'name': {'type': 'string'}, 'on': {'type': 'boolean'}}, ['name', 'on'], 'car', _set_feature),
    Tool('run_command', 'Run one of the car\'s spoken commands that do not drive it. The names are in the status line.',
         {'name': {'type': 'string'}}, ['name'], 'car', _run_command),
    Tool('propose_feature', 'Save a new feature request with your understanding and the options, before asking for "go".',
         {'title': TITLE, 'understanding': {'type': 'string'},
          'options': {'type': 'array', 'items': {'type': 'string'}, 'description': 'two or three ways to build it'}},
         ['title', 'understanding', 'options'], 'read',
         lambda turn, arguments: turn.builder.propose(arguments['title'], arguments['understanding'], arguments['options'], turn.language)),
    Tool('start_build', 'Build the proposed feature. Only after the owner said "go".',
         {'option': {'type': 'string', 'description': 'the option the owner chose, in full'}}, ['option'], 'build',
         lambda turn, arguments: turn.builder.start(arguments['option'], turn.transcript)),
    Tool('deploy_feature', 'Install the built feature on the car. Only after the owner said "deploy". If the status line lists phrases that '
         'change meaning, read them to the owner first and set phrase_changes_confirmed only after a clear yes.',
         {'phrase_changes_confirmed': {'type': 'boolean'}}, ['phrase_changes_confirmed'], 'deploy',
         lambda turn, arguments: turn.builder.deploy(turn.transcript, arguments['phrase_changes_confirmed'])),
    Tool('car_facts', 'What is really installed on the car (measured): check it before propose_feature.', {}, [], 'read',
         lambda turn, arguments: car_facts.summary()),
    Tool('hand_to_chat', 'Write down a wish the voice build cannot do (software the car lacks, hardware, PC changes) for Claude in the chat.',
         {'title': TITLE, 'understanding': {'type': 'string'},
          'reason': {'type': 'string', 'description': 'one sentence why the chat must do it'}}, ['title', 'understanding', 'reason'], 'read',
         lambda turn, arguments: turn.builder.hand_to_chat(arguments['title'], arguments['understanding'], arguments['reason'], turn.language)),
    Tool('project_status', 'The project now: version on the car, open wishes, voice builds not reviewed, untested requirements.', {}, [], 'read',
         lambda turn, arguments: project_docs.status()),
    Tool('read_doc', 'Read a project document, whole or only the lines containing search (empty search = all): '
         'REQUIREMENTS (tests by category, and the spoken wishes under W), CHANGES, CAR_FACTS, VOICE_COMMANDS, WORKPLAN, SETUP_LOG.',
         {'name': {'type': 'string'}, 'search': {'type': 'string'}}, ['name', 'search'], 'read',
         lambda turn, arguments: project_docs.read(arguments['name'], arguments['search'])),
    Tool('rollback_version', 'Put the previous version back on the car. Only after the owner asked for it and confirmed once.', {}, [], 'deploy',
         lambda turn, arguments: turn.start_rollback(turn.transcript)),
)
BY_NAME = {tool.name: tool for tool in TOOLS}
assert len(BY_NAME) == len(TOOLS), 'a tool name is used twice'
assert all(tool.level in LEVELS for tool in TOOLS), 'a tool has an unknown side-effect level'


def schemas():
    return [{'name': tool.name, 'description': tool.description, 'eager_input_streaming': True,
             'input_schema': {'type': 'object', 'properties': tool.properties, 'required': tool.required, 'additionalProperties': False}}
            for tool in TOOLS]


def run(name, turn, arguments):
    return BY_NAME[name].run(turn, arguments)
