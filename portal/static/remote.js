import {resolutionFor, resizePacket} from './remote-sizing.js';
/* Octets RFB/PTY relayés par HTTPS : aucune adresse ni aucun jeton de poste dans le navigateur. */
const root = document.getElementById('remote-session');
const screen = document.getElementById('remote-screen');
const status = document.getElementById('remote-status');
status.title = 'État de la session ; la durée est la latence entre ce navigateur et le serveur loco.';
const closeButton = document.getElementById('remote-close');
const encoder = new TextEncoder();
let running = true, inputSeq = 0, outputAck = 0, queue = new Uint8Array(), pending = null;
let terminal, fit, rfb, channel, failures = 0, cols = 100, rows = 30;
let pollTimer, inFlight = false, quiet = 0, rtt = 0, lastLabel = '';
let remoteLabel = root.dataset.kind === 'terminal' ? 'terminal du poste' : 'bureau du poste';
function schedulePoll(delay = 0) {
  if (!running || inFlight) return;
  clearTimeout(pollTimer); pollTimer = setTimeout(poll, delay);
}
// Pastille d'état : connexion, connecté (avec la latence vers le serveur), bureau sécurisé, fermé.
function message(text, state = 'ok') {
  if (status.textContent !== text) status.textContent = text;
  if (status.dataset.state !== state) status.dataset.state = state;
}
function connectedMessage() {
  const label = 'Connecté · ' + remoteLabel + (rtt ? ` · ${Math.max(1, Math.round(rtt / 5) * 5)} ms` : '');
  if (label !== lastLabel) { lastLabel = label; message(label, remoteLabel.startsWith('bureau sécurisé') ? 'warn' : 'ok'); }
}
// Superposition de la scène : attente de connexion, puis fin de session avec reprise en un clic.
const overlay = document.getElementById('remote-overlay');
function hideOverlay() { overlay.hidden = true; }
function showEnd(title, text) {
  document.getElementById('remote-overlay-spinner').hidden = true;
  document.getElementById('remote-overlay-icon').hidden = false;
  document.getElementById('remote-overlay-title').textContent = title;
  document.getElementById('remote-overlay-text').textContent = text;
  document.getElementById('remote-overlay-actions').hidden = false;
  overlay.hidden = false; overlay.classList.add('ended');
}
document.getElementById('remote-overlay-reconnect').addEventListener('click', () => document.getElementById('remote-reconnect').click());
// Décodage base64 sans fermeture par octet (natif quand le navigateur le propose) : moins de temps par image reçue.
const bytes = typeof Uint8Array.fromBase64 === 'function' ? s => Uint8Array.fromBase64(s) : s => {
  const raw = atob(s), out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
};
const base64 = typeof Uint8Array.prototype.toBase64 === 'function' ? data => data.toBase64() : data => {
  let out = ''; for (let i = 0; i < data.length; i += 8192) out += String.fromCharCode(...data.subarray(i, i + 8192)); return btoa(out);
};
function send(data) {
  const part = data instanceof Uint8Array ? data : new Uint8Array(data);
  if (queue.length + part.length > 512 * 1024) { stop('La saisie dépasse la capacité de la session.', true); return; }
  const combined = new Uint8Array(queue.length + part.length); combined.set(queue); combined.set(part, queue.length); queue = combined;
  quiet = 0; schedulePoll();
}
function closeRequest(beacon = false) {
  const data = new URLSearchParams({ csrf: root.dataset.csrf, close: '1' });
  if (beacon) navigator.sendBeacon(root.dataset.io, data);
  else return fetch(root.dataset.io, { method: 'POST', body: data, credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' }, keepalive: true });
}
function stop(text = 'Session fermée.', notify = false) {
  if (!running) return;
  running = false; clearTimeout(pollTimer); closeButton.disabled = true; message(text, 'closed');
  document.getElementById('remote-reconnect').hidden = false;
  showEnd(text, 'La connexion au poste est fermée. Ouvrez une nouvelle session pour reprendre.');
  if (channel) { channel.readyState = 3; if (channel.onclose) channel.onclose({ code: 1000, reason: text }); }
  if (terminal) terminal.options.disableStdin = true;
  if (document.fullscreenElement === root) document.exitFullscreen().catch(() => {});
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
    const started = performance.now();
    const response = await fetch(root.dataset.io, { method: 'POST', body, credentials: 'same-origin',
      headers: { 'X-Requested-With': 'fetch' }, signal: AbortSignal.timeout(10000) });
    if ([401,403,404,409,413].includes(response.status)) { stop('Session indisponible ou accès révoqué.', true); return; }
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const data = await response.json();
    if (!running) return;
    const elapsed = performance.now() - started; rtt = rtt ? rtt * 0.85 + elapsed * 0.15 : elapsed;
    failures = 0; busy = data.packets.length > 0;
    if (pending && data.ack >= pending.seq) pending = null;
    if (data.state === 'active') {
      connectedMessage();
      if (terminal) hideOverlay();
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
    lastLabel = ''; message('Reconnexion au serveur…', 'warn');
  } finally { inFlight = false; }
  // Interroger tout de suite tant que ça bouge ; au repos prolongé (> 1 s), espacer un peu pour soulager le serveur.
  if (failures) schedulePoll(1000);
  else if (busy || pending || queue.length) { quiet = 0; schedulePoll(0); }
  else schedulePoll(++quiet < 40 ? 25 : 80);
}
closeButton.addEventListener('click', () => stop('Session fermée.', true));
window.addEventListener('pagehide', () => { if (running) { stop('Session fermée.'); closeRequest(true); } });
const fullscreenButton = document.getElementById('remote-fullscreen');
fullscreenButton.addEventListener('click', () => {
  if (document.fullscreenElement) document.exitFullscreen(); else root.requestFullscreen().catch(() => message('Le navigateur a refusé le plein écran.', 'warn'));
});
document.addEventListener('fullscreenchange', () => {
  const full = document.fullscreenElement === root;
  fullscreenButton.querySelector('span').textContent = full ? 'Quitter le plein écran' : 'Plein écran';
  fullscreenButton.setAttribute('aria-pressed', String(full));
  root.classList.toggle('is-fullscreen', full);
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
  const viewport = document.getElementById('remote-viewport');
  const resolutionMode = document.getElementById('remote-resolution-mode');
  const zoom = document.getElementById('remote-zoom');
  const readout = document.getElementById('remote-readout');
  let nativeSize, resizeTimer, connected = false, lastSize = '';
  const dpr = () => window.devicePixelRatio || 1;

  // Curseur du poste à la même échelle que le bureau affiché : net à 100 %, proportionné en « Ajuster » ou en zoom.
  const cursor = rfb._cursor, baseChange = cursor.change.bind(cursor);
  let shape = null, shapeScale = 1;
  function scaledShape(rgba, hotx, hoty, w, h, s) {
    const W = Math.max(1, Math.min(128, Math.round(w * s))), H = Math.max(1, Math.min(128, Math.round(h * s)));
    const source = document.createElement('canvas'); source.width = w; source.height = h;
    source.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(rgba), w, h), 0, 0);
    const target = document.createElement('canvas'); target.width = W; target.height = H;
    const ctx = target.getContext('2d'); ctx.imageSmoothingQuality = 'high'; ctx.drawImage(source, 0, 0, W, H);
    return [ctx.getImageData(0, 0, W, H).data, Math.min(W - 1, Math.round(hotx * W / w)), Math.min(H - 1, Math.round(hoty * H / h)), W, H];
  }
  cursor.change = (rgba, hotx, hoty, w, h) => {
    shape = [rgba, hotx, hoty, w, h];
    shapeScale = Math.max(0.6, Math.min(2, (rfb._display && rfb._display.scale) || 1));
    if (!w || !h || Math.abs(shapeScale - 1) < 0.05) return baseChange(rgba, hotx, hoty, w, h);
    return baseChange(...scaledShape(rgba, hotx, hoty, w, h, shapeScale));
  };
  function refreshCursor() {
    const s = Math.max(0.6, Math.min(2, (rfb._display && rfb._display.scale) || 1));
    if (shape && Math.abs(s - shapeScale) > 0.02) cursor.change(...shape);
  }

  // Réglages d'affichage mémorisés par navigateur.
  const PREFS = 'loco-remote-display';
  try {
    const saved = JSON.parse(localStorage.getItem(PREFS) || '{}');
    if ([...resolutionMode.options].some(o => o.value === saved.res)) resolutionMode.value = saved.res;
    if ([...zoom.options].some(o => o.value === saved.zoom)) zoom.value = saved.zoom;
  } catch { /* stockage indisponible : réglages par défaut */ }
  const savePrefs = () => { try { localStorage.setItem(PREFS, JSON.stringify({res: resolutionMode.value, zoom: zoom.value})); } catch { /* ignoré */ } };

  // « Adaptée à la fenêtre » : la résolution du poste suit la fenêtre (divisée par le zoom, qui agrandit alors l'interface)
  // et l'image est toujours ajustée : jamais d'ascenseurs, même si le pilote impose un mode un peu différent.
  // « Résolution du poste » : zoom fixe sur l'échelle propre de noVNC (pointeur juste à tout zoom), avec défilement.
  const factor = () => zoom.value === 'fit' ? 1 : parseInt(zoom.value, 10) / 100;
  const fitting = () => resolutionMode.value === 'adapt' || zoom.value === 'fit';
  const baseUpdateScale = rfb._updateScale.bind(rfb);
  rfb._updateScale = function () {
    if (fitting()) baseUpdateScale();
    else {
      rfb._display.scale = factor() / dpr();   // 100 % = un pixel du bureau par pixel physique (net)
      if (typeof rfb._fixScrollbars === 'function') rfb._fixScrollbars();
    }
    refreshCursor();
  };
  function applyDisplay() {
    const fit = fitting();
    rfb.clipViewport = false;
    rfb.scaleViewport = fit;   // déclenche _updateScale (surcharge ci-dessus)
    viewport.classList.toggle('fit', fit);
    viewport.classList.toggle('zoomed', !fit);
    rfb._updateScale();
    updateReadout();
  }
  function requestResolution() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      // Adaptateur noVNC 1.7 : cette capacité n'a pas d'accesseur public. Ne jamais envoyer SetDesktopSize à un ancien agent qui ne l'annonce pas.
      if (!running || !nativeSize || !rfb._supportsSetDesktopSize) return;
      const target = resolutionMode.value === 'native'
        ? {width: nativeSize.width, height: nativeSize.height}
        : resolutionFor(Math.floor(viewport.clientWidth * dpr() / factor()), Math.floor(viewport.clientHeight * dpr() / factor()));
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
  function updateReadout() {
    const canvas = screen.querySelector('canvas');
    if (!connected || !canvas || !canvas.width || !canvas.height) return;
    if (!nativeSize) nativeSize = {width: canvas.width, height: canvas.height};
    const percent = Math.round(100 * canvas.getBoundingClientRect().width * dpr() / canvas.width);
    readout.textContent = `${canvas.width} × ${canvas.height} · ${percent} %`;
  }
  function displayChanged() { savePrefs(); applyDisplay(); requestResolution(); if (connected) rfb.focus(); }
  resolutionMode.addEventListener('change', () => { lastSize = ''; displayChanged(); });
  zoom.addEventListener('change', displayChanged);
  new ResizeObserver(() => { updateReadout(); requestResolution(); }).observe(viewport);
  new MutationObserver(() => { updateReadout(); if (!fitting()) rfb._updateScale(); })
    .observe(screen, { childList: true, subtree: true, attributes: true, attributeFilter: ['width', 'height'] });
  rfb.addEventListener('connect', () => { connected = true; hideOverlay(); applyDisplay(); requestResolution(); });

  // Presse-papiers partagé : noVNC émet « clipboard » quand le poste copie (ServerCutText) et clipboardPasteFrom() renvoie vers le poste (ClientCutText).
  const clip = document.getElementById('remote-clipboard');
  const clipArea = clip.querySelector('textarea');
  const clipHint = document.getElementById('remote-clipboard-hint');
  const clipAuto = document.getElementById('remote-clipboard-auto');
  const CLIP = 'loco-remote-clipboard-auto';
  let lastShared = '';
  rfb.addEventListener('clipboard', e => {
    clipArea.value = lastShared = e.detail.text || '';
    if (navigator.clipboard && navigator.clipboard.writeText)
      navigator.clipboard.writeText(clipArea.value).then(
        () => { clipHint.textContent = 'Copié depuis le poste et placé dans votre presse-papiers local.'; },
        () => { clipHint.textContent = 'Copié depuis le poste. Cliquez dans la zone puis Ctrl+C pour le récupérer localement.'; });
    else clipHint.textContent = 'Copié depuis le poste.';
  });
  document.getElementById('remote-clipboard-read').addEventListener('click', async () => {
    try { clipArea.value = await navigator.clipboard.readText(); clipArea.focus(); }
    catch { clipHint.textContent = 'Lecture du presse-papiers local refusée par le navigateur ; collez (Ctrl+V) dans la zone, puis « Envoyer au poste ».'; clipArea.focus(); }
  });
  clip.addEventListener('submit', e => {
    e.preventDefault();
    if (!connected) return;
    rfb.clipboardPasteFrom(lastShared = clipArea.value.slice(0, 100000));
    clipHint.textContent = 'Envoyé au presse-papiers du poste. Collez-y avec Ctrl+V.';
    rfb.focus();
  });
  // Synchronisation automatique (sur demande) : au clic dans le bureau, le presse-papiers local est envoyé s'il a changé ;
  // Ctrl+V sur le poste colle alors directement ce qui a été copié ailleurs.
  try { clipAuto.checked = localStorage.getItem(CLIP) === '1'; } catch { /* ignoré */ }
  clipAuto.addEventListener('change', async () => {
    try { localStorage.setItem(CLIP, clipAuto.checked ? '1' : '0'); } catch { /* ignoré */ }
    if (!clipAuto.checked) return;
    try { await navigator.clipboard.readText(); clipHint.textContent = 'Synchronisation activée : votre presse-papiers part vers le poste à chaque clic sur le bureau.'; }
    catch { clipAuto.checked = false; clipHint.textContent = 'Le navigateur refuse l’accès au presse-papiers : synchronisation impossible ici.'; }
  });
  async function shareLocalClipboard() {
    if (!clipAuto.checked || !connected || !navigator.clipboard || !navigator.clipboard.readText) return;
    try {
      const text = (await navigator.clipboard.readText()).slice(0, 100000);
      if (text && text !== lastShared) { rfb.clipboardPasteFrom(lastShared = text); clipArea.value = text; }
    } catch { /* autorisation retirée ou page sans focus : rien à faire */ }
  }

  // Plein écran bureau : les commandes passent dans un rail gauche rétractable, et le curseur logiciel de noVNC
  // (un canvas ajouté au <body>, donc masqué et décalé en plein écran) est replacé dans l'élément plein écran.
  const rail = document.getElementById('remote-rail');
  const railBody = document.getElementById('remote-rail-body');
  const railToggle = document.getElementById('remote-rail-toggle');
  const actions = root.querySelector('.actions');
  const railBar = document.getElementById('remote-toolbar');
  const home = {s: [status.parentNode, status.nextSibling], a: [actions.parentNode, actions.nextSibling], t: [railBar.parentNode, railBar.nextSibling]};
  const setRail = open => { rail.classList.toggle('open', open); railToggle.setAttribute('aria-expanded', String(open)); };
  railToggle.addEventListener('click', () => setRail(!rail.classList.contains('open')));
  const cursorCanvas = () => rfb._cursor && rfb._cursor._canvas;
  function fullscreen(on) {
    const cc = cursorCanvas();
    if (on) {
      railBody.append(status, actions, railBar);
      rail.hidden = false; setRail(true);
      if (cc && cc.parentNode === document.body) root.appendChild(cc);
    } else {
      home.s[0].insertBefore(status, home.s[1]); home.a[0].insertBefore(actions, home.a[1]); home.t[0].insertBefore(railBar, home.t[1]);
      setRail(false); rail.hidden = true;
      if (cc && cc.parentNode === root) document.body.appendChild(cc);
    }
    lastSize = ''; updateReadout(); requestResolution();
  }
  document.addEventListener('fullscreenchange', () => fullscreen(document.fullscreenElement === root));
  // Un clic dans le bureau replie le rail (il reste à portée par sa poignée) et partage le presse-papiers si demandé.
  viewport.addEventListener('pointerdown', () => {
    if (root.classList.contains('is-fullscreen') && rail.classList.contains('open')) setRail(false);
    shareLocalClipboard();
  }, true);

  // Panneau de commandes : combinaisons, touches maintenues et texte tapé (keysyms X11, indépendants du clavier).
  const KEYS = {ctrl: [0xffe3, 'ControlLeft'], alt: [0xffe9, 'AltLeft'], shift: [0xffe1, 'ShiftLeft'], super: [0xffeb, 'MetaLeft'],
                delete: [0xffff, 'Delete'], escape: [0xff1b, 'Escape'], tab: [0xff09, 'Tab'], f4: [0xffc1, 'F4'],
                print: [0xff61, 'PrintScreen'], menu: [0xff67, 'ContextMenu'], enter: [0xff0d, 'Enter'],
                r: [0x72, 'KeyR'], e: [0x65, 'KeyE'], d: [0x64, 'KeyD'], l: [0x6c, 'KeyL']};
  const held = new Set();
  function combo(names) {
    const keys = names.split(' ').map(n => KEYS[n]).filter(Boolean);
    keys.forEach(([sym, code]) => rfb.sendKey(sym, code, true));
    keys.slice().reverse().forEach(([sym, code]) => rfb.sendKey(sym, code, false));
  }
  function releaseHeld() {
    held.forEach(name => rfb.sendKey(KEYS[name][0], KEYS[name][1], false));
    held.clear();
    document.querySelectorAll('[data-hold]').forEach(b => b.setAttribute('aria-pressed', 'false'));
  }
  const toolbar = document.getElementById('remote-toolbar');
  toolbar.addEventListener('click', e => {
    const button = e.target.closest('[data-keys], [data-hold]');
    if (!button || !connected) return;
    if (button.dataset.keys) {
      // Ctrl+Alt+Suppr : séquence dédiée (l'agent Windows la transmet au système comme la vraie séquence sécurisée).
      if (button.dataset.keys === 'ctrl alt delete') rfb.sendCtrlAltDel(); else combo(button.dataset.keys);
      releaseHeld();
      const menu = button.closest('details'); if (menu) menu.open = false;
    } else {
      const name = button.dataset.hold, on = !held.has(name);
      rfb.sendKey(KEYS[name][0], KEYS[name][1], on);
      if (on) held.add(name); else held.delete(name);
      button.setAttribute('aria-pressed', String(on));
    }
    rfb.focus();
  });
  // Une touche maintenue se relâche au prochain clic dans le bureau, comme sur un clavier tactile.
  screen.addEventListener('mouseup', () => { if (held.size) setTimeout(releaseHeld, 50); });
  document.getElementById('remote-type').addEventListener('submit', e => {
    e.preventDefault();
    const area = e.target.querySelector('textarea');
    if (!connected || !area.value) return;
    releaseHeld();
    for (const ch of area.value.slice(0, 2000)) {
      const cp = ch.codePointAt(0);
      if (ch === '\n') { rfb.sendKey(0xff0d, 'Enter'); continue; }
      if (ch === '\t') { rfb.sendKey(0xff09, 'Tab'); continue; }
      if (cp < 32) continue;
      rfb.sendKey(cp <= 0xff ? cp : 0x01000000 + cp, null);
    }
    area.value = '';
    e.target.closest('details').open = false;
    rfb.focus();
  });
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
    }
    lastLabel = ''; if (running) connectedMessage();
  });
  rfb.addEventListener('disconnect', e => { if (running) stop(e.detail.clean ? 'Bureau déconnecté.' : 'La connexion au bureau a été interrompue.', true); });
}
poll();
