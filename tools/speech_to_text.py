'''Whisper on the PC's GPU: the car's 16 kHz utterance -> text and language.'''
import os

import pc_settings  # before faster_whisper: puts the CUDA DLLs on PATH
import numpy as np
from faster_whisper import WhisperModel

WHISPER_SIZE = os.environ.get('WHISPER_SIZE', 'small')  # GTX 1070: small 0.8 s, medium 1.5 s for a 4.5 s sentence
NOT_SPEECH_PROBABILITY = 0.6
FULL_SCALE = 32768.0
GERMAN = 'de'
model = WhisperModel(WHISPER_SIZE, device='cuda', compute_type='int8_float32', download_root=os.path.join(pc_settings.VOICE_DIR, 'models'),
                     local_files_only=True)


def _whisper(samples, language=None, initial_prompt=None):
    segments, info = model.transcribe(samples, language=language, beam_size=1, vad_filter=True, hotwords=None if initial_prompt else 'Claude',
                                      initial_prompt=initial_prompt, condition_on_previous_text=False)
    return ' '.join(segment.text.strip() for segment in segments if segment.no_speech_prob < NOT_SPEECH_PROBABILITY), info.language


def transcribe(audio, languages=pc_settings.voice_protocol.LANGUAGES, awaited=None):
    '''languages: the car's setting voice_languages; with one language Whisper does not guess (it took the owner's
    "Wie geht es dir?" for English "You get to do that?", 2026-11-14).
    awaited: (pattern, example) when a word is awaited ("go", "deploy"). A German "go" often comes out as "Co." or "Boom";
    a second German pass primed with the example hears it (measured: every "go" phrase found, "Cool"/"Gut" not taken).'''
    samples = np.frombuffer(audio, np.int16).astype(np.float32) / FULL_SCALE
    text, language = _whisper(samples, languages[0] if len(languages) == 1 else None)
    if language not in languages:  # short sentences are sometimes taken for a third language
        language = languages[0]
        text, _ = _whisper(samples, language)
    if awaited and text and not awaited[0].search(text):
        primed, _ = _whisper(samples, GERMAN, awaited[1])
        if awaited[0].search(primed):
            return primed, language
    return text, language
