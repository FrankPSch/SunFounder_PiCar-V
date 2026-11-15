'''Recordings of spoken_texts.sentences() in the natural Piper voice, made on the PC by tools/prerender_voice.py:
  <cache>/<language>/<file_name(text)>   16-bit mono wav at Piper's own rate
clips() finds the recordings for a text: the whole text, else its sentences ("Lernen beendet." + "31 von 33 verstanden."),
a sentence not recorded as a whole from its words ("31" "von" "33" "verstanden"). None when anything is missing:
then the car speaks the whole text with espeak-ng, never half and half. No hardware here.'''
import hashlib
import os
import re

SENTENCE_END = re.compile(r'(?<=[.:!?])\s+')
EDGE_PUNCTUATION = '.,:;!?"\' '


def key(text):
    '''The words that matter: case, spaces and punctuation at the ends do not change the recording.'''
    return ' '.join(text.split()).strip(EDGE_PUNCTUATION).lower()


def file_name(text):
    return hashlib.sha1(key(text).encode('utf-8')).hexdigest()[:20] + '.wav'


def path(cache_dir, language, text):
    return os.path.join(cache_dir, language, file_name(text))


def clips(cache_dir, language, text):
    '''Recording paths that say text in order, or None.'''
    found = []
    for sentence in [text] if os.path.isfile(path(cache_dir, language, text)) else SENTENCE_END.split(text.strip()):
        if os.path.isfile(path(cache_dir, language, sentence)):
            found.append(path(cache_dir, language, sentence))
            continue
        words = [path(cache_dir, language, word) for word in sentence.split() if key(word)]
        if not words or not all(os.path.isfile(word) for word in words):
            return None
        found += words
    return found or None
