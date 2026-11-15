'''tools/voice_check.py with the car's Vosk models (copied from the car to <voice dir>/vosk_models) and Piper as the
speaker: every phrase must be understood, the verdicts, and the learning flow with microphone and speaker replaced.
Voice environment; run by tools/run_tests.py.'''
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools'))
import pc_settings  # noqa: E402
import text_to_speech  # noqa: E402
import voice_check  # noqa: E402
from remote_control import vocabulary  # noqa: E402

MODELS = os.path.join(pc_settings.VOICE_DIR, 'vosk_models')
SILENCE = b'\0\0' * voice_check.SAMPLE_RATE
recognizers = None


def setUpModule():
    global recognizers
    recognizers = voice_check.Recognizers(MODELS)


def speech(phrase, language):
    return text_to_speech.synthesize(phrase, language, rate=voice_check.SAMPLE_RATE)


class RecognitionTest(unittest.TestCase):
    def test_every_phrase_is_understood(self):
        '''Fails for a phrase the small models do not know, or two spellings of one sound (confidence split).'''
        failures = []
        for language in vocabulary.LANGUAGES:
            for phrase in sorted(vocabulary.phrases(language)):
                hearing = recognizers.hear(language, phrase, speech(phrase, language))
                if hearing.verdict != voice_check.Verdict.UNDERSTOOD:
                    failures.append('%s %r: %s %r %.2f' % (language, phrase, hearing.verdict.name, hearing.heard, hearing.confidence))
        self.assertEqual(failures, [])


class VerdictTest(unittest.TestCase):
    def test_silence_is_too_quiet(self):
        self.assertEqual(recognizers.hear('de', 'links', SILENCE).verdict, voice_check.Verdict.QUIET)

    def test_another_command_is_wrong(self):
        hearing = recognizers.hear('de', 'links', speech('rechts', 'de'))
        self.assertEqual((hearing.verdict, hearing.heard), (voice_check.Verdict.OTHER, 'rechts'))
        self.assertEqual(hearing.spoken_feedback(), 'falsch verstanden: rechts')

    def test_another_phrase_of_the_same_command_counts(self):
        self.assertEqual(recognizers.hear('de', 'halt', speech('anhalten', 'de')).verdict, voice_check.Verdict.UNDERSTOOD)


class LearnTest(unittest.TestCase):
    def test_learn_retries_and_keeps_the_good_recording(self):
        said, folder = [], tempfile.mkdtemp(prefix='voice_check_')
        takes = iter([SILENCE, speech('foto', 'de'), speech('bild', 'de')])
        with mock.patch.object(voice_check, 'service_running', lambda: False), \
                mock.patch.object(voice_check.alsa, 'usb_device', lambda tool: 'mic'), \
                mock.patch.object(voice_check.alsa, 'set_mic_gain', lambda device: None), \
                mock.patch.object(voice_check, 'record', lambda device: next(takes)), \
                mock.patch.object(voice_check.speaker, 'say', lambda text, language: said.append(text)), \
                mock.patch.object(voice_check.speaker, 'play_tones', lambda *tones: None):
            hearings = voice_check.learn(recognizers, folder, 'de', None, 'photo')
        self.assertEqual(said, ['Fotos und Videos', 'foto. Macht ein Foto.', 'zu leise', 'verstanden', 'bild. Macht ein Foto.', 'verstanden'])
        self.assertEqual([hearing.verdict for hearing in hearings], [voice_check.Verdict.UNDERSTOOD] * 2)
        replayed = voice_check.replay(recognizers, folder)
        self.assertEqual([(hearing.phrase, hearing.verdict) for hearing in replayed], [('bild', voice_check.Verdict.UNDERSTOOD),
                                                                                    ('foto', voice_check.Verdict.UNDERSTOOD)])

    def test_learn_refuses_while_the_car_service_runs(self):
        with mock.patch.object(voice_check, 'service_running', lambda: True):
            with self.assertRaises(SystemExit):
                voice_check.learn(recognizers, tempfile.mkdtemp(), 'de', None, None)


class ChecklistTest(unittest.TestCase):
    def test_every_phrase_and_explanation_listed(self):
        text = voice_check.checklist()
        for spoken in vocabulary.SPOKEN:
            self.assertIn(', '.join(spoken.de), text)
            self.assertIn(vocabulary.EXPLANATIONS[spoken.name][1], text)


if __name__ == '__main__':
    unittest.main()
