'''The simulated car through its web API: integer camera steps, clamps, speed and steering, the watchdog, ball tracking.
Was tools/sim_test.py. The tests run in name order and share the one car.'''
from sim_case import SimCase, requirements

shows = requirements.shows
TRACKING_SECONDS = 20
FOUND_SHARE = 0.75          # a share, not a count: a busy PC answers the state slower, so fewer samples fit
MIN_SAMPLES = 20
CENTRED_OFFSET = 0.10
TRACK_STEP = 5


def setUpModule():
    case = SimCase()
    case.cmd('feature', name='ball_tracking', on=False)
    case.cmd('ready')
    case.cmd('cam_step', value=1)


class Car(SimCase):
    @shows(('K.command.cam_step', 'run'))
    def test_01_cam_step_changes_by_delta_and_clamps(self):
        self.assertEqual(self.cmd('cam_step', delta=1)['car']['cam_step'], 2)
        self.assertEqual(self.cmd('cam_step', delta=10)['car']['cam_step'], 12)
        self.assertEqual(self.cmd('cam_step', delta=-10)['car']['cam_step'], 2)
        self.assertEqual(self.cmd('cam_step', delta=-10)['car']['cam_step'], 1, 'clamps at 1')

    @shows(('K.command.cam_move', 'run'), ('K.command.cam_set', 'run'), ('K.command.cam_center', 'run'))
    def test_02_camera_moves_in_ticks_and_clamps(self):
        self.assertEqual(self.cmd('cam_move', pan=1)['car']['pan_ticks'], 1, 'pan +1 step of 1 tick')
        self.assertEqual(self.cmd('cam_move', tilt=-1, step=10)['car']['tilt_ticks'], -10, 'tilt -1 step of 10 ticks')
        self.assertEqual(self.cmd('cam_set', pan=999, tilt=0)['car']['pan_ticks'], 221, 'pan clamps at its limit')
        self.assertEqual(self.cmd('cam_center')['car']['pan_ticks'], 0)

    @shows(('K.command.speed', 'run'), ('K.command.steer', 'run'))
    def test_03_speed_and_steering_clamp(self):
        self.assertEqual(self.cmd('speed', value=37)['car']['speed'], 37)
        self.assertEqual(self.cmd('speed', delta=10)['car']['speed'], 47)
        self.assertEqual(self.cmd('speed', value=250)['car']['speed'], 100, 'speed clamps at 100')
        self.assertEqual(self.cmd('steer', delta=-1)['car']['steer_angle'], 89)
        self.assertEqual(self.cmd('steer', value=0)['car']['steer_angle'], 45, 'steering clamps at its minimum')
        self.cmd('steer', value=90)

    def test_04_unknown_command_refused(self):
        self.assert_refused('nope')

    @shows(('K.switch.watchdog', 'on'), ('K.command.drive', 'run'), ('K.command.keepalive', 'run'), ('K.command.stop', 'run'))
    def test_05_watchdog_stops_without_keepalive(self):
        stops_before = self.state()['car']['watchdog_stops']
        self.cmd('drive', direction=1)
        stopped = self.wait_until(lambda current: current['car']['drive_direction'] == 0, 2, 'the watchdog stop')
        self.assertEqual(stopped['car']['watchdog_stops'] - stops_before, 1)
        self.cmd('drive', direction=1)
        self.let_run(1.6, command='keepalive', every_s=0.2)
        self.assertEqual(self.state()['car']['drive_direction'], 1, 'keepalives keep it driving')
        self.cmd('stop')
        self.assertEqual(self.state()['car']['drive_direction'], 0)

    @shows(('K.switch.ball_tracking', 'on'), ('K.switch.ball_tracking', 'off'), ('K.command.track_color', 'run'), ('K.command.track_teach', 'run'))
    def test_06_camera_follows_the_ball_in_whole_steps(self):
        self.cmd('cam_center')
        self.cmd('cam_step', value=TRACK_STEP)
        self.cmd('track_color', name='red')
        self.cmd('feature', name='ball_tracking', on=True)
        looked, offsets, pans = 0, [], set()
        for current in self.samples(TRACKING_SECONDS):
            looked += 1
            pans.add(current['car']['pan_ticks'])
            tracker = current['tracker']
            if tracker.get('found'):
                offsets.append(max(abs(tracker['x'] - 0.5), abs(tracker['y'] - 0.5)))
        print('tracking: found in %d of %d samples, distinct pan values %d' % (len(offsets), looked, len(pans)))
        self.assertGreaterEqual(looked, MIN_SAMPLES)
        self.assertGreaterEqual(len(offsets), FOUND_SHARE * looked, 'ball found most of the time')
        self.assertLess(sorted(offsets)[len(offsets) // 2], CENTRED_OFFSET, 'median offset under 10 % of the image')
        self.assertTrue(all(pan % TRACK_STEP == 0 for pan in pans), 'pan moved in multiples of the step')
        self.assertEqual(self.cmd('track_teach')['tracker']['color'], 'custom')
        self.cmd('feature', name='ball_tracking', on=False)
