'''The car's USB sound devices (Jabra SPEAK 510: microphone and speaker in one) as ALSA names, and the mic gain.'''
import re
import subprocess

INTERNAL_AUDIO_CARDS = ('Headphones', 'vc4hdmi')
LIST_TIMEOUT_S = 5
# Capture gain set before each recording start, by ALSA card name:
MIC_MIXERS = {'Device': ('Mic', '100%'),      # old USB mic: capture volume boots at 0 % (peak 55 of 32767 measured)
              'USB': ('Headset', '100%')}     # Jabra SPEAK 510: boots at 4/7; 7/7 = +9 dB, the thresholds are measured with it


def usb_device(list_tool):
    '''First USB card as "plughw:CARD=..,DEV=..": list_tool 'arecord' = microphone, 'aplay' = speaker. None if there is none.'''
    try:
        listing = subprocess.run([list_tool, '-l'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True,
                                 timeout=LIST_TIMEOUT_S).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for card, device in re.findall(r'^card \d+: (\w+) \[.*\], device (\d+):', listing, re.MULTILINE):
        if card not in INTERNAL_AUDIO_CARDS:
            return 'plughw:CARD=%s,DEV=%s' % (card, device)
    return None


def set_mic_gain(device):
    for card, (control, value) in MIC_MIXERS.items():
        if 'CARD=%s,' % card in device:
            subprocess.run(['amixer', '-q', '-c', card, 'sset', control, value], stderr=subprocess.DEVNULL)
