'''
Telemetry: everything readable on the Pi without extra sensors, sampled every SAMPLE_SECONDS
into a 24 h ring buffer for the dashboard. Each reader returns None when its source is missing,
so a broken source shows up as "n/a" instead of killing the sampler.
'''
import collections
import os
import re
import shutil
import socket
import subprocess
import threading
import time

from . import features
from .car import car, SIM

SAMPLE_SECONDS = 5
HISTORY_SAMPLES = 24 * 3600 // SAMPLE_SECONDS
REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVICES = ('picar', 'picar-update.timer')
I2C_DEVICES = {0x40: 'PCA9685 servo/PWM', 0x48: 'PCF8591 ADC'}

UNDERVOLTAGE_NOW, THROTTLED_NOW, UNDERVOLTAGE_SINCE_BOOT = 1 << 0, 1 << 2, 1 << 16

history = collections.deque(maxlen=HISTORY_SAMPLES)
latest = {}
_tracker_status = lambda: {}


def _run(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _read(path):
    try:
        with open(path) as source:
            return source.read()
    except OSError:
        return None


def read_cpu_temp_c():
    raw = _read('/sys/class/thermal/thermal_zone0/temp')
    return round(int(raw) / 1000.0, 1) if raw else None


def read_cpu_mhz():
    raw = _read('/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq')
    return int(raw) // 1000 if raw else None


def read_throttled():
    output = _run(['vcgencmd', 'get_throttled'])
    if not output or '=' not in output:
        return None
    flags = int(output.split('=')[1], 16)
    return {'flags': hex(flags), 'undervoltage_now': bool(flags & UNDERVOLTAGE_NOW),
            'throttled_now': bool(flags & THROTTLED_NOW),
            'undervoltage_since_boot': bool(flags & UNDERVOLTAGE_SINCE_BOOT)}


def read_load():
    raw = _read('/proc/loadavg')
    return [float(value) for value in raw.split()[:3]] if raw else None


def read_memory():
    raw = _read('/proc/meminfo')
    if not raw:
        return None
    meminfo = {line.split(':')[0]: int(line.split()[1]) for line in raw.splitlines()}
    total_mb, available_mb = meminfo['MemTotal'] // 1024, meminfo['MemAvailable'] // 1024
    return {'total_mb': total_mb, 'available_mb': available_mb,
            'used_pct': round(100.0 * (total_mb - available_mb) / total_mb, 1)}


def read_disk():
    usage = shutil.disk_usage('/')
    return {'total_gb': round(usage.total / 1e9, 1), 'free_gb': round(usage.free / 1e9, 1),
            'used_pct': round(100.0 * usage.used / usage.total, 1)}


def read_uptime_s():
    raw = _read('/proc/uptime')
    return int(float(raw.split()[0])) if raw else None


def read_wifi():
    raw = _read('/proc/net/wireless')
    level_dbm = None
    if raw:
        for line in raw.splitlines()[2:]:
            if line.strip().startswith('wlan0'):
                level_dbm = int(float(line.split()[3]))
    link = _run(['iw', 'dev', 'wlan0', 'link']) or ''
    ssid = re.search(r'SSID: (.+)', link)
    bitrate = re.search(r'tx bitrate: ([\d.]+ \S+)', link)
    if level_dbm is None and not ssid:
        return None
    return {'ssid': ssid.group(1) if ssid else None, 'level_dbm': level_dbm,
            'tx_bitrate': bitrate.group(1) if bitrate else None}


def read_ip():
    return (_run(['hostname', '-I']) or '').split(' ')[0] or None


def read_services():
    return {name: _run(['systemctl', 'is-active', name]) for name in SERVICES}


def read_git():
    # The service runs as root but the repo belongs to pi; safe.directory avoids git's "dubious ownership" refusal.
    git = ['git', '-c', 'safe.directory=' + REPO_DIR, '-C', REPO_DIR]
    commit = _run(git + ['log', '-1', '--format=%h %cI %s'])
    branch = _run(git + ['rev-parse', '--abbrev-ref', 'HEAD'])
    dirty = _run(git + ['status', '--porcelain'])
    return {'commit': commit, 'branch': branch, 'dirty': bool(dirty)} if commit else None


def read_i2c():
    try:
        import smbus
    except ImportError:
        return None
    bus = smbus.SMBus(1)
    found = {}
    for address, name in I2C_DEVICES.items():
        try:
            bus.read_byte(address)
            found[name] = True
        except OSError:
            found[name] = False
    return found


def read_camera():
    return {'device': os.path.exists('/dev/video0'),
            'streamer': bool(_run(['pgrep', '-x', 'mjpg_streamer']))}


def read_microphone():
    cards = _read('/proc/asound/cards') or ''
    match = re.search(r'\[(\w+)\s*\]: USB-Audio - (.+)', cards)
    return {'present': bool(match), 'name': match.group(2).strip() if match else None}


SLOW_READERS = {'git': read_git, 'i2c': read_i2c, 'services': read_services}


def sample(include_slow=False):
    snapshot = {
        'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'hostname': socket.gethostname(),
        'cpu_temp_c': read_cpu_temp_c(), 'cpu_mhz': read_cpu_mhz(), 'load': read_load(),
        'throttled': read_throttled(), 'memory': read_memory(), 'disk': read_disk(),
        'uptime_s': read_uptime_s(), 'wifi': read_wifi(), 'ip': read_ip(),
        'camera': read_camera(), 'microphone': read_microphone(),
        'car': car.state(), 'tracker': _tracker_status(),
    }
    for name, reader in SLOW_READERS.items():
        snapshot[name] = reader() if include_slow else latest.get(name)
    if SIM:
        snapshot['cpu_temp_c'] = snapshot['cpu_temp_c'] or 48.0
        snapshot['throttled'] = snapshot['throttled'] or {'flags': '0x0', 'undervoltage_now': False,
                                                          'throttled_now': False, 'undervoltage_since_boot': False}
    snapshot['warnings'] = warnings_for(snapshot)
    return snapshot


def warnings_for(snapshot):
    warnings = []
    throttled = snapshot.get('throttled') or {}
    if features.enabled('power_warning') and throttled.get('undervoltage_now'):
        warnings.append('UNDER-VOLTAGE now: power supply or battery too weak')
    if throttled.get('throttled_now'):
        warnings.append('CPU throttled now')
    if (snapshot.get('cpu_temp_c') or 0) >= 75:
        warnings.append('CPU hot: %s C' % snapshot['cpu_temp_c'])
    if (snapshot.get('disk') or {}).get('used_pct', 0) >= 90:
        warnings.append('SD card almost full')
    wifi = snapshot.get('wifi') or {}
    if wifi.get('level_dbm') is not None and wifi['level_dbm'] <= -75:
        warnings.append('Wi-Fi weak: %s dBm' % wifi['level_dbm'])
    if not SIM and not (snapshot.get('camera') or {}).get('device'):
        warnings.append('camera not connected')
    return warnings


def _history_row(snapshot):
    throttled = snapshot.get('throttled') or {}
    return [snapshot['time'][11:19], snapshot.get('cpu_temp_c'), (snapshot.get('load') or [None])[0],
            (snapshot.get('memory') or {}).get('used_pct'), (snapshot.get('wifi') or {}).get('level_dbm'),
            1 if throttled.get('undervoltage_now') else 0, snapshot.get('cpu_mhz')]


HISTORY_COLUMNS = ['time', 'cpu_temp_c', 'load_1m', 'mem_used_pct', 'wifi_dbm', 'undervoltage', 'cpu_mhz']


def _loop():
    sample_count = 0
    previous_warnings = []
    while True:
        if features.enabled('telemetry'):
            snapshot = sample(include_slow=sample_count % 12 == 0)
            latest.clear()
            latest.update(snapshot)
            history.append(_history_row(snapshot))
            if snapshot['warnings'] != previous_warnings:
                print('telemetry: warnings now %s' % (snapshot['warnings'] or 'none'))
                previous_warnings = snapshot['warnings']
            sample_count += 1
        time.sleep(SAMPLE_SECONDS)


def start(tracker_status):
    global _tracker_status
    _tracker_status = tracker_status
    latest.update(sample(include_slow=True))
    threading.Thread(target=_loop, name='telemetry', daemon=True).start()
