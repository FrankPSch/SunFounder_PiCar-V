# Usage: python tools/car_check.py   (runs ON THE PC against the real car; never drives, only moves the camera)
"""Non-driving check of the car: detectors load, media files are written, microphone runs. Prints values to read, no pass/fail."""
import json, time, urllib.request, urllib.error
BASE='http://192.168.178.131:8000'
def cmd(c, **a):
    r=urllib.request.Request(BASE+'/api/cmd',data=json.dumps(dict(cmd=c,**a)).encode(),method='POST')
    try: return json.load(urllib.request.urlopen(r,timeout=10))
    except urllib.error.HTTPError as e: return {'http_error':e.code,'body':e.read()[:200]}
def st(): return json.load(urllib.request.urlopen(BASE+'/api/state',timeout=10))
s=st()
print('features on:',[f['name'] for f in s['features'] if f['on']])
print('car:',{k:s['car'].get(k) for k in ('drive_direction','speed','steer_angle','cam_step')})
t=s['telemetry']; print('warnings:',t.get('warnings')); print('telemetry keys:',list(t)[:20])
print('audio:',s['audio']); print('tracker detectors:',s['tracker'].get('detectors'))
cmd('cam_center')
for name in ('face_tracking','animal_tracking','ball_tracking'):
    cmd('feature',name=name,on=True); time.sleep(12); s=st()
    print(name,'->',{k:s['tracker'].get(k) for k in ('mode','fps','target','error','detectors')})
    cmd('feature',name=name,on=False)
cmd('cam_center')
for name in ('qr_reader','markers'):
    cmd('feature',name=name,on=True); time.sleep(6); s=st()
    print(name,'->',s['tracker'].get('qr'),s['tracker'].get('markers'),s['tracker'].get('error'),'fps',s['tracker'].get('fps'))
    cmd('feature',name=name,on=False)
cmd('photo'); s=st(); print('photo:',s['media']['files'][:1])
cmd('feature',name='record_audio',on=True)
cmd('record',on=True); time.sleep(5); print('rec:',st()['media']['recording']); cmd('record',on=False); time.sleep(1)
print('files:',[(f['name'],f['bytes']) for f in st()['media']['files'][:4]])
cmd('feature',name='record_audio',on=False)
cmd('setting',name='timelapse_seconds',value=3); cmd('feature',name='timelapse',on=True); time.sleep(8)
print('timelapse:',[f['name'] for f in st()['media']['files'] if f['name'].startswith('timelapse-')][:3]); cmd('feature',name='timelapse',on=False)
cmd('setting',name='timelapse_seconds',value=10)
cmd('feature',name='sound_level',on=True); time.sleep(4); print('sound:',st()['audio']); cmd('feature',name='sound_level',on=False)
for name in ('stop_word','voice_commands'):
    cmd('feature',name=name,on=True); time.sleep(12); print(name,st()['audio']); cmd('feature',name=name,on=False)
time.sleep(2)
s=st(); print('state after:',s['car'].get('drive_direction'),'on:',[f['name'] for f in s['features'] if f['on']])
print('tel:',{k:s['telemetry'].get(k) for k in ('cpu_temp_c','load','throttled','memory','warnings')})
print('events:',[e.get('text') for e in s['events'][:15]])
