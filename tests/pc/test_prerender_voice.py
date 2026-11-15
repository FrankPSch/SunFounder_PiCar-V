'''tools/prerender_voice.py: records only what is missing, with real Piper, and copies to the car only the files it
does not have (the car side replaced by a stand-in). Voice environment; run by tools/run_tests.py.'''
import io
import os
import sys
import tarfile
import tempfile
import unittest
import wave
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools'))
import prerender_voice  # noqa: E402
from remote_control import voice_cache  # noqa: E402

SENTENCES = {('de', 'Ich bin bereit.'), ('de', 'verstanden'), ('en', 'camera center')}


class FakeUntar(object):
    '''ssh ... tar xf -: keeps what it was sent.'''
    def __init__(self, *args, **kwargs):
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None
        FakeUntar.last = self

    def wait(self, timeout=None):
        self.stdin.seek(0)
        with tarfile.open(fileobj=self.stdin, mode='r|') as archive:
            FakeUntar.names = sorted(member.name for member in archive)
        return 0


class PrerenderTest(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp(prefix='voice_cache_')
        for patch in (mock.patch.object(prerender_voice, 'LOCAL_CACHE', self.cache),
                      mock.patch.object(prerender_voice.spoken_texts, 'sentences', lambda: SENTENCES)):
            patch.start()
            self.addCleanup(patch.stop)

    def test_records_only_what_is_missing(self):
        self.assertEqual(prerender_voice.render_missing(lambda text: None), 3)
        self.assertEqual(prerender_voice.render_missing(lambda text: None), 0)
        with wave.open(voice_cache.path(self.cache, 'de', 'Ich bin bereit.'), 'rb') as recording:
            self.assertEqual((recording.getframerate(), recording.getnchannels()), (prerender_voice.VOICE_RATE, 1))
            self.assertGreater(recording.getnframes() / float(prerender_voice.VOICE_RATE), 0.5)

    def test_copies_only_what_the_car_lacks(self):
        prerender_voice.render_missing(lambda text: None)
        on_car = 'de:\n%s\n\nen:\n' % voice_cache.file_name('Ich bin bereit.')
        with mock.patch.object(prerender_voice, '_ssh', lambda command, **options: mock.Mock(returncode=0, stdout=on_car)), \
                mock.patch.object(prerender_voice.subprocess, 'Popen', FakeUntar):
            self.assertEqual(prerender_voice.copy_missing_to_car(lambda text: None), 2)
        self.assertEqual(FakeUntar.names, sorted(['de/' + voice_cache.file_name('verstanden'), 'en/' + voice_cache.file_name('camera center')]))

    def test_car_not_reachable(self):
        with mock.patch.object(prerender_voice, '_ssh', lambda command, **options: mock.Mock(returncode=255, stdout='')):
            self.assertIsNone(prerender_voice.copy_missing_to_car(lambda text: None))


if __name__ == '__main__':
    unittest.main()
