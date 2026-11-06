// PiCar dashboard: polls /api/state, sends commands to /api/cmd.
// Holding a drive control sends keepalives; the car's watchdog stops it 0.6 s after the last one.
'use strict';

const KEEPALIVE_MS = 200;
const CAM_REPEAT_DELAY_MS = 350;
const CAM_REPEAT_MS = 120;
const POLL_MS = 400;

let lastState = null;
let connected = false;

const $ = (selector) => document.querySelector(selector);

async function send(cmd, args = {}) {
  try {
    const response = await fetch('/api/cmd', { method: 'POST', body: JSON.stringify(Object.assign({ cmd }, args)) });
    if (!response.ok) { console.warn(cmd, await response.text()); return; }
    render(await response.json());
  } catch (error) { setConnected(false); }
}

// ---------- holding: drive keeps alive, camera repeats ----------
const holds = new Map();

function startHold(id, onStart, onRepeat, onEnd, repeatMs, delayMs = 0) {
  if (holds.has(id)) return;
  onStart();
  const hold = { onEnd, timer: null };
  if (onRepeat) {
    hold.timer = setTimeout(function tick() { onRepeat(); hold.timer = setTimeout(tick, repeatMs); }, delayMs || repeatMs);
  }
  holds.set(id, hold);
}

function endHold(id) {
  const hold = holds.get(id);
  if (!hold) return;
  clearTimeout(hold.timer);
  holds.delete(id);
  if (hold.onEnd) hold.onEnd();
}

function holdDrive(id, direction) {
  startHold(id, () => send('drive', { direction }), () => send('keepalive'), () => send('drive', { direction: 0 }), KEEPALIVE_MS);
}
function holdSteer(id, side) {
  startHold(id, () => send('steer_hold', { side }), null, () => send('steer_hold', { side: 0 }));
}
function holdCam(id, pan, tilt, tenfold) {
  // Send step counts only; the car multiplies by its current step size (never a stale value from the page).
  const factor = tenfold ? 10 : 1;
  const move = () => send('cam_move', { pan: pan * factor, tilt: tilt * factor });
  startHold(id, move, move, null, CAM_REPEAT_MS, CAM_REPEAT_DELAY_MS);
}

function bindPointerHold(button, begin) {
  const id = 'ptr-' + Math.random();
  button.addEventListener('pointerdown', (event) => { event.preventDefault(); button.setPointerCapture(event.pointerId); begin(id, event); });
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) button.addEventListener(type, () => endHold(id));
}

document.querySelectorAll('[data-drive]').forEach((b) => bindPointerHold(b, (id) => holdDrive(id, +b.dataset.drive)));
document.querySelectorAll('[data-hold]').forEach((b) => bindPointerHold(b, (id) => holdSteer(id, +b.dataset.hold)));
document.querySelectorAll('[data-cam]').forEach((b) => {
  const [pan, tilt] = b.dataset.cam.split(',').map(Number);
  bindPointerHold(b, (id, event) => holdCam(id, pan, tilt, event.shiftKey));
});
document.querySelector('[data-ready]').addEventListener('click', () => send('ready'));
document.querySelector('[data-cam-center]').addEventListener('click', () => send('cam_center'));
$('#stop').addEventListener('click', () => { holds.forEach((_, id) => endHold(id)); send('stop'); });

document.querySelectorAll('.stepper').forEach((stepper) => {
  const cmd = stepper.dataset.cmd;
  stepper.querySelectorAll('[data-delta]').forEach((b) => b.addEventListener('click', () => send(cmd, { delta: +b.dataset.delta })));
  const slider = stepper.querySelector('input[type=range]');
  if (slider) slider.addEventListener('change', () => { send(cmd, { value: +slider.value }); slider.blur(); });
});

document.querySelectorAll('[data-feature]').forEach((box) => box.addEventListener('change', () => send('feature', { name: box.dataset.feature, on: box.checked })));
$('#track-color').addEventListener('change', (event) => { send('track_color', { name: event.target.value }); event.target.blur(); });
$('#teach').addEventListener('click', () => send('track_teach'));
$('#track-animal').addEventListener('change', (event) => { send('track_animal', { name: event.target.value }); event.target.blur(); });
$('#treasure-next').addEventListener('click', () => send('treasure_next'));
$('#photo').addEventListener('click', () => send('photo'));
$('#record').addEventListener('click', () => send('record', { on: !(lastState && lastState.media.recording) }));
$('#timelapse-seconds').addEventListener('change', (event) => { send('setting', { name: 'timelapse_seconds', value: +event.target.value }); event.target.blur(); });
$('#rooms-seen').addEventListener('change', (event) => {
  if (event.target.dataset.marker) send('room_name', { id: +event.target.dataset.marker, name: event.target.value });
});
$('#rooms-goto').addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.room) send('room_goto', { name: button.dataset.room }); else send('room_cancel');
});

// ---------- keyboard (physical keys, so QWERTZ and QWERTY behave the same) ----------
const KEY_HOLDS = {
  KeyW: (id) => holdDrive(id, 1), KeyS: (id) => holdDrive(id, -1),
  KeyA: (id) => holdSteer(id, -1), KeyD: (id) => holdSteer(id, 1),
  ArrowUp: (id, e) => holdCam(id, 0, 1, e.shiftKey), ArrowDown: (id, e) => holdCam(id, 0, -1, e.shiftKey),
  ArrowLeft: (id, e) => holdCam(id, 1, 0, e.shiftKey), ArrowRight: (id, e) => holdCam(id, -1, 0, e.shiftKey),
};
const KEY_PRESSES = {
  KeyQ: (e) => send('steer', { delta: e.shiftKey ? -10 : -1 }), KeyE: (e) => send('steer', { delta: e.shiftKey ? 10 : 1 }),
  KeyR: (e) => send('speed', { delta: e.shiftKey ? 10 : 1 }), KeyF: (e) => send('speed', { delta: e.shiftKey ? -10 : -1 }),
  Comma: (e) => send('cam_step', { delta: e.shiftKey ? -10 : -1 }), Period: (e) => send('cam_step', { delta: e.shiftKey ? 10 : 1 }),
  KeyC: () => send('cam_center'), Space: () => $('#stop').click(),
  KeyT: () => lastState && send('feature', { name: 'ball_tracking', on: !lastState.tracker.enabled }),
};

// Fallback when a browser/remote tool sends no physical code: map the character (US and German Shift variants).
const CHAR_TO_CODE = { '.': 'Period', ':': 'Period', '>': 'Period', ',': 'Comma', ';': 'Comma', '<': 'Comma', ' ': 'Space' };
function codeOf(event) {
  if (event.code) return event.code;
  if (CHAR_TO_CODE[event.key]) return CHAR_TO_CODE[event.key];
  return /^[a-z]$/i.test(event.key) ? 'Key' + event.key.toUpperCase() : event.key;
}

window.addEventListener('keydown', (event) => {
  const typing = event.target.tagName === 'INPUT' && ['text', 'number'].includes(event.target.type);
  if (typing || event.target.tagName === 'SELECT' || event.ctrlKey || event.altKey || event.metaKey) return;
  const code = codeOf(event);
  if (KEY_HOLDS[code]) { event.preventDefault(); if (!event.repeat) KEY_HOLDS[code]('key-' + code, event); }
  else if (KEY_PRESSES[code]) { event.preventDefault(); KEY_PRESSES[code](event); }
});
window.addEventListener('keyup', (event) => endHold('key-' + codeOf(event)));
window.addEventListener('blur', () => holds.forEach((_, id) => endHold(id)));

// ---------- rendering ----------
function setConnected(isConnected) {
  connected = isConnected;
  $('#conn').textContent = isConnected ? (window.PICAR_SIM ? 'connected (SIMULATION)' : 'connected') : 'no connection';
  $('#conn').className = 'pill ' + (isConnected ? 'ok' : 'bad');
}

function fmtUptime(seconds) {
  if (seconds == null) return 'n/a';
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60);
  return hours ? `${hours} h ${minutes} min` : `${minutes} min`;
}

function tile(key, value, level = '') {
  return `<div class="tile ${level}"><div class="k">${key}</div><div class="v">${value ?? 'n/a'}</div></div>`;
}

function renderTiles(t) {
  const throttled = t.throttled || {}, memory = t.memory || {}, disk = t.disk || {}, wifi = t.wifi || {};
  const git = t.git || {}, camera = t.camera || {}, mic = t.microphone || {}, i2c = t.i2c || {}, services = t.services || {};
  const power = throttled.undervoltage_now ? 'UNDER-VOLTAGE' : throttled.undervoltage_since_boot ? 'ok (dipped since boot)' : 'ok';
  const i2cText = Object.keys(i2c).length ? Object.entries(i2c).map(([name, ok]) => (ok ? '✓ ' : '✗ ') + name.split(' ')[0]).join(' ') : 'n/a';
  $('#tiles').innerHTML = [
    tile('CPU temp', t.cpu_temp_c != null ? t.cpu_temp_c + ' °C' : null, t.cpu_temp_c >= 75 ? 'bad' : t.cpu_temp_c >= 65 ? 'warn' : ''),
    tile('Power', power, throttled.undervoltage_now ? 'bad' : throttled.undervoltage_since_boot ? 'warn' : 'good'),
    tile('CPU', (t.cpu_mhz ?? '?') + ' MHz · load ' + (t.load ? t.load[0] : '?'), throttled.throttled_now ? 'warn' : ''),
    tile('RAM', memory.used_pct != null ? `${memory.used_pct} % of ${memory.total_mb} MB` : null, memory.used_pct > 85 ? 'warn' : ''),
    tile('SD card', disk.free_gb != null ? `${disk.free_gb} GB free` : null, disk.used_pct > 90 ? 'bad' : ''),
    tile('Wi-Fi', wifi.level_dbm != null ? `${wifi.level_dbm} dBm · ${wifi.tx_bitrate || ''}` : null, wifi.level_dbm <= -75 ? 'warn' : ''),
    tile('Uptime', fmtUptime(t.uptime_s)),
    tile('IP', t.ip),
    tile('Software', git.commit ? git.commit.split(' ')[0] + (git.dirty ? ' (modified)' : '') : null, git.dirty ? 'warn' : ''),
    tile('Services', Object.entries(services).map(([name, s]) => `${name.replace('.timer', '⏱')}: ${s || '?'}`).join('<br>')),
    tile('Camera', camera.device === false ? 'missing' : (camera.streamer ? 'streaming' : 'device only'), camera.device === false ? 'bad' : ''),
    tile('Microphone', mic.present ? mic.name : 'missing', mic.present ? '' : 'warn'),
    tile('I²C boards', i2cText),
    tile('Watchdog', `${t.car ? t.car.watchdog_stops : 0} stops`),
  ].join('');
}

function renderFeatures(featureList) {
  const box = $('#features');
  if (box.childElementCount !== featureList.length) {
    box.innerHTML = featureList.map((f) => `<label class="feature-row"><input type="checkbox" data-feature-row="${f.name}"><b>${f.name}</b><small>${f.help}</small></label>`).join('');
    box.querySelectorAll('[data-feature-row]').forEach((input) => input.addEventListener('change', () => send('feature', { name: input.dataset.featureRow, on: input.checked })));
  }
  for (const f of featureList) {
    box.querySelector(`[data-feature-row="${f.name}"]`).checked = f.on;
    document.querySelectorAll(`[data-feature="${f.name}"]`).forEach((input) => { input.checked = f.on; });
  }
}

function setIfIdle(input, value) { if (document.activeElement !== input) input.value = value; }

function render(state) {
  lastState = state;
  setConnected(true);
  const car = state.car, tracker = state.tracker, t = state.telemetry || {};
  const video = $('#video');
  if (video.dataset.src !== state.stream_url) { video.src = state.stream_url; video.dataset.src = state.stream_url; }

  $('#speed-val').textContent = car.speed; setIfIdle($('#speed'), car.speed);
  $('#steer-val').textContent = car.steer_angle; setIfIdle($('#steer'), car.steer_angle);
  $('#steer').min = car.steer_min; $('#steer').max = car.steer_max;
  $('#step-val').textContent = car.cam_step;
  $('#step-deg').textContent = ` ≈ ${(car.cam_step * car.degrees_per_tick).toFixed(2)}°`;
  const deg = (ticks) => (ticks * car.degrees_per_tick).toFixed(1);
  $('#camera-info').textContent = `pan ${car.pan_ticks} ticks (${deg(car.pan_ticks)}°) · tilt ${car.tilt_ticks} ticks (${deg(car.tilt_ticks)}°) · step ${car.cam_step} · drive ${['back', 'stop', 'fwd'][car.drive_direction + 1]} · last: ${car.last_event}`;
  document.querySelectorAll('[data-drive]').forEach((b) => b.classList.toggle('active', +b.dataset.drive === car.drive_direction));

  const select = $('#track-color');
  if (select.options.length !== tracker.colors.length) select.innerHTML = tracker.colors.map((c) => `<option>${c}</option>`).join('');
  setIfIdle(select, tracker.color);
  const animalSelect = $('#track-animal');
  if (animalSelect.options.length !== tracker.animals.length) animalSelect.innerHTML = tracker.animals.map((a) => `<option>${a}</option>`).join('');
  setIfIdle(animalSelect, tracker.animal);
  const detectorNote = Object.entries(tracker.detectors || {}).filter(([, s]) => s !== 'ok').map(([n, s]) => `${n}: ${s}`).join(' · ');
  $('#track-status').textContent = !tracker.mode ? 'off' + (detectorNote ? ' · ' + detectorNote : '') : tracker.error ? 'error: ' + tracker.error
    : (tracker.found ? `${tracker.mode}: ${tracker.target.label} · ${tracker.centred ? 'centred' : 'following'}` : `${tracker.mode}: searching`)
      + ` · ${tracker.fps} fps` + (detectorNote ? ' · ' + detectorNote : '');

  renderExtras(state);
  renderFeatures(state.features);
  renderTiles(Object.assign({}, t, { car }));
  $('#warnings').textContent = (t.warnings || []).join(' · ');
  drawOverlay(tracker);
}

function drawOverlay(tracker) {
  const canvas = $('#overlay'), video = $('#video');
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, width, height);
  const natW = video.naturalWidth || 4, natH = video.naturalHeight || 3;
  const scale = Math.min(width / natW, height / natH), drawW = natW * scale, drawH = natH * scale;
  const left = (width - drawW) / 2, top = (height - drawH) / 2;
  ctx.strokeStyle = 'rgba(255,255,255,0.35)';
  ctx.beginPath();
  ctx.moveTo(left + drawW / 2 - 10, top + drawH / 2); ctx.lineTo(left + drawW / 2 + 10, top + drawH / 2);
  ctx.moveTo(left + drawW / 2, top + drawH / 2 - 10); ctx.lineTo(left + drawW / 2, top + drawH / 2 + 10);
  ctx.stroke();
  const target = tracker.found && tracker.target;
  if (target) {
    ctx.strokeStyle = tracker.centred ? '#4caf7d' : '#e0a030';
    ctx.lineWidth = 3;
    const boxW = Math.max(12, target.w * drawW), boxH = Math.max(12, target.h * drawH);
    const boxX = left + target.x * drawW - boxW / 2, boxY = top + target.y * drawH - boxH / 2;
    ctx.strokeRect(boxX, boxY, boxW, boxH);
    ctx.fillStyle = ctx.strokeStyle; ctx.font = '12px system-ui'; ctx.fillText(target.label, boxX, boxY - 4);
  }
  for (const marker of tracker.markers || []) {
    const size = marker.size * drawW;
    ctx.strokeStyle = '#3fa7d6'; ctx.lineWidth = 2;
    ctx.strokeRect(left + marker.x * drawW - size / 2, top + marker.y * drawH - size / 2, size, size);
    ctx.fillStyle = '#3fa7d6'; ctx.font = '12px system-ui';
    const roomName = lastState && lastState.rooms.names[String(marker.id)];
    ctx.fillText(`#${marker.id}${roomName ? ' ' + roomName : ''}`, left + marker.x * drawW - size / 2, top + marker.y * drawH - size / 2 - 4);
  }
}

// ---------- extras: vision, rooms, media, microphone, events ----------
const escapeHtml = (value) => String(value).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
let roomsSignature = '';

function renderExtras(state) {
  const tracker = state.tracker, media = state.media, audio = state.audio, rooms = state.rooms;
  $('#qr-text').textContent = tracker.qr ? `${tracker.qr.text} (${tracker.qr.time})` : '';
  const treasure = tracker.treasure || {};
  $('#treasure-status').textContent = treasure.color ? `find: ${treasure.color} · score ${treasure.score} · ${'●'.repeat(treasure.progress)}` : '';

  const signature = JSON.stringify([Object.keys(rooms.seen), rooms.names]);
  if (signature !== roomsSignature && !$('#rooms-seen').contains(document.activeElement)) {
    roomsSignature = signature;
    const ids = [...new Set([...Object.keys(rooms.seen), ...Object.keys(rooms.names)])].sort((a, b) => a - b);
    $('#rooms-seen').innerHTML = ids.length ? ids.map((id) => `<label>marker #${id} ${rooms.seen[id] != null ? '👁' : ''}
      <input type="text" data-marker="${id}" placeholder="room name" value="${escapeHtml(rooms.names[id] || '')}"></label>`).join('')
      : '<span class="muted">no markers seen yet - switch on "detect markers"</span>';
    $('#rooms-goto').innerHTML = rooms.rooms.map((room) => `<button data-room="${escapeHtml(room)}">go to ${escapeHtml(room)}</button>`).join('')
      + (rooms.rooms.length ? '<button>cancel</button>' : '');
  }
  const nav = rooms.nav;
  $('#rooms-status').textContent = `current: ${rooms.current.room || 'unknown'}` + (nav.state !== 'idle' ? ` · nav ${nav.state}: ${nav.message || ''}` : '')
    + (Object.keys(rooms.transitions).length ? ' · seen paths: ' + Object.entries(rooms.transitions).map(([k, n]) => `${k} (${n}×)`).join(', ') : '');

  const record = $('#record');
  record.textContent = media.recording ? `■ stop (${media.recording.seconds} s, ${media.recording.mb} MB)` : '● record';
  record.classList.toggle('recording', !!media.recording);
  setIfIdle($('#timelapse-seconds'), media.timelapse_seconds);
  $('#media-status').textContent = `${media.files.length} recent files`;
  $('#media-files').innerHTML = media.files.slice(0, 12).map((f) => `<a href="/media/${encodeURI(f.name)}" target="_blank">${escapeHtml(f.name)}</a>`
    + ` <small>${(f.bytes / 1e6).toFixed(2)} MB</small>`).join('');

  const level = audio.level_dbfs;
  $('#mic-bar').style.width = level == null ? '0' : Math.max(0, Math.min(100, (level + 70) / 70 * 100)) + '%';
  $('#mic-status').textContent = (audio.running ? `listening · ${level} dBFS` : 'off')
    + (audio.heard ? ` · heard "${audio.heard}" ${audio.heard_time}` : '') + ` · claps ${audio.claps} · alarms ${audio.alarms}`
    + (audio.vosk_model ? '' : ' · vosk model missing') + (audio.error ? ' · ' + audio.error : '')
    + (audio.conversation && audio.conversation.state !== 'off' ? ` · Claude ${audio.conversation.state} ${audio.conversation.seconds_left} s` : '')
    + (audio.conversation && audio.conversation.error ? ` · Claude: ${audio.conversation.error}` : '');

  $('#events').innerHTML = state.events.slice().reverse().map((e) => `<div><small>${e.time}</small> <b>${e.source}</b> ${escapeHtml(e.text)}</div>`).join('')
    || '<span class="muted">no events yet</span>';
}

// ---------- charts ----------
function drawChart(canvas, rows, column, color) {
  const width = canvas.width = canvas.clientWidth, height = canvas.height;
  const ctx = canvas.getContext('2d');
  const values = rows.map((row) => row[column]).filter((v) => v != null);
  ctx.clearRect(0, 0, width, height);
  if (values.length < 2) { ctx.fillStyle = '#8b9aa5'; ctx.fillText('collecting…', 6, 14); return; }
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
  values.forEach((v, i) => {
    const x = i / (values.length - 1) * (width - 2) + 1, y = height - 4 - (v - min) / span * (height - 16);
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = '#8b9aa5'; ctx.fillText(`${min}…${max}  now ${values[values.length - 1]}`, 6, 11);
}

async function pollHistory() {
  try {
    const data = await (await fetch('/api/history?minutes=60')).json();
    drawChart($('#chart-temp'), data.rows, data.columns.indexOf('cpu_temp_c'), '#e0a030');
    drawChart($('#chart-wifi'), data.rows, data.columns.indexOf('wifi_dbm'), '#3fa7d6');
  } catch (error) { /* shown by the connection pill */ }
}

async function poll() {
  try { render(await (await fetch('/api/state')).json()); } catch (error) { setConnected(false); }
  setTimeout(poll, POLL_MS);
}

poll();
pollHistory();
setInterval(pollHistory, 30000);
