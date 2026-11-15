'''The conversation with Claude: one history (the car has one microphone), streamed sentence by sentence, with the
tools the car offers. CLAUDE_VOICE_MODEL=echo repeats the owner's sentence instead (tests, no API key).'''
import json
import os
import re
import threading

import pc_settings  # noqa: F401  before anthropic: puts the ssl DLLs on PATH
import anthropic

MODEL = os.environ.get('CLAUDE_VOICE_MODEL', 'claude-opus-5-5')
ECHO = 'echo'
EFFORT = os.environ.get('CLAUDE_VOICE_EFFORT', 'low')  # empty for a model without the effort setting
MAX_TOKENS = 4000           # thinking counts too
MAX_HISTORY_MESSAGES = 80
SENTENCE_END = re.compile(r'(?<=[.!?:;])\s+')
SYSTEM_PROMPT = (
    'You are the voice of a small robot car (SunFounder PiCar-V) talking with its owner in the room. '
    'Everything you write is read aloud by a speech synthesizer, so answer in one or two short spoken sentences, '
    'in the language the owner used (German or English). No lists, markdown, emoji or abbreviations. '
    'What the owner says comes from speech recognition and can contain mistakes; go by the likely meaning. '
    'Each message starts with the current car status in square brackets. Mention it only when it matters. '
    'You cannot make the car drive: the owner steers with single spoken words such as '
    '"links", "rechts", "vorwärts" and "stopp". Say so if asked to drive. '
    'You can run the car\'s other spoken commands (take a photo, move the camera, read out the help, ...) with run_command; '
    'the status line lists them. '
    'You can switch the car\'s features on and off with set_feature. Before switching on a feature that makes the car drive '
    '(tracking_drive, room_navigation), ask once whether the car has room. '
    'New features: when the owner asks for something the car cannot do yet, say in one or two sentences what you understood, '
    'offer two or three short numbered options for how to build it, call propose_feature, and ask the owner to say "go" with '
    'the number. Call start_build only after the owner said "go". When a build is ready and the owner says "deploy", '
    'call deploy_feature. The status line tells you the state of the current feature request.')
CLAUDE_MD = os.path.join(pc_settings.REPO, 'CLAUDE.md')
CAR_SECTION = '## For Claude on the car'


def project_instructions():
    '''The section of CLAUDE.md meant for Claude voice: how we work, read at every start of the voice server.'''
    try:
        with open(CLAUDE_MD, encoding='utf-8') as claude_md:
            text = claude_md.read()
    except OSError:
        return ''
    section = text.split(CAR_SECTION, 1)[1] if CAR_SECTION in text else ''
    return section.split('\n## ', 1)[0].strip()


INSTRUCTIONS = SYSTEM_PROMPT + ('\n\nProject rules (from CLAUDE.md):\n' + project_instructions() if project_instructions() else '')
SPOKEN_ERROR = {'de': 'Ich kann Claude gerade nicht erreichen.', 'en': 'I cannot reach Claude right now.'}
SPOKEN_REFUSAL = {'de': 'Dazu kann ich nichts sagen.', 'en': 'I cannot help with that.'}


def _tool(name, description, properties, required):
    return {'name': name, 'description': description, 'eager_input_streaming': True,
            'input_schema': {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}}


TOOLS = [
    _tool('set_feature', 'Switch one of the car\'s features on or off. The names and current states are in the status line.',
          {'name': {'type': 'string'}, 'on': {'type': 'boolean'}}, ['name', 'on']),
    _tool('run_command', 'Run one of the car\'s spoken commands that do not drive it. The names are in the status line.',
          {'name': {'type': 'string'}}, ['name']),
    _tool('propose_feature', 'Save a new feature request with your understanding and the options, before asking for "go".',
          {'title': {'type': 'string', 'description': 'two to five words'}, 'understanding': {'type': 'string'},
           'options': {'type': 'array', 'items': {'type': 'string'}, 'description': 'two or three ways to build it'}},
          ['title', 'understanding', 'options']),
    _tool('start_build', 'Build the proposed feature. Only after the owner said "go".',
          {'option': {'type': 'string', 'description': 'the option the owner chose, in full'}}, ['option']),
    _tool('deploy_feature', 'Install the built feature on the car. Only after the owner said "deploy".', {}, []),
    _tool('car_facts', 'What is really installed on the car (measured): check it before propose_feature.', {}, []),
    _tool('hand_to_chat', 'Write down a wish the voice build cannot do (software the car lacks, hardware, PC changes) for Claude in the chat.',
          {'title': {'type': 'string', 'description': 'two to five words'}, 'understanding': {'type': 'string'},
           'reason': {'type': 'string', 'description': 'one sentence why the chat must do it'}}, ['title', 'understanding', 'reason']),
]
TOOL_TYPES = {tool['name']: {key: value['type'] for key, value in tool['input_schema']['properties'].items()} for tool in TOOLS}
JSON_TYPES = {'string': str, 'boolean': bool, 'array': list}


class Refused(Exception):
    pass


def valid_tool_input(name, arguments):
    '''Tool input streams unchecked (eager_input_streaming): check it here before running the tool.'''
    types = TOOL_TYPES.get(name)
    return (types is not None and isinstance(arguments, dict) and set(arguments) == set(types)
            and all(isinstance(arguments[key], JSON_TYPES[kind]) for key, kind in types.items()))


class Chat(object):
    def __init__(self):
        self.client = None if MODEL == ECHO else anthropic.Anthropic()
        self.history = []
        self.lock = threading.Lock()  # hold it for one whole turn

    def forget(self):
        del self.history[:]

    def sentences(self, status_line, transcript, run_tool):
        '''Claude's answer to one turn, sentence by sentence; runs the tools it calls. Raises Refused, anthropic.AnthropicError.'''
        if self.client is None:
            yield transcript
            return
        turn = self.history + [{'role': 'user', 'content': status_line + transcript}]
        for sentence in self._stream(turn, run_tool):
            yield sentence
        self.history[:] = turn  # append-only, so the thinking blocks in it stay valid
        while len(self.history) > MAX_HISTORY_MESSAGES or (self.history and not isinstance(self.history[0]['content'], str)):
            del self.history[0]

    def _stream(self, turn, run_tool):
        options = {'output_config': {'effort': EFFORT}} if EFFORT else {}
        while True:
            with self.client.messages.stream(model=MODEL, max_tokens=MAX_TOKENS, system=INSTRUCTIONS, messages=turn, tools=TOOLS,
                                             cache_control={'type': 'ephemeral'}, **options) as stream:
                unfinished = ''
                for text in stream.text_stream:
                    parts = SENTENCE_END.split(unfinished + text)
                    unfinished = parts.pop()
                    for sentence in parts:
                        yield sentence
                if unfinished.strip():
                    yield unfinished
                message = stream.get_final_message()
            if message.stop_reason == 'refusal':
                raise Refused()
            turn.append({'role': 'assistant', 'content': message.content})
            if message.stop_reason != 'tool_use':  # max_tokens with a cut-off tool call: do not run it
                return
            turn.append({'role': 'user', 'content': [self._tool_result(block, run_tool) for block in message.content if block.type == 'tool_use']})

    @staticmethod
    def _tool_result(block, run_tool):
        if valid_tool_input(block.name, block.input):
            return {'type': 'tool_result', 'tool_use_id': block.id, 'content': run_tool(block.name, block.input)}
        return {'type': 'tool_result', 'tool_use_id': block.id, 'is_error': True, 'content': json.dumps({'INVALID_JSON': json.dumps(block.input)})}
