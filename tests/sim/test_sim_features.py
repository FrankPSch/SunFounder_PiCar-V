'''The simulated car's switches and what they do: camera-follow exclusivity, media, QR codes, room markers and navigation,
treasure hunt, the twin's microphone, speaker, sensors and file owners, voice pauses, the dashboard, the natural voice.
Was tools/sim_test_features.py. The tests run in name order and share the one car. A new switch gets its own test here:
switch it on and off through the API and check what it does, without making the car drive.'''
import ast
import json
import math
import os
import re
import struct
import urllib.error
import wave

from sim_case import DATA, REPO, SimCase, requirements

shows = requirements.shows
SYSTEM_SWITCHES = ('watchdog', 'telemetry', 'power_warning', 'stuck_detection')
FEATURES_SOURCE = os.path.join(REPO, 'remote_control', 'remote_control', 'features.py')
TREASURE_COLOURS = ('red', 'orange', 'yellow', 'green', 'blue', 'purple')
TREASURE_TRIES = 12
LOUD_DBFS = -20
MIC_RATE = 16000
SERVICES_SOURCE = os.path.join(REPO, 'remote_control', 'remote_control', 'services.py')


def registry_names():
    registry = next(node.value for node in ast.walk(ast.parse(open(FEATURES_SOURCE, encoding='utf-8').read()))
                    if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == 'FEATURES')
    return [key.value for key in registry.keys]


def service_names():
    '''services.SERVICES, read from the source (importing the car's modules here would write to the twin's data).'''
    return next(ast.literal_eval(node.value) for node in ast.walk(ast.parse(open(SERVICES_SOURCE, encoding='utf-8').read()))
                if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == 'SERVICES')


def setUpModule():
    case = SimCase()
    for feature in case.state()['features']:
        if feature['name'] not in SYSTEM_SWITCHES:
            case.cmd('feature', name=feature['name'], on=False)
    case.cmd('cam_center')


def write_loud_recording(path, seconds=1):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    frames = MIC_RATE * seconds
    with wave.open(path, 'wb') as loud:
        loud.setnchannels(1)
        loud.setsampwidth(2)
        loud.setframerate(MIC_RATE)
        loud.writeframes(struct.pack('<%dh' % frames, *(int(16000 * math.sin(2 * math.pi * 440 * index / MIC_RATE)) for index in range(frames))))


class Features(SimCase):
    def test_01_every_switch_of_the_registry_is_served(self):
        self.assertEqual([feature['name'] for feature in self.state()['features']], registry_names())

    @shows(('K.switch.face_tracking', 'on'), ('K.switch.animal_tracking', 'on'), ('K.switch.animal_tracking', 'off'), ('K.command.track_animal', 'run'))
    def test_02_one_camera_follow_mode_at_a_time(self):
        self.cmd('feature', name='ball_tracking', on=True)
        current = self.cmd('feature', name='face_tracking', on=True)
        self.assertEqual((self.enabled(current, 'face_tracking'), self.enabled(current, 'ball_tracking')), (True, False), 'face switches ball off')
        current = self.cmd('feature', name='animal_tracking', on=True)
        self.assertEqual((self.enabled(current, 'animal_tracking'), self.enabled(current, 'face_tracking')), (True, False), 'animal switches face off')
        current = self.wait_until(lambda state: state['tracker']['detectors'].get('animal'), what='the animal detector status')
        self.assertIn('missing', current['tracker']['detectors']['animal'], 'a missing model is reported, no crash')
        self.assertIsNone(current['tracker']['error'], 'the vision loop has no error')
        self.cmd('track_animal', name='cat')
        self.assertEqual(self.state()['tracker']['animal'], 'cat')
        self.cmd('feature', name='animal_tracking', on=False)

    @shows(('K.command.photo', 'run'), ('K.command.record', 'run'), ('K.switch.timelapse', 'on'), ('K.switch.timelapse', 'off'),
           ('K.setting.timelapse_seconds', 'set'))
    def test_03_photos_videos_timelapse(self):
        self.cmd('photo')
        photo_name = self.state()['media']['files'][0]['name']
        self.assertTrue(photo_name.startswith('photo-') and photo_name.endswith('.jpg'), photo_name)
        self.assertEqual(self.get('/media/' + photo_name)[:3], b'\xff\xd8\xff', 'the photo downloads as JPEG')
        with self.assertRaises(urllib.error.HTTPError) as refused:
            self.get('/media/../features.json')
        self.assertEqual(refused.exception.code, 404, 'path traversal refused')
        self.cmd('record', on=True)
        self.assertTrue(self.wait_until(lambda state: state['media']['recording'], what='the recording')['media']['recording'])
        self.let_run(2.5)           # a video of some length
        self.cmd('record', on=False)
        video = next(item for item in self.state()['media']['files'] if item['name'].endswith('.mjpg'))
        self.assertGreater(video['bytes'], 20000, 'the video file has data')
        self.cmd('setting', name='timelapse_seconds', value=3)
        self.cmd('feature', name='timelapse', on=True)
        self.wait_until(lambda state: any(item['name'].startswith('timelapse-') for item in state['media']['files']), 10, 'a timelapse photo')
        self.cmd('feature', name='timelapse', on=False)

    @shows(('K.switch.qr_reader', 'on'), ('K.switch.qr_reader', 'off'))
    def test_04_qr_code_read(self):
        self.cmd('feature', name='qr_reader', on=True)
        current = self.wait_until(lambda state: state['tracker']['qr'], what='a QR code')
        self.assertEqual(current['tracker']['qr'].get('text'), 'picar-sim-qr')
        self.cmd('feature', name='qr_reader', on=False)

    @shows(('K.switch.markers', 'on'), ('K.switch.markers', 'off'), ('K.switch.room_navigation', 'on'), ('K.switch.room_navigation', 'off'),
           ('K.command.room_name', 'run'), ('K.command.room_goto', 'run'), ('K.command.room_cancel', 'run'))
    def test_05_markers_rooms_and_navigation(self):
        self.cmd('feature', name='markers', on=True)
        current = self.wait_until(lambda state: state['tracker']['markers'], what='a marker')
        self.assertEqual([marker['id'] for marker in current['tracker']['markers']], [3])
        self.cmd('room_name', id=3, name='kitchen')
        self.wait_until(lambda state: state['rooms']['current']['room'] == 'kitchen', what='the current room')
        self.assert_refused('room_goto', name='kitchen')        # room_navigation is off
        self.cmd('feature', name='room_navigation', on=True)
        self.cmd('room_goto', name='kitchen')
        self.wait_until(lambda state: state['rooms']['nav']['state'] == 'approach', what='the approach')
        # driving starts with the next camera frame after "approach"
        current = self.wait_until(lambda state: state['car']['drive_direction'] == 1, what='driving to the marker')
        self.assertEqual(current['car']['speed'], 25, 'slowly')
        self.cmd('room_cancel')
        current = self.state()
        self.assertEqual((current['rooms']['nav']['state'], current['car']['drive_direction']), ('idle', 0), 'cancel stops the car')
        self.cmd('feature', name='room_navigation', on=False)
        self.cmd('feature', name='markers', on=False)

    @shows(('K.switch.treasure_hunt', 'on'), ('K.switch.treasure_hunt', 'off'), ('K.command.treasure_next', 'run'))
    def test_06_treasure_hunt_scores_the_red_ball(self):
        self.cmd('feature', name='treasure_hunt', on=True)
        first = self.wait_until(lambda state: state['tracker']['treasure']['color'], what='a treasure colour')['tracker']['treasure']['color']
        self.assertIn(first, TREASURE_COLOURS)
        self.cmd('treasure_next')
        self.assertNotEqual(self.state()['tracker']['treasure']['color'], first)
        for _ in range(TREASURE_TRIES):         # the sim ball is red: steer the game to red and expect a point
            if self.state()['tracker']['treasure']['color'] == 'red':
                break
            self.cmd('treasure_next')
        self.cmd('cam_step', value=5)
        self.wait_until(lambda state: state['tracker']['treasure']['score'] >= 1, 25, 'a point for the red ball')
        self.cmd('feature', name='treasure_hunt', on=False)

    @shows(('K.switch.sound_level', 'on'), ('K.switch.sound_level', 'off'), ('K.setting.mic_device', 'set'))
    def test_07_twin_microphone(self):
        self.cmd('setting', name='mic_device', value='plughw:CARD=Missing,DEV=0')
        self.cmd('feature', name='sound_level', on=True)
        self.wait_until(lambda state: state['audio']['error'], 6, 'the error for a missing real microphone')
        self.cmd('setting', name='mic_device', value='')
        write_loud_recording(os.path.join(DATA, 'twin_microphone', 'loud.wav'))
        current = self.wait_until(lambda state: (state['audio']['level_dbfs'] or -200) > LOUD_DBFS, 8, 'the loud recording')
        self.assertEqual(current['audio']['device'], 'twin')
        self.cmd('feature', name='sound_level', on=False)

    @shows(('S.start.message', 'restart'))
    def test_08_twin_speaker_said_the_start_message(self):
        with open(os.path.join(DATA, 'twin_speaker.jsonl'), encoding='utf-8') as speaker_file:
            said = [json.loads(line)['text'] for line in speaker_file]
        self.assertIn('Ich bin bereit.', said)

    @shows(('K.switch.power_warning', 'on'))
    def test_09_twin_sensors_under_voltage_warns(self):
        hardware_file = os.path.join(DATA, 'twin_hardware.json')
        with open(hardware_file, 'w') as hardware:
            json.dump({'throttled': {'flags': '0x50005', 'undervoltage_now': True, 'throttled_now': True, 'undervoltage_since_boot': True}}, hardware)
        try:
            self.wait_until(lambda state: any('UNDER-VOLTAGE' in warning for warning in state['telemetry'].get('warnings', [])), 12, 'the warning')
        finally:
            os.remove(hardware_file)

    def test_10_twin_car_data_like_the_car(self):
        with open(os.path.join(DATA, 'twin_owners.json')) as owners_file:
            self.assertEqual(json.load(owners_file).get('features.json'), 'root')
        self.assertNotIn('mbrola_voice', [feature['name'] for feature in self.state()['features']], 'a switch the car no longer knows is dropped')

    @shows(('K.switch.pause_voice_commands', 'on'), ('K.switch.pause_voice_commands', 'off'))
    def test_11_pause_voice_commands_only_flips(self):
        current = self.cmd('feature', name='pause_voice_commands', on=True)
        self.assertTrue(self.enabled(current, 'pause_voice_commands'))
        self.assertFalse(self.state()['audio']['commands_paused'], 'no conversation, so nothing is paused')
        current = self.cmd('feature', name='pause_voice_commands', on=False)
        self.assertEqual((self.enabled(current, 'pause_voice_commands'), self.state()['audio']['commands_paused']), (False, False))
        self.assertEqual(self.state()['car']['drive_direction'], 0)

    @shows(('K.switch.dashboard_pause_switch', 'on'), ('K.switch.dashboard_pause_switch', 'off'))
    def test_12_dashboard_pause_switch_only_shows_the_switch(self):
        dashboard_js = self.get('/static/dashboard.js').decode('utf-8')
        self.assertIn('data-feature="pause_voice_commands"', dashboard_js)
        self.assertIn("'dashboard_pause_switch'", dashboard_js)
        current = self.cmd('feature', name='dashboard_pause_switch', on=True)
        self.assertEqual((self.enabled(current, 'dashboard_pause_switch'), self.enabled(current, 'pause_voice_commands')), (True, False))
        self.assertTrue(self.enabled(self.cmd('feature', name='pause_voice_commands', on=True), 'pause_voice_commands'))
        self.cmd('feature', name='pause_voice_commands', on=False)
        current = self.cmd('feature', name='dashboard_pause_switch', on=False)
        self.assertEqual((self.enabled(current, 'dashboard_pause_switch'), self.enabled(current, 'pause_voice_commands')), (False, False))
        self.assertEqual(self.state()['car']['drive_direction'], 0)

    @shows(('K.setting.voice_languages', 'set'))
    def test_13_dashboard_switches_settings_and_charts(self):
        current = self.state()
        self.assertEqual(set(current['setting_types']), set(current['settings']), 'every setting is listed')
        dashboard_html = self.get('/').decode('utf-8')
        for marker in ('id="features"', 'id="settings"') + tuple('id="chart-%s"' % name for name in ('temp', 'wifi', 'ram', 'power')):
            self.assertIn(marker, dashboard_html)
        self.assertEqual(self.cmd('setting', name='voice_languages', value='de,en')['settings']['voice_languages'], 'de,en')
        self.cmd('setting', name='voice_languages', value='de')
        history_columns = json.loads(self.get('/api/history?minutes=60').decode('utf-8'))['columns']
        chart_columns = re.findall(r"drawChart\(\$\('#chart-[a-z]+'\), data\.rows, data\.columns\.indexOf\('([a-z_0-9]+)'\)",
                                   self.get('/static/dashboard.js').decode('utf-8'))
        self.assertEqual(chart_columns, ['cpu_temp_c', 'wifi_dbm', 'mem_used_pct', 'undervoltage'], 'four charts, one under the other')
        self.assertTrue(set(chart_columns) <= set(history_columns), 'each chart draws a recorded column')

    @shows(('K.switch.natural_voice', 'on'), ('K.switch.natural_voice', 'off'))
    def test_14_natural_voice_needs_recordings(self):
        current = self.cmd('feature', name='natural_voice', on=True)
        self.assertEqual((self.enabled(current, 'natural_voice'), current['speaker']), (True, {'voice': 'espeak', 'recordings': {'de': 0, 'en': 0}}),
                         'the twin voice cache is empty: espeak-ng')
        current = self.cmd('feature', name='natural_voice', on=False)
        self.assertEqual((self.enabled(current, 'natural_voice'), current['speaker']['voice']), (False, 'espeak'))
        self.cmd('feature', name='natural_voice', on=True)

    def _data_file(self, name):
        with open(os.path.join(DATA, name), encoding='utf-8') as data_file:
            return json.load(data_file)

    @shows(('K.switch.single_config_file', 'on'), ('K.switch.single_config_file', 'off'))
    def test_15_single_config_file(self):
        settings_before = self._data_file('settings.json')
        self.cmd('feature', name='single_config_file', on=True)
        config = self._data_file('picar.json')
        self.assertEqual((config['version'], config['features']['single_config_file'], config['settings']), (1, True, settings_before),
                         'switches and settings in one file')
        self.cmd('setting', name='voice_languages', value='de,en')
        self.assertEqual(self._data_file('picar.json')['settings']['voice_languages'], 'de,en')
        self.assertEqual(self._data_file('settings.json'), settings_before, 'settings.json is left alone while on')
        self.cmd('feature', name='single_config_file', on=False)
        self.assertFalse(self._data_file('features.json')['single_config_file'])
        self.assertFalse(self._data_file('picar.json')['features']['single_config_file'], 'picar.json says it is off')
        self.assertEqual(self.state()['settings']['voice_languages'], 'de,en', 'a setting changed while on is kept')
        self.cmd('setting', name='voice_languages', value='de')

    @shows(('K.switch.data_files_as_pi', 'on'), ('K.switch.data_files_as_pi', 'off'))
    def test_16_data_files_as_pi(self):
        photo = 'media/' + self.cmd('photo')['media']['files'][0]['name']
        self.assertNotEqual(self._data_file('twin_owners.json').get(photo), 'pi', 'off: the service owns its files')
        self.cmd('feature', name='data_files_as_pi', on=True)
        owners = self._data_file('twin_owners.json')
        self.assertEqual({name: owners.get(name) for name in ('features.json', 'settings.json', 'media', photo)},
                         dict.fromkeys(('features.json', 'settings.json', 'media', photo), 'pi'), 'once: everything belongs to pi')
        new_photo = 'media/' + self.cmd('photo')['media']['files'][0]['name']
        self.assertEqual(self._data_file('twin_owners.json').get(new_photo), 'pi', 'a new file belongs to pi')
        self.cmd('feature', name='data_files_as_pi', on=False)

    @shows(('K.switch.disk_warning', 'on'), ('K.switch.disk_warning', 'off'))
    def test_17_disk_warning(self):
        hardware_file = os.path.join(DATA, 'twin_hardware.json')
        with open(hardware_file, 'w') as hardware:
            json.dump({'disk': {'total_gb': 62.3, 'free_gb': 9.3, 'used_pct': 85.0}}, hardware)
        try:
            current = self.wait_until(lambda state: state['telemetry'].get('disk', {}).get('used_pct') == 85.0, 12, 'the full twin disk')
            self.assertIsNone(current['telemetry']['disk_warning'], 'off: no warning below 90 %')
            self.cmd('feature', name='disk_warning', on=True)
            current = self.wait_until(lambda state: state['telemetry'].get('disk_warning'), 12, 'the disk warning')
            self.assertIn('SD card 85 % full; biggest: media', current['telemetry']['disk_warning'])
            self.assertIn(current['telemetry']['disk_warning'], current['telemetry']['warnings'])
            self.cmd('feature', name='disk_warning', on=False)
            self.wait_until(lambda state: not state['telemetry'].get('disk_warning'), 12, 'the warning gone')
        finally:
            os.remove(hardware_file)

    @shows(('K.switch.service_list', 'on'), ('K.switch.service_list', 'off'))
    def test_18_service_list(self):
        self.assertIsNone(self.state()['services'], 'off: not in the state')
        services = self.cmd('feature', name='service_list', on=True)['services']
        self.assertEqual(list(services), list(service_names()), 'every loop runs, in the listed order')
        self.assertTrue(all(service['running'] and not service['error'] for service in services.values()), services)
        self.assertIn('id="services"', self.get('/').decode('utf-8'))
        self.cmd('feature', name='service_list', on=False)

    @shows(('K.switch.state_sections', 'on'), ('K.switch.state_sections', 'off'))
    def test_19_state_sections(self):
        before = self.state()
        self.cmd('feature', name='state_sections', on=True)
        self.assertEqual(set(self.state()), set(before), 'the same sections either way')
        photo = self.cmd('photo')['media']['files'][0]['name']
        self.assertTrue(photo.startswith('photo-'), 'a command answers with fresh sections')
        polled = [self.state()['media']['files'][0]['name'] for _ in range(3)]
        self.assertEqual(polled, [photo] * 3)
        self.cmd('feature', name='state_sections', on=False)

    @shows(('K.switch.detector_registry', 'on'), ('K.switch.detector_registry', 'off'))
    def test_20_detector_registry_finds_the_same(self):
        self.cmd('feature', name='detector_registry', on=True)
        try:
            self.cmd('cam_center')
            self.cmd('feature', name='qr_reader', on=True)
            self.assertEqual(self.wait_until(lambda state: state['tracker']['qr'], what='a QR code')['tracker']['qr']['text'], 'picar-sim-qr')
            self.cmd('feature', name='markers', on=True)
            current = self.wait_until(lambda state: state['tracker']['markers'], what='a marker')
            self.assertEqual([marker['id'] for marker in current['tracker']['markers']], [3])
            self.cmd('feature', name='markers', on=False)
            self.wait_until(lambda state: state['tracker']['markers'] == [], what='markers cleared when off')
            self.cmd('feature', name='qr_reader', on=False)
            self.cmd('cam_center')
            self.cmd('track_color', name='red')
            self.cmd('feature', name='ball_tracking', on=True)
            self.wait_until(lambda state: state['tracker']['found'], 10, 'the red ball')
            self.cmd('feature', name='face_tracking', on=True)
            self.wait_until(lambda state: state['tracker']['detectors'].get('face'), what='the face detector status')
            self.cmd('feature', name='face_tracking', on=False)
            self.wait_until(lambda state: not state['tracker']['found'], what='nothing followed')
        finally:
            self.cmd('feature', name='detector_registry', on=False)
        self.assertEqual(self.state()['car']['drive_direction'], 0)

    @shows(('K.switch.dashboard_calibration', 'on'), ('K.switch.dashboard_calibration', 'off'), ('K.command.calibrate', 'run'))
    def test_21_dashboard_calibration(self):
        self.assert_refused('calibrate', part='camera', step='start')        # off
        self.assertIsNone(self.state()['calibration'])
        before = self.cmd('feature', name='dashboard_calibration', on=True)['calibration']
        for step in ('start', 'left', 'left', 'up', 'ok'):
            self.cmd('calibrate', part='camera', step=step)
        for step in ('start', 'right', 'ok'):
            current = self.cmd('calibrate', part='steering', step=step)
        self.assertEqual(current['calibration'], dict(before, pan_offset=before['pan_offset'] + 2, tilt_offset=before['tilt_offset'] + 1,
                                                      turning_offset=before['turning_offset'] + 1), 'saved in the calibration file')
        self.assert_refused('calibrate', part='rear', step='start')          # the rear wheels drive: old page only
        self.assertIn('id="calibration"', self.get('/').decode('utf-8'))
        for step in ('start', 'right', 'right', 'down', 'ok'):
            self.cmd('calibrate', part='camera', step=step)
        for step in ('start', 'left', 'ok'):
            self.cmd('calibrate', part='steering', step=step)
        self.assertEqual(self.state()['calibration'], before, 'back to the old offsets')
        self.cmd('feature', name='dashboard_calibration', on=False)
        self.assertEqual(self.state()['car']['drive_direction'], 0)

    def test_99_events_logged(self):
        self.assertGreaterEqual(len(self.state()['events']), 5)
