'''
Stand-in for the SunFounder `picar` package when PICAR_SIM=1 (testing on a PC without the car).
Mirrors the parts of the real API this server uses; every PWM write lands in PWM_CHANNELS,
which the simulated camera (sim/world.py) reads to know where the camera points.
'''
import sys
import types

PWM_CHANNELS = {}
MOTOR_STATE = {'speed': 0, 'direction': 0}


class PWM(object):
    def __init__(self, bus_number=1, address=0x40):
        self.frequency = 60

    def setup(self):
        pass

    def write(self, channel, on, off):
        PWM_CHANNELS[channel] = off

    @staticmethod
    def map(x, in_min, in_max, out_min, out_max):
        return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min


class Servo(object):
    _MIN_PULSE_WIDTH = 600
    _MAX_PULSE_WIDTH = 2400

    def __init__(self, channel, offset=0, lock=True, bus_number=1, address=0x40):
        self.channel = channel
        self.offset = offset
        self.frequency = 60
        self.pwm = PWM()
        self.write(90)

    def _angle_to_analog(self, angle):
        pulse_wide = PWM.map(angle, 0, 180, self._MIN_PULSE_WIDTH, self._MAX_PULSE_WIDTH)
        return int(float(pulse_wide) / 1000000 * self.frequency * 4096)

    def write(self, angle):
        angle = max(0, min(180, angle))
        self.pwm.write(self.channel, 0, self._angle_to_analog(angle) + self.offset)


class fileDB(object):
    def __init__(self, db=None):
        self.db = db

    def get(self, name, default_value=None):
        try:
            with open(self.db) as conf:
                for line in conf:
                    if line.split('=')[0].strip() == name:
                        return line.split('=')[1].replace(' ', '').strip()
        except FileNotFoundError:
            pass
        return default_value

    def set(self, name, value):
        pass


class _NoopCalibration(object):
    def __getattr__(self, name):
        if name.startswith('cali'):
            return lambda *args, **kwargs: None
        raise AttributeError(name)


class Front_Wheels(_NoopCalibration):
    def __init__(self, debug=False, db='config', bus_number=1, channel=0):
        self.turning_offset = int(fileDB(db).get('turning_offset', default_value=0))
        self.wheel = Servo(channel, offset=self.turning_offset)
        self.turning_max = 45

    def turn(self, angle):
        self.wheel.write(max(90 - self.turning_max, min(90 + self.turning_max, angle)))

    def turn_left(self):
        self.turn(90 - self.turning_max)

    def turn_right(self):
        self.turn(90 + self.turning_max)

    def turn_straight(self):
        self.turn(90)

    def ready(self):
        self.turn_straight()


class Back_Wheels(_NoopCalibration):
    def __init__(self, debug=False, bus_number=1, db='config'):
        self._speed = 0

    @property
    def speed(self):
        return self._speed

    @speed.setter
    def speed(self, value):
        self._speed = value
        MOTOR_STATE['speed'] = value

    def forward(self):
        MOTOR_STATE['direction'] = 1

    def backward(self):
        MOTOR_STATE['direction'] = -1

    def stop(self):
        MOTOR_STATE['direction'] = 0

    def ready(self):
        self.stop()


def install():
    '''Register fake modules under the real names so `import picar...` resolves to this file.'''
    package = types.ModuleType('picar')
    package.setup = lambda: None
    pca = types.ModuleType('picar.SunFounder_PCA9685')
    pca.Servo = types.ModuleType('picar.SunFounder_PCA9685.Servo')
    pca.Servo.Servo = Servo
    pca.PCA9685 = types.ModuleType('picar.SunFounder_PCA9685.PCA9685')
    pca.PCA9685.PWM = PWM
    package.SunFounder_PCA9685 = pca
    package.filedb = types.ModuleType('picar.filedb')
    package.filedb.fileDB = fileDB
    package.front_wheels = types.ModuleType('picar.front_wheels')
    package.front_wheels.Front_Wheels = Front_Wheels
    package.back_wheels = types.ModuleType('picar.back_wheels')
    package.back_wheels.Back_Wheels = Back_Wheels
    for module in (package, pca, pca.Servo, pca.PCA9685, package.filedb,
                   package.front_wheels, package.back_wheels):
        sys.modules[module.__name__] = module
