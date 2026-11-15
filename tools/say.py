'''On the car, as pi: says one text like the car software does (speaker.say: the natural voice when recorded, else
espeak-ng). For tools/picar-update.sh, which speaks while the car software is stopped.
Usage: python3 tools/say.py de "Update fertig."'''
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'remote_control'))
from remote_control import speaker  # noqa: E402

if __name__ == '__main__':
    speaker.say(sys.argv[2], sys.argv[1])
