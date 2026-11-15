'''The generated project documents in project/generated/ (not in git, so voice builds and the chat never conflict over
them), and the short status Claude voice reads:
  WISHES.md          every spoken wish (wish_list.py)          CHANGES.md   git history: source, deploy tags, reviews
  CAR_FACTS.md       measured on the car (car_facts.py)        VOICE_COMMANDS.md  every spoken command
  REQUIREMENTS.md    requirements = tests (requirements_report.py, refreshed with the newest real-car evidence)
Rebuilt by the voice server at start, after every deploy, and by hand:  python tools/project_docs.py'''
import os
import subprocess

import car_facts
import pc_settings
import release
import requirements_report
import voice_check
import wish_list

GENERATED = os.path.join(pc_settings.REPO, 'project', 'generated')
DOCUMENTS = ('WISHES', 'REQUIREMENTS', 'CHANGES', 'CAR_FACTS', 'VOICE_COMMANDS', 'WORKPLAN', 'SETUP_LOG')
HAND_WRITTEN = {'WORKPLAN': os.path.join(pc_settings.REPO, 'project', 'WORKPLAN.md'), 'SETUP_LOG': os.path.join(pc_settings.REPO, 'project', 'SETUP_LOG.md')}
CHANGES_SHOWN = 60
OPEN_WISH_STATES = ('proposed', 'building', 'built', 'for_chat', 'failed', 'deploying', 'deploy_failed')


def document_path(name):
    return HAND_WRITTEN.get(name) or os.path.join(GENERATED, name + '.md')


def wishes():
    return wish_list.WishList(pc_settings.WISHES_DIR, os.path.join(GENERATED, 'WISHES.md'))


def changes():
    '''[{commit, short, date, subject, source, tags, reviewed}] newest first, from origin/master.'''
    log = subprocess.run(['git', 'log', '-n', str(CHANGES_SHOWN), '--date=format:%Y-%m-%d %H:%M', '--format=%H\t%h\t%ad\t%s', 'origin/master'],
                         cwd=pc_settings.REPO, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, encoding='utf-8').stdout
    tags, done = {}, release.reviews()
    try:
        for name, commit in release.deploy_tags():
            tags.setdefault(commit, []).append(name)
    except release.ReleaseError:
        pass
    rows = []
    for line in log.splitlines():
        commit, short, date, subject = line.split('\t', 3)
        source = 'voice' if subject.startswith('Voice feature') else 'chat'
        rows.append({'commit': commit, 'short': short, 'date': date, 'subject': subject, 'source': source, 'tags': tags.get(commit, []),
                     'reviewed': source == 'chat' or commit in done})
    return rows


def _changes_markdown(rows, running):
    lines = ['# PiCar changes', '', 'Generated from git (origin/master) by tools/project_docs.py. Voice builds stay "not reviewed" until the chat',
             'checked them on the real car (python tools/release.py review <commit>). The car runs %s.' % (running or 'an unknown commit'), '',
             '| date | commit | source | change | deployed as | reviewed |', '|---|---|---|---|---|---|']
    for row in rows:
        mark = ' **← car**' if running and row['short'].startswith(running) else ''
        lines.append('| %s | %s%s | %s | %s | %s | %s |' % (row['date'], row['short'], mark, row['source'], row['subject'].replace('|', '/'),
                                                          ', '.join(row['tags']) or '–', 'yes' if row['reviewed'] else '**no**'))
    return '\n'.join(lines) + '\n'


def _facts_markdown(facts):
    if not facts:
        return '# Car facts\n\nNot measured yet (python tools/car_facts.py).\n'
    return '# Car facts\n\n' + car_facts.summary(facts, with_packages=True).replace('\n- ', '\n\n- ') + '\n'


def build(log=print):
    os.makedirs(GENERATED, exist_ok=True)
    facts = car_facts.load()
    running = (facts or {}).get('commit', '').split(' ')[0]
    written = {'CHANGES': _changes_markdown(changes(), running), 'CAR_FACTS': _facts_markdown(facts),
               'VOICE_COMMANDS': voice_check.checklist() + '\n', 'WISHES': wishes().markdown()}
    for name, text in written.items():
        with open(document_path(name), 'w', encoding='utf-8') as document:
            document.write(text)
    if os.path.isfile(requirements_report.CATALOG_FILE):
        requirements_report.build()
    log('project documents rebuilt in ' + GENERATED)


def read(name, search=''):
    '''A document for Claude voice: whole (cut to a size it can speak about) or only the lines with search.'''
    if name not in DOCUMENTS:
        return 'Unknown document. Known: ' + ', '.join(DOCUMENTS)
    try:
        with open(document_path(name), encoding='utf-8') as document:
            text = document.read()
    except OSError:
        return '%s does not exist yet.' % name
    if search:
        found = [line for line in text.splitlines() if search.lower() in line.lower()]
        text = '\n'.join(found) or 'Nothing in %s mentions "%s".' % (name, search)
    return text[:6000]


def status():
    '''The project status in a few lines, for Claude voice (project_status) and the chat.'''
    facts = car_facts.load() or {}
    rows = changes()
    unreviewed = [row['subject'] for row in rows if not row['reviewed']]
    open_wishes = ['%s (%s)' % (wish.get('title', wish_id), wish['state']) for wish_id, wish in wishes().wishes().items() if wish['state'] in OPEN_WISH_STATES]
    last_tag = next((row['tags'][-1] for row in rows if row['tags']), 'none')
    return '\n'.join(['Car runs: %s (measured %s). Last deploy tag: %s.' % (facts.get('commit', 'unknown'), facts.get('measured', 'never'), last_tag),
                      'Open wishes: %s.' % ('; '.join(open_wishes) or 'none'),
                      'Voice builds not reviewed by the chat: %s.' % ('; '.join(unreviewed) or 'none'),
                      'Requirements: ' + requirements_report.summary()])


if __name__ == '__main__':
    build()
    print(status())
