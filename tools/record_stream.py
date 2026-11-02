# Usage: python tools/record_stream.py [seconds] [output.mjpg]   (runs ON THE PC, no extra packages)
"""Record the car's camera stream onto this PC as-is (.mjpg). Convert with: ffmpeg -i video.mjpg video.mp4"""
import sys
import time
import urllib.request

STREAM_URL = 'http://192.168.178.131:8080/?action=stream'
seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30
output = sys.argv[2] if len(sys.argv) > 2 else time.strftime('picar-%Y%m%d-%H%M%S.mjpg')
written, end = 0, time.time() + seconds
with urllib.request.urlopen(STREAM_URL, timeout=5) as stream, open(output, 'wb') as video:
    while time.time() < end:
        chunk = stream.read(16384)
        if not chunk:
            break
        video.write(chunk)
        written += len(chunk)
print('saved %s: %.1f MB in %.0f s' % (output, written / 1e6, seconds))
