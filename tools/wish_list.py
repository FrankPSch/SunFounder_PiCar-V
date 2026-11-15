'''Every feature wish spoken to Claude on the car, kept for good, so a chat on the PC can follow them:
  <voice dir>/wishes.jsonl            one line per step of a wish (append only, the data)
  project/generated/REQUIREMENTS.md    section "W Spoken wishes": every wish in its latest state (requirements_report.py),
                                       rebuilt after each step through on_change
Steps (feature_builder.Builder): proposed, building, built, failed, deploying, deployed, deploy_failed, and dropped
(a new wish replaced one still waiting for "go").'''
import json
import os
import threading
import time
import uuid

WISHES_FILE = 'wishes.jsonl'
STEP_FIELDS = ('title', 'understanding', 'options', 'language', 'option', 'branch', 'feature', 'detail')


def new_id():
    '''When it was spoken, and unique even for two wishes in one second.'''
    return '%s-%s' % (time.strftime('%Y%m%d-%H%M%S'), uuid.uuid4().hex[:4])


class WishList(object):
    def __init__(self, folder, on_change=None):
        self.folder = folder
        self._on_change = on_change    # called after each step, e.g. to rebuild REQUIREMENTS.md
        self._lock = threading.Lock()

    def record(self, wish_id, state, **fields):
        '''One step of a wish: its new state and whatever became known (STEP_FIELDS).'''
        unknown = set(fields) - set(STEP_FIELDS)
        assert not unknown, 'unknown wish fields: %s' % unknown
        line = dict(fields, id=wish_id, state=state, time=time.strftime('%Y-%m-%d %H:%M:%S'))
        with self._lock:
            os.makedirs(self.folder, exist_ok=True)
            with open(os.path.join(self.folder, WISHES_FILE), 'a', encoding='utf-8') as wishes:
                wishes.write(json.dumps(line, ensure_ascii=False) + '\n')
        if self._on_change:
            self._on_change()

    def wishes(self):
        '''{id: wish} in the order they were first spoken: the latest value of every field, and the history of states.'''
        path = os.path.join(self.folder, WISHES_FILE)
        if not os.path.isfile(path):
            return {}
        wishes = {}
        with open(path, encoding='utf-8') as lines:
            for line in (json.loads(text) for text in lines if text.strip()):
                wish = wishes.setdefault(line['id'], {'history': []})
                wish.update((key, value) for key, value in line.items() if key in STEP_FIELDS + ('state',))
                wish['history'].append((line['time'], line['state']))
        return wishes

    def markdown(self):
        '''The section "W Spoken wishes" of REQUIREMENTS.md: newest first.'''
        lines = ['## W Spoken wishes', '', 'Feature wishes spoken to Claude on the car, newest first. Written by tools/feature_builder.py;',
                 'the steps are in wishes.jsonl. "go" + option builds a wish, "deploy" installs it on the car, "for_chat" goes to the chat.']
        for wish_id, wish in reversed(list(self.wishes().items())):
            lines += ['', '### %s: %s' % (wish.get('title', wish_id), wish['state']), '',
                      '- Spoken %s, last step %s (id %s)' % (wish['history'][0][0], wish['history'][-1][0], wish_id)]
            if wish.get('understanding'):
                lines.append('- Understood: %s' % wish['understanding'])
            lines += ['- Option %d: %s%s' % (number, option, '  **(chosen)**' if option == wish.get('option') else '')
                      for number, option in enumerate(wish.get('options', []), 1)]
            if wish.get('option') and wish['option'] not in wish.get('options', []):
                lines.append('- Chosen: %s' % wish['option'])
            for label, key in (('Branch', 'branch'), ('Switch', 'feature'), ('Detail', 'detail')):
                if wish.get(key):
                    lines.append('- %s: %s' % (label, wish[key]))
            lines.append('- Steps: %s' % ', '.join('%s %s' % (state, step_time[11:16]) for step_time, state in wish['history']))
        return '\n'.join(lines) + '\n'
