'''
Single owner of the car hardware: rear motors, steering servo, camera pan/tilt servos.
The classic page (views.py), the JSON API (api.py) and the ball tracker all go through this module.

Camera positions are integer PWM ticks relative to the calibrated centre
(1 tick = the smallest step the PCA9685 can make, about 0.41 degrees). +pan = left, +tilt = up.
'''
import os
import shutil
import threading
import time

from . import features

SIM = os.environ.get('PICAR_SIM') == '1'
if SIM:
    from .sim import fake_picar
    fake_picar.install()

import picar
from picar import back_wheels, front_wheels
from .driver import camera

REPO_CALIBRATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'driver', 'config')
CALIBRATION_FILE = os.path.join(features.DATA_DIR, 'calibration')

WATCHDOG_SECONDS = 0.6
SPEED_MIN, SPEED_MAX = 0, 100
CAM_STEP_MIN, CAM_STEP_MAX = 1, 100

if not os.path.exists(CALIBRATION_FILE):
    os.makedirs(features.DATA_DIR, exist_ok=True)
    shutil.copy(REPO_CALIBRATION_FILE, CALIBRATION_FILE)

picar.setup()
fw = front_wheels.Front_Wheels(debug=False, db=CALIBRATION_FILE)
bw = back_wheels.Back_Wheels(debug=False, db=CALIBRATION_FILE)
cam = camera.Camera(debug=False, db=CALIBRATION_FILE)
STEER_MIN, STEER_MAX = 90 - fw.turning_max, 90 + fw.turning_max


def _tick_range(servo):
    low_tick = servo._angle_to_analog(0)
    high_tick = servo._angle_to_analog(180)
    return (high_tick - low_tick) // 2, 180.0 / (high_tick - low_tick)


CAM_HALF_RANGE_TICKS, DEGREES_PER_TICK = _tick_range(cam.pan_servo)


def _clamp(value, low, high):
    return max(low, min(high, int(value)))


class Car(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.speed = 40
        self.drive_direction = 0
        self.steer_angle = 90
        self.steer_hold = 0
        self.pan_ticks = 0
        self.tilt_ticks = 0
        self.cam_step = 1
        self.last_drive_command = 0.0
        self.watchdog_stops = 0
        self.last_event = 'started'
        self.ready()
        threading.Thread(target=self._watchdog_loop, name='watchdog', daemon=True).start()

    # ---- rear wheels ----
    def drive(self, direction):
        with self.lock:
            self.drive_direction = _clamp(direction, -1, 1)
            self.last_drive_command = time.time()
            bw.speed = self.speed
            if self.drive_direction > 0:
                bw.forward()
            elif self.drive_direction < 0:
                bw.backward()
            else:
                bw.stop()

    def keepalive(self):
        with self.lock:
            self.last_drive_command = time.time()

    def stop(self, reason='stop'):
        with self.lock:
            self.drive_direction = 0
            bw.stop()
            self.last_event = reason

    def set_speed(self, value):
        with self.lock:
            self.speed = _clamp(value, SPEED_MIN, SPEED_MAX)
            if self.drive_direction:
                bw.speed = self.speed

    # ---- steering ----
    def set_steer(self, angle):
        with self.lock:
            self.steer_angle = _clamp(angle, STEER_MIN, STEER_MAX)
            self._apply_steer()

    def hold_steer(self, side):
        '''side -1/+1: full lock left/right while a key is held; 0: back to steer_angle.'''
        with self.lock:
            self.steer_hold = _clamp(side, -1, 1)
            self._apply_steer()

    def _apply_steer(self):
        held_angles = {-1: STEER_MIN, 1: STEER_MAX}
        fw.turn(held_angles.get(self.steer_hold, self.steer_angle))

    # ---- camera ----
    def set_cam_step(self, ticks):
        with self.lock:
            self.cam_step = _clamp(ticks, CAM_STEP_MIN, CAM_STEP_MAX)

    def move_cam(self, pan_steps=0, tilt_steps=0, step_ticks=None):
        with self.lock:
            step = self.cam_step if step_ticks is None else step_ticks
            self.set_cam(self.pan_ticks + pan_steps * step, self.tilt_ticks + tilt_steps * step)

    def set_cam(self, pan_ticks, tilt_ticks):
        with self.lock:
            self.pan_ticks = _clamp(pan_ticks, -CAM_HALF_RANGE_TICKS, CAM_HALF_RANGE_TICKS)
            self.tilt_ticks = _clamp(tilt_ticks, -CAM_HALF_RANGE_TICKS, CAM_HALF_RANGE_TICKS)
            for servo, rel_ticks in ((cam.pan_servo, self.pan_ticks), (cam.tilt_servo, self.tilt_ticks)):
                centre_tick = servo._angle_to_analog(90) + servo.offset
                servo.pwm.write(servo.channel, 0, centre_tick + rel_ticks)

    def center_cam(self):
        self.set_cam(0, 0)

    # ---- all ----
    def ready(self):
        with self.lock:
            self.stop('ready')
            self.steer_hold = 0
            self.set_steer(90)
            self.center_cam()

    def _watchdog_loop(self):
        while True:
            time.sleep(0.1)
            if not features.enabled('watchdog'):
                continue
            with self.lock:
                if self.drive_direction and time.time() - self.last_drive_command > WATCHDOG_SECONDS:
                    self.watchdog_stops += 1
                    self.stop('watchdog stop #%d' % self.watchdog_stops)
                    print('car: watchdog stopped the motors (no command for %.1f s)' % WATCHDOG_SECONDS)

    def state(self):
        with self.lock:
            return {
                'speed': self.speed, 'drive_direction': self.drive_direction,
                'steer_angle': self.steer_angle, 'steer_hold': self.steer_hold,
                'steer_min': STEER_MIN, 'steer_max': STEER_MAX,
                'pan_ticks': self.pan_ticks, 'tilt_ticks': self.tilt_ticks,
                'cam_step': self.cam_step, 'cam_limit_ticks': CAM_HALF_RANGE_TICKS,
                'degrees_per_tick': round(DEGREES_PER_TICK, 3),
                'watchdog_stops': self.watchdog_stops, 'last_event': self.last_event,
                'sim': SIM,
            }


car = Car()
