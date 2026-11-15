'''The natural voice on the simulated car: every fixed text the code says is in spoken_texts.sentences() (so the PC
records it), and speaker.say plays recordings, stitched when needed, or espeak-ng when anything is missing.'''
import glob
import os
import re
import sys
import tempfile
import unittest
import wave
from unittest import mock

os.environ.update(PICAR_SIM='1', PICAR_DATA=tempfile.mkdtemp(prefix='picar_test_'), DJANGO_SETTINGS_MODULE='remote_control.settings')
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'remote_control'))
import django  # noqa: E402
django.setup()

from remote_control import features, speaker, spoken_texts, tracker, voice_cache  # noqa: E402

CAR_CODE = glob.glob(os.path.join(REPO, 'remote_control', 'remote_control', '*.py'))
SAY_LITERAL = re.compile(r'''say(?:_later)?(?:=|\()\s*(['"])(.+?)\1''')
RATE = 22050


def recording(cache_dir, language, text, seconds=0.5):
    path = voice_cache.path(cache_dir, language, text)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, 'wb') as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(RATE)
        clip.writeframes(b'\x01\x00' * int(RATE * seconds))


class CatalogTest(unittest.TestCase):
    def test_every_literal_the_car_code_says_is_recorded(self):
        said = {voice_cache.key(text) for _, text in spoken_texts.sentences()}
        missing = []
        for path in CAR_CODE:
            with open(path, encoding='utf-8') as source:
                missing += ['%s: %s' % (os.path.basename(path), text) for _, text in SAY_LITERAL.findall(source.read())
                            if '%' not in text and voice_cache.key(text) not in said]  # '%s. %s': made of catalog pieces
        self.assertEqual(missing, [])

    def test_update_script_texts_are_in_the_catalog(self):
        with open(os.path.join(REPO, 'tools', 'picar-update.sh'), encoding='utf-8') as script:
            texts = re.findall(r'^\s*say "(.+)"$', script.read(), re.M)
        self.assertEqual(sorted(texts), sorted(spoken_texts.UPDATE.values()))

    def test_natural_voice_is_on_by_default(self):
        self.assertTrue(features.FEATURES['natural_voice'][0])

    def test_treasure_colours_match_the_tracker(self):
        self.assertEqual(set(spoken_texts.TREASURE_COLORS), set(tracker.COLOR_PRESETS))

    def test_learning_and_command_texts(self):
        sentences = spoken_texts.sentences()
        for expected in (('de', 'links. Lenkt nach links.'), ('de', 'falsch verstanden'), ('de', '31'), ('de', 'von'), ('en', 'camera center'),
                         ('de', 'Lernen beendet.'), ('en', 'find something red'), spoken_texts.STARTUP):
            self.assertIn(expected, sentences)


class SayTest(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp(prefix='voice_cache_')
        self.played = []
        features.set_enabled('natural_voice', True)
        for patch in (mock.patch.object(speaker, 'CACHE_DIR', self.cache),
                      mock.patch.object(speaker, '_play', lambda audio: self.played.append(len(audio))),
                      mock.patch.object(speaker, '_espeak', lambda text, language: b'espeak:' + text.encode('utf-8')),
                      mock.patch.object(speaker, 'ECHO_S', 0)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_recorded_sentence_plays_the_recording(self):
        recording(self.cache, 'de', 'Ich bin bereit.')
        speaker.say('Ich bin bereit.', 'de')
        self.assertEqual(len(self.played), 1)
        self.assertAlmostEqual(self.played[0], 0.5 * speaker.PLAYBACK_RATE * 2, delta=16)  # rate conversion rounds

    def test_sentences_and_words_are_stitched(self):
        for text in ('Lernen beendet.', '31', 'von', '33', 'verstanden.'):
            recording(self.cache, 'de', text)
        clips = speaker.natural_clips('Lernen beendet. 31 von 33 verstanden.', 'de')
        self.assertEqual([os.path.basename(clip) for clip in clips], [voice_cache.file_name(text) for text in ('Lernen beendet', '31', 'von', '33', 'verstanden')])

    def test_anything_missing_means_espeak_for_the_whole_text(self):
        recording(self.cache, 'de', 'Lernen beendet.')
        speaker.say('Lernen beendet. 31 von 33 verstanden.', 'de')
        self.assertEqual(self.played, [len(b'espeak:Lernen beendet. 31 von 33 verstanden.')])

    def test_switch_off_means_espeak(self):
        recording(self.cache, 'de', 'Ich bin bereit.')
        features.set_enabled('natural_voice', False)
        self.addCleanup(features.set_enabled, 'natural_voice', True)
        self.assertIsNone(speaker.natural_clips('Ich bin bereit.', 'de'))
        self.assertEqual(speaker.describe()['voice'], 'espeak')

    def test_describe_counts_the_recordings(self):
        recording(self.cache, 'de', 'Ich bin bereit.')
        self.assertEqual(speaker.describe(), {'voice': 'natural', 'recordings': {'de': 1}})


if __name__ == '__main__':
    unittest.main()
