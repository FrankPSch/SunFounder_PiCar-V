'''Treasure hunt (switch treasure_hunt): the named colour found and centred for TREASURE_FRAMES frames = a point.'''
from . import ball
from .. import events, spoken_texts

SWITCHES, KIND, EVERY_N_FRAMES = ('treasure_hunt',), 'target', 1
TREASURE_FRAMES = 5
TREASURE_MIN_RADIUS_PX = 8
CENTRED = 0.12


def detect(tracker, frame):
    treasure = tracker.status['treasure']
    if not treasure['color']:
        tracker.next_treasure()
    target = ball.detect_color(tracker, frame, treasure['color'])
    if target and target['radius_px'] >= TREASURE_MIN_RADIUS_PX and abs(target['x'] - 0.5) < CENTRED and abs(target['y'] - 0.5) < CENTRED:
        treasure['progress'] += 1
        if treasure['progress'] >= TREASURE_FRAMES:
            treasure['score'] += 1
            events.add('treasure', 'found %s! score %d' % (treasure['color'], treasure['score']), say=spoken_texts.EVENTS['treasure_found'])
            tracker.next_treasure()
    else:
        treasure['progress'] = 0
    return target
