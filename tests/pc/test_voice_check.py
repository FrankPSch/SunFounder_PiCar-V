'''Recognition with the car's Vosk models (copied from the car to <voice dir>/vosk_models) and Piper as the speaker:
every phrase must be understood, the verdicts, replay of kept tries, tools/voice_check.py learn against a stand-in car,
and the owner's own recordings (copied from the car to <voice dir>/recordings) once there are any.
Voice environment; run by tools/run_tests.py.'''
import http.server
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools'))
import pc_settings  # noqa: E402
import text_to_speech  # noqa: E402
import voice_check  # noqa: E402
from remote_control import phrase_check, requirements, vocabulary  # noqa: E402

MODELS = os.path.join(pc_settings.VOICE_DIR, 'vosk_models')
OWNER_RECORDINGS = os.path.join(pc_settings.VOICE_DIR, 'recordings')
SILENCE = b'\0\0' * phrase_check.SAMPLE_RATE
STUB_CAR_PORT = 18868
SYNTHESES = 2
models = None


def setUpModule():
    global models
    models = voice_check.load_models(MODELS)


def speech(phrase, language):
    return text_to_speech.synthesize(phrase, language, rate=phrase_check.SAMPLE_RATE)


class RecognitionTest(unittest.TestCase):
    @requirements.shows(('S.recognition.piper', 'de'), ('S.recognition.piper', 'en'))
    def test_every_phrase_is_understood(self):
        '''Fails for a phrase the small models do not know, or two spellings of one sound (confidence split).
        Piper varies every synthesis a little, so a phrase gets a second one: failing twice in a row is real.'''
        failures = []
        for language in vocabulary.LANGUAGES:
            for phrase in sorted(vocabulary.phrases(language)):
                for _ in range(SYNTHESES):
                    hearing = phrase_check.hear(models, language, phrase, speech(phrase, language))
                    if hearing.verdict == phrase_check.Verdict.UNDERSTOOD:
                        break
                else:
                    failures.append('%s %r: %s %r %.2f' % (language, phrase, hearing.verdict.name, hearing.heard, hearing.confidence))
        self.assertEqual(failures, [])


class VerdictTest(unittest.TestCase):
    def test_silence_is_too_quiet(self):
        self.assertEqual(phrase_check.hear(models, 'de', 'links', SILENCE).verdict, phrase_check.Verdict.QUIET)

    def test_another_command_is_wrong(self):
        hearing = phrase_check.hear(models, 'de', 'links', speech('rechts', 'de'))
        self.assertEqual((hearing.verdict, hearing.heard), (phrase_check.Verdict.OTHER, 'rechts'))
        self.assertEqual(hearing.spoken_feedback(), 'falsch verstanden: rechts')

    def test_another_phrase_of_the_same_command_counts(self):
        self.assertEqual(phrase_check.hear(models, 'de', 'halt', speech('anhalten', 'de')).verdict, phrase_check.Verdict.UNDERSTOOD)


class ReplayTest(unittest.TestCase):
    def test_kept_tries_are_heard_again(self):
        folder = tempfile.mkdtemp(prefix='voice_check_')
        for phrase, audio in (('foto', SILENCE), ('foto', speech('foto', 'de')), ('bild', speech('bild', 'de'))):
            phrase_check.save_recording(folder, 'parked', phrase_check.hear(models, 'de', phrase, audio), audio)
        with redirect_stdout(io.StringIO()):
            results = voice_check.replay(models, folder)
            self.assertTrue(voice_check.all_understood(results))
        self.assertEqual([(result['phrase'], result['verdict']) for result in results], [('foto', 'quiet'), ('foto', 'understood'), ('bild', 'understood')])

    def test_owner_recordings_understood_then_are_understood_now(self):
        '''The owner's voice as a regression test: copy the car's /home/pi/picar-data/voice_check to <voice dir>/recordings.'''
        if not phrase_check.recorded_results(OWNER_RECORDINGS):
            self.skipTest('no owner recordings in %s yet' % OWNER_RECORDINGS)
        with redirect_stdout(io.StringIO()):
            results = voice_check.replay(models, OWNER_RECORDINGS)
        lost = ['%s %s %s: now %s (%s)' % (result['condition'], result['language'], result['phrase'], result['verdict'], result['heard'])
                for result in results if result['then'] == 'understood' and result['verdict'] != 'understood']
        self.assertEqual(lost, [])


class LearnCommandTest(unittest.TestCase):
    '''voice_check.py learn against a stand-in for the car's web API.'''
    def test_starts_the_session_and_shows_every_try(self):
        commands, result = [], {'language': 'de', 'phrase': 'foto', 'heard': 'foto', 'confidence': 1.0, 'peak_dbfs': -20.0,
                                'verdict': 'understood', 'other_languages': {}, 'condition': 'parked'}
        states = [{'active': True, 'results': [result]}, {'active': False, 'results': [result, dict(result, phrase='bild', heard='bild')]}]

        class StubCar(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, body):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode('utf-8'))

            def do_POST(self):
                commands.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.reply({'ok': True})

            def do_GET(self):
                self.reply({'audio': {'learning': states.pop(0) if len(states) > 1 else states[0]}})
        stub = http.server.ThreadingHTTPServer(('127.0.0.1', STUB_CAR_PORT), StubCar)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        self.addCleanup(stub.server_close)
        self.addCleanup(stub.shutdown)
        output = io.StringIO()
        with redirect_stdout(output):
            code = voice_check.main(['learn', '--car', 'http://127.0.0.1:%d' % STUB_CAR_PORT, '--group', 'media'])
        self.assertEqual(commands, [{'cmd': 'learn', 'on': True, 'language': 'de', 'group': 'media'}])
        self.assertEqual(code, 0)
        self.assertIn('2 of 2 phrases understood', output.getvalue())


class ChecklistTest(unittest.TestCase):
    def test_every_phrase_and_explanation_listed(self):
        text = voice_check.checklist()
        for spoken in vocabulary.SPOKEN:
            self.assertIn(', '.join(spoken.de), text)
            self.assertIn(vocabulary.EXPLANATIONS[spoken.name][1], text)


if __name__ == '__main__':
    unittest.main()
