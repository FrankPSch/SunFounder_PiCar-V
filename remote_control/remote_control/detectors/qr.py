'''QR codes (switch qr_reader), every third frame: a new text is an event, and spoken.'''
import time

import cv2

from .. import events, spoken_texts

SWITCHES, KIND, EVERY_N_FRAMES = ('qr_reader',), 'extra', 3


def detect(tracker, frame):
    text, points, _ = cv2.QRCodeDetector().detectAndDecode(frame)
    if text and (not tracker.status['qr'] or tracker.status['qr']['text'] != text):
        events.add('vision', 'QR code: ' + text, say=spoken_texts.EVENTS['qr_code'])
    if text:
        tracker.status['qr'] = {'text': text, 'time': time.strftime('%H:%M:%S')}
