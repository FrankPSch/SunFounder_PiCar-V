'''The camera detectors, one module each (switch detector_registry: tracker.py runs them from DETECTORS; off: its old
fixed order calls the same functions). A detector module has
  SWITCHES        the feature switches that need it (any one on = it runs)
  KIND            'target': it finds what the camera follows (the first active one in DETECTORS wins); 'extra': it reports
  EVERY_N_FRAMES  how often it runs
  detect(tracker, frame)   a target dict (KIND 'target') or None; an 'extra' detector writes its result to tracker.status
  off(tracker)    optional: what to clear while it does not run
A new vision wish adds a module here and one entry in DETECTORS, and does not edit tracker.py.'''
from . import animal, ball, face, markers, qr, treasure
from .common import box_target  # noqa: F401

DETECTORS = (markers, qr, treasure, ball, face, animal)     # targets in this order of precedence
KINDS = ('target', 'extra')


def active(detector, enabled):
    return any(enabled(name) for name in detector.SWITCHES)


assert all(detector.KIND in KINDS and detector.SWITCHES and detector.EVERY_N_FRAMES >= 1 for detector in DETECTORS)
