# -*- coding: utf-8 -*-
# On the car: sends a raw 16 kHz mono 16-bit file to the voice server and plays the answer on the USB speaker. Does not drive.
# Usage: python3 voice_check.py speech.raw [plughw:CARD=USB,DEV=0]
import http.client
import json
import struct
import subprocess
import sys
import time

with open(sys.argv[1], 'rb') as speech_file:
    speech = speech_file.read()
speaker = sys.argv[2] if len(sys.argv) > 2 else 'plughw:CARD=USB,DEV=0'
started = time.time()
connection = http.client.HTTPConnection('127.0.0.1', 8766, timeout=30)
connection.request('POST', '/utterance?new=1', speech, {'X-Car-Status': '{"speed": 40}'})
response = connection.getresponse()
player = subprocess.Popen(['aplay', '-q', '-D', speaker, '-t', 'raw', '-f', 'S16_LE', '-c', '1', '-r', '16000'], stdin=subprocess.PIPE)
while True:
    header = response.read(5)
    if len(header) < 5:
        break
    payload = response.read(struct.unpack('>I', header[1:])[0])
    if header[:1] == b'T':
        print('%.2f s text %s' % (time.time() - started, json.loads(payload.decode('ascii'))))
    else:
        print('%.2f s audio %.1f s' % (time.time() - started, len(payload) / 32000.0))
        player.stdin.write(payload)
player.stdin.close()
print('speaker exit code', player.wait())
