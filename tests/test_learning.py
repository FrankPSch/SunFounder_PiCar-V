'''The learning session (remote_control/learning.py) on the simulated car: tries, feedback, kept recordings, the ways
it ends, and what the word recogniser does meanwhile. Vosk and the speaker are replaced; tests/pc/test_voice_check.py
checks the real recognition.'''
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.update(PICAR_SIM='1', PICAR_DATA=tempfile.mkdtemp(prefix='picar_test_'), DJANGO_SETTINGS_MODULE='remote_control.settings')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'remote_control'))
import django  # noqa: E402
django.setup()

from remote_control import audio, features, learning, phrase_check, speaker, spoken_texts  # noqa: E402
from remote_control.car import car  # noqa: E402

LOUD_CHUNK = b'\x00\x40' * (phrase_check.CHUNK_BYTES // 2)
SESSION_TIMEOUT_S = 10


class SessionTest(unittest.TestCase):
    '''A session over the media group in German: foto, bild, aufnahme, aufnahme stopp.'''
    def setUp(self):
        features.set_enabled('watchdog', False)
        car.ready()
        self.folder = tempfile.mkdtemp(prefix='learning_')
        self.session = learning.Session(self.folder, self.folder)
        self.said, self.heard = [], []
        patches = [mock.patch.object(phrase_check, 'model', lambda model_dir: 'model'),
                   mock.patch.object(phrase_check, 'hear', self.fake_hear),
                   mock.patch.object(speaker, 'say', lambda text, language='en': self.said.append(text)),
                   mock.patch.object(speaker, 'play_tones', lambda *tones: self.feed_microphone())]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def feed_microphone(self):
        '''The microphone loop: chunks arrive once the try records.'''
        def feed():
            while self.session._chunks is None:
                time.sleep(0.001)
            for _ in range(learning.RECORD_CHUNKS):
                self.session.feed(LOUD_CHUNK)
        threading.Thread(target=feed, daemon=True).start()

    def fake_hear(self, models, language, phrase, recorded):
        '''Each try is heard as the next entry of self.answers: a phrase, '' (nothing) or None (too quiet).'''
        self.assertEqual(len(recorded), learning.RECORD_CHUNKS * phrase_check.CHUNK_BYTES)
        answer = self.answers.pop(0)
        self.heard.append(phrase)
        if callable(answer):
            answer = answer()
        return phrase_check.Hearing(language, phrase, answer or '', 1.0, -90.0 if answer is None else -20.0, {})

    def run_session(self, answers, group='media'):
        self.answers = answers
        self.session.start('de', group)
        deadline = time.time() + SESSION_TIMEOUT_S
        while self.session.active() and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.session.active())

    def test_tries_until_understood_and_keeps_every_try(self):
        self.run_session([None, 'foto', 'bild', 'rechts', 'rechts', 'rechts', 'aufnahme stopp'])
        self.assertEqual(self.heard, ['foto', 'foto', 'bild', 'aufnahme', 'aufnahme', 'aufnahme', 'aufnahme stopp'])
        self.assertEqual(self.said[:5], [spoken_texts.LEARNING['start']['de'], 'Fotos und Videos', 'foto. Macht ein Foto.', 'zu leise', 'verstanden'])
        self.assertIn('falsch verstanden: rechts', self.said)
        self.assertEqual(self.said[-1], 'Lernen beendet. 3 von 4 verstanden.')
        self.assertEqual(sorted(os.listdir(os.path.join(self.folder, 'parked', 'de'))),
                         ['aufnahme.1.wav', 'aufnahme.2.wav', 'aufnahme.3.wav', 'aufnahme_stopp.1.wav', 'bild.1.wav', 'foto.1.wav', 'foto.2.wav'])
        with open(os.path.join(self.folder, phrase_check.RESULTS_FILE), encoding='utf-8') as results:
            lines = [json.loads(line) for line in results]
        self.assertEqual([(line['file'], line['verdict']) for line in lines[:2]], [('parked/de/foto.1.wav', 'quiet'), ('parked/de/foto.2.wav', 'understood')])

    def test_three_quiet_tries_in_a_row_end_it(self):
        self.run_session([None, None, None, 'bild'])
        self.assertEqual(self.heard, ['foto'] * 3)
        self.assertEqual(self.said[-1], 'Lernen beendet. 0 von 1 verstanden.')

    def test_end_stops_after_the_current_try(self):
        def end_now():
            self.session.end()
            return 'foto'
        self.run_session([end_now, 'bild'])
        self.assertEqual(self.heard, ['foto'])
        self.assertEqual(self.said[-1], 'Lernen beendet. 1 von 1 verstanden.')

    def test_tries_while_driving_are_kept_apart(self):
        car.drive(1)
        self.addCleanup(car.stop, 'test')
        self.run_session(['foto', 'bild', 'aufnahme', 'aufnahme stopp'])
        self.assertTrue(os.path.isfile(os.path.join(self.folder, 'driving', 'de', 'foto.1.wav')))
        self.assertEqual(self.session.describe()['results'][0]['condition'], 'driving')

    def test_the_whole_list_without_the_learning_commands(self):
        phrases = [phrase for _, phrase in learning.practice_phrases('de')]
        self.assertIn('vorwärts', phrases)
        self.assertFalse({'befehle lernen', 'lernen beenden'} & set(phrases))

    def test_a_missing_model_ends_the_session(self):
        with mock.patch.object(phrase_check, 'model', mock.Mock(side_effect=Exception('no model'))):
            self.run_session([])
        self.assertEqual(self.session.status['error'], 'no model')
        self.assertEqual(self.said, ['Lernen beendet. 0 von 0 verstanden.'])


class WhileLearningTest(unittest.TestCase):
    '''While a session runs, the owner repeats phrases: only stop and "Lernen beenden" act.'''
    def setUp(self):
        features.set_enabled('watchdog', False)
        features.set_enabled('voice_commands', True)
        features.set_enabled('claude_voice', True)
        car.ready()
        speaker.speaking.clear()
        audio.microphone.words.reset_repeats()
        self.ended = []
        for patch in (mock.patch.object(learning.session, 'active', lambda: True),
                      mock.patch.object(learning.session, 'end', lambda: self.ended.append(True)),
                      mock.patch.object(audio.conversation, 'start', lambda *args: self.fail('conversation started'))):
            patch.start()
            self.addCleanup(patch.stop)

    def test_commands_do_not_run(self):
        for text in ('links', 'kamera hoch', 'hey claude', 'befehle lernen'):
            audio.microphone.words.heard('de', text)
        self.assertEqual((car.state()['steer_angle'], car.state()['tilt_ticks']), (90, 0))

    def test_stop_still_stops(self):
        car.drive(1)
        audio.microphone.words.heard('de', 'stopp')
        self.assertEqual(car.state()['drive_direction'], 0)

    def test_lernen_beenden_ends_it(self):
        audio.microphone.words.heard('de', 'lernen beenden')
        self.assertEqual(self.ended, [True])

    def test_the_microphone_runs_for_the_session(self):
        for name in audio.AUDIO_FEATURES:
            features.set_enabled(name, False)
        self.assertTrue(audio.microphone.wanted())


if __name__ == '__main__':
    unittest.main()
