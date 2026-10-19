import {resolutionFor, resizePacket} from './remote-sizing.js';
/* Octets RFB/PTY relayés par HTTPS : aucune adresse ni aucun jeton de poste dans le navigateur. */
const root = document.getElementById('remote-session');
const screen = document.getElementById('remote-screen');
const status = document.getElementById('remote-status');
const closeButton = document.getElementById('remote-close');
const encoder = new TextEncoder();
let running = true, inputSeq = 0, outputAck = 0, queue = new Uint8Array(), pending = null;
let terminal, fit, rfb, channel, failures = 0, cols = 100, rows = 30;
let pollTimer, inFlight = false;
let remoteLabel = root.dataset.kind === 'terminal' ? 'terminal du poste' : 'bureau du poste';
function schedulePoll(delay = 0) {
  if (!running || inFlight) return;
  clearTimeout(pollTimer); pollTimer = setTimeout(poll, delay);
}
function message(text, error = false) { status.textContent = text; status.className = error ? 'note err' : 'note'; }
function bytes(encoded) { return Uint8Array.from(atob(encoded), c => c.charCodeAt(0)); }
function base64(data) { let out = ''; for (let i = 0; i < data.length; i += 8192) out += String.fromCharCode(...data.subarray(i, i + 8192)); return btoa(out); }
function send(data) {
  const part = data instanceof Uint8Array ? data : new Uint8Array(data);
  if (queue.length + part.length > 512 * 1024) { stop('La saisie dépasse la capacité de la session.', true); return; }
  const combined = new Uint8Array(queue.length + part.length); combined.set(queue); combined.set(part, queue.length); queue = combined;
  schedulePoll();
}
function closeRequest(beacon = false) {
  const data = new URLSearchParams({ csrf: root.dataset.csrf, close: '1' });
  if (beacon) navigator.sendBeacon(root.dataset.io, data);
  else return fetch(root.dataset.io, { method: 'POST', body: data, credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' }, keepalive: true });
}
function stop(text = 'Session fermée.', notify = false) {
  if (!running) return;
  running = false; clearTimeout(pollTimer); closeButton.disabled = true; message(text);
  document.getElementById('remote-reconnect').hidden = false;
  if (channel) { channel.readyState = 3; if (channel.onclose) channel.onclose({ code: 1000, reason: text }); }
  if (terminal) terminal.options.disableStdin = true;
  if (notify) closeRequest().catch(() => {});
}
// Interface de canal acceptée par noVNC (WebSocket/RTCDataChannel).
class RelayChannel {
  constructor() {
    this.readyState = 0; this.binaryType = 'arraybuffer'; this.bufferedAmount = 0;
    this.protocol = ''; this.onopen = null; this.onmessage = null; this.onclose = null; this.onerror = null;
  }
  send(data) { send(data); }
  close() { stop('Session fermée.', true); }
}
async function poll() {
  if (!running || inFlight) return;
  inFlight = true; let busy = false;
  if (!pending && queue.length) {
    const data = queue.subarray(0, 64 * 1024); queue = queue.slice(data.length);
    pending = { seq: ++inputSeq, data: base64(data) };
  }
  const body = new URLSearchParams({ csrf: root.dataset.csrf, ack: String(outputAck), cols: String(cols), rows: String(rows) });
  if (pending) { body.set('seq', String(pending.seq)); body.set('data', pending.data); }
  try {
    const response = await fetch(root.dataset.io, { method: 'POST', body, credentials: 'same-origin',
      headers: { 'X-Requested-With': 'fetch' }, signal: AbortSignal.timeout(10000) });
    if ([401,403,404,409,413].includes(response.status)) { stop('Session indisponible ou accès révoqué.', true); return; }
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const data = await response.json();
    if (!running) return;
    failures = 0; busy = data.packets.length > 0;
    if (pending && data.ack >= pending.seq) pending = null;
    if (data.state === 'active') {
      message('Connecté · ' + remoteLabel);
      if (channel && channel.readyState === 0) { channel.readyState = 1; if (channel.onopen) channel.onopen({}); }
    }
    for (const packet of data.packets) {
      if (packet.seq <= outputAck) continue;
      const buffer = bytes(packet.data);
      if (terminal) await new Promise(resolve => terminal.write(buffer, resolve));
      else if (channel.onmessage) channel.onmessage({ data: buffer.buffer });
      outputAck = packet.seq;
    }
    if (data.state === 'closed' && !data.packets.length) { stop(data.error || 'Session terminée.'); return; }
  } catch (error) {
    if (++failures >= 10) { stop('Connexion au serveur interrompue.', true); return; }
    message('Reconnexion au serveur…');
  } finally { inFlight = false; }
  schedulePoll(failures ? 1000 : busy || pending || queue.length ? 0 : 25);
}
closeButton.addEventListener('click', () => stop('Session fermée.', true));
window.addEventListener('pagehide', () => { if (running) { stop('Session fermée.'); closeRequest(true); } });
document.getElementById('remote-fullscreen').addEventListener('click', () => {
  if (document.fullscreenElement) document.exitFullscreen(); else root.requestFullscreen().catch(() => message('Le navigateur a refusé le plein écran.', true));
});
document.addEventListener('fullscreenchange', () => {
  const full = document.fullscreenElement === root;
  const button = document.getElementById('remote-fullscreen');
  button.textContent = full ? 'Quitter le plein écran' : 'Plein écran';
  button.setAttribute('aria-pressed', String(full));
});
if (root.dataset.kind === 'terminal') {
  terminal = new window.Terminal({ cursorBlink: true, fontSize: 14, scrollback: 3000, theme: { background: '#080808', foreground: '#ddd' } });
  fit = new window.FitAddon.FitAddon(); terminal.loadAddon(fit); terminal.open(screen);
  new ResizeObserver(() => {
    fit.fit(); cols = Math.max(20, Math.min(500, terminal.cols)); rows = Math.max(5, Math.min(200, terminal.rows));
    if (terminal.cols !== cols || terminal.rows !== rows) terminal.resize(cols, rows);
  }).observe(screen);
  terminal.onData(data => send(encoder.encode(data))); terminal.focus();
  terminal.onBinary(data => send(Uint8Array.from(data, c => c.charCodeAt(0) & 255)));
} else {
  const { default: RFB } = await import('./vendor/novnc-1.7.0/core/rfb.js');
  channel = new RelayChannel(); rfb = new RFB(screen, channel, { shared: true }); rfb.showDotCursor = true;
  const scale = document.getElementById('remote-scale');
  const resolution = document.getElementById('remote-resolution');
  let nativeSize, resizeTimer, connected = false, lastSize = '';
  function requestResolution() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      // Adaptateur noVNC 1.7 : cette capacité n'a pas d'accesseur public.
      // Ne jamais envoyer SetDesktopSize à un ancien agent qui ne l'annonce pas.
      if (!running || !nativeSize || !rfb._supportsSetDesktopSize) return;
      // Pixels de l'écran et non pixels CSS : avec un affichage à 150 %, un pixel du bureau = un pixel affiché.
      const dpr = window.devicePixelRatio || 1;
      const target = resolutionFor(nativeSize.width, nativeSize.height, Math.floor((screen.clientWidth - 2) * dpr),
                                   Math.floor((screen.clientHeight - 2) * dpr), scale.value);
      if (!target) return;
      const key = `${target.width}x${target.height}`;
      if (key === lastSize) return;
      lastSize = key;
      send(resizePacket(target.width, target.height));
    }, 350);
  }
  const capabilityTimer = setInterval(() => {
    if (!running) { clearInterval(capabilityTimer); clearTimeout(resizeTimer); return; }
    requestResolution();
  }, 1000);
  function updateViewport() {
    const canvas = screen.querySelector('canvas');
    if (!connected || !canvas || !canvas.width || !canvas.height) return;
    if (!nativeSize) nativeSize = {width: canvas.width, height: canvas.height};
    // Ajuster aussi vers le haut si le pilote limite les résolutions disponibles.
    rfb.scaleViewport = scale.value !== 'native';
    // 100 % : un pixel du bureau par pixel de l'écran (net), quelle que soit la mise à l'échelle du navigateur.
    const percent = Math.round(100 * canvas.getBoundingClientRect().width * (window.devicePixelRatio || 1) / canvas.width);
    resolution.textContent = `${canvas.width} × ${canvas.height} · ${percent} %`;
  }
  scale.addEventListener('change', () => { updateViewport(); requestResolution(); });
  new ResizeObserver(() => { updateViewport(); requestResolution(); }).observe(screen);
  new MutationObserver(updateViewport).observe(screen, { childList: true, subtree: true, attributes: true, attributeFilter: ['width', 'height'] });
  rfb.addEventListener('connect', () => { connected = true; updateViewport(); requestResolution(); });
  rfb.addEventListener('desktopname', e => {
    if (e.detail.name.startsWith('loco-secure:')) {
      remoteLabel = 'bureau sécurisé Windows · UAC / verrouillage';
      document.querySelector('.remote-description').textContent = 'Bureau sécurisé Windows · Via l’agent loco';
    } else if (e.detail.name.startsWith('loco-desktop:')) {
      remoteLabel = 'bureau du poste';
      document.querySelector('.remote-description').textContent = 'Session graphique du poste · clavier et souris · Via l’agent loco';
    } else if (e.detail.name.startsWith('loco-console root@')) {
      document.querySelector('.remote-description').textContent = 'Console texte · Shell root · Via l’agent loco';
      remoteLabel = 'console texte du poste · root';
      if (running) message('Connecté · ' + remoteLabel);
    }
  });
  rfb.addEventListener('disconnect', e => { if (running) stop(e.detail.clean ? 'Bureau déconnecté.' : 'La connexion au bureau a été interrompue.', true); });
}
poll();
