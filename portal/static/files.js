/* Gestionnaire de fichiers du poste : requêtes et fragments relayés par HTTPS, comme les terminaux.
   Aucune adresse ni aucun jeton de poste dans le navigateur ; l'agent exécute chaque opération en root/SYSTEM. */
const root = document.getElementById('file-manager');
if (!root) throw new Error('loco: gestionnaire de fichiers absent');  // poste hors ligne : panneau non rendu
const CHUNK = 192 * 1024;           // fragment de lecture/écriture, sous le paquet de 256 Kio du relais
const EDIT_MAX = 2 * 1024 * 1024;   // taille maxi d'un fichier ouvert dans l'éditeur de texte
const enc = new TextEncoder(), dec = new TextDecoder();
const bytes = typeof Uint8Array.fromBase64 === 'function' ? s => Uint8Array.fromBase64(s) : s => {
  const raw = atob(s), out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
};
const base64 = typeof Uint8Array.prototype.toBase64 === 'function' ? d => d.toBase64() : d => {
  let out = ''; for (let i = 0; i < d.length; i += 8192) out += String.fromCharCode(...d.subarray(i, i + 8192)); return btoa(out);
};
const human = window.lrBytes || (n => n + ' o');
const toast = window.lrToast || (m => {});
const confirmDialog = window.lrConfirm || ((t, x, ok) => Promise.resolve(window.confirm(x || t)));

// ---------------------------------------------------------------- transport (octets relayés, acquittés)
let io = null, running = false, inputSeq = 0, outputAck = 0, queue = new Uint8Array(0), pending = null;
let inFlight = false, failures = 0, quiet = 0, pollTimer = null, nextId = 1, inbuf = new Uint8Array(0);
const waiting = new Map();          // identifiant de requête -> {resolve, reject}

function schedulePoll(delay = 0) {
  if (!running || inFlight) return;
  clearTimeout(pollTimer); pollTimer = setTimeout(poll, delay);
}
function enqueue(frame) {
  const combined = new Uint8Array(queue.length + frame.length);
  combined.set(queue); combined.set(frame, queue.length); queue = combined;
  quiet = 0; schedulePoll();
}
function frame(header, data) {
  const hb = enc.encode(JSON.stringify(header)), body = data || new Uint8Array(0);
  const out = new Uint8Array(8 + hb.length + body.length), view = new DataView(out.buffer);
  view.setUint32(0, hb.length); view.setUint32(4, body.length);
  out.set(hb, 8); out.set(body, 8 + hb.length);
  return out;
}
function request(op, extra, data) {
  if (!running) return Promise.reject(new Error('Session fermée.'));
  const id = nextId++;
  return new Promise((resolve, reject) => {
    waiting.set(id, { resolve, reject });
    enqueue(frame(Object.assign({ id, op }, extra), data));
  });
}
function feed(chunk) {
  const merged = new Uint8Array(inbuf.length + chunk.length);
  merged.set(inbuf); merged.set(chunk, inbuf.length); inbuf = merged;
  for (;;) {
    if (inbuf.length < 8) break;
    const view = new DataView(inbuf.buffer, inbuf.byteOffset, inbuf.byteLength);
    const hlen = view.getUint32(0), dlen = view.getUint32(4);
    if (inbuf.length < 8 + hlen + dlen) break;
    let header;
    try { header = JSON.parse(dec.decode(inbuf.subarray(8, 8 + hlen))); }
    catch { stop('Réponse du poste illisible.'); return; }
    const data = inbuf.slice(8 + hlen, 8 + hlen + dlen);
    inbuf = inbuf.slice(8 + hlen + dlen);
    const r = waiting.get(header.id);
    if (!r) continue;
    waiting.delete(header.id);
    if (header.ok) r.resolve({ header, data });
    else r.reject(new Error(header.error || 'Opération refusée par le poste.'));
  }
}
async function poll() {
  if (!running || inFlight) return;
  inFlight = true; let busy = false;
  if (!pending && queue.length) {
    const data = queue.subarray(0, 64 * 1024); queue = queue.slice(data.length);
    pending = { seq: ++inputSeq, data: base64(data) };
  }
  const body = new URLSearchParams({ csrf: root.dataset.csrf, ack: String(outputAck) });
  if (pending) { body.set('seq', String(pending.seq)); body.set('data', pending.data); }
  try {
    const response = await fetch(io, { method: 'POST', body, credentials: 'same-origin',
      headers: { 'X-Requested-With': 'fetch' }, signal: AbortSignal.timeout(20000) });
    if ([401, 403, 404, 409, 413].includes(response.status)) { stop('Session de fichiers indisponible ou accès révoqué.', true); return; }
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const d = await response.json();
    if (!running) return;
    failures = 0; busy = d.packets.length > 0;
    if (pending && d.ack >= pending.seq) pending = null;
    for (const packet of d.packets) { if (packet.seq <= outputAck) continue; feed(bytes(packet.data)); outputAck = packet.seq; }
    if (d.state === 'closed' && !d.packets.length) { stop(d.error || 'Session fermée.'); return; }
  } catch (error) {
    if (++failures >= 10) { stop('Connexion au serveur interrompue.', true); return; }
  } finally { inFlight = false; }
  if (!running) return;
  if (busy || pending || queue.length) { quiet = 0; schedulePoll(0); }
  else schedulePoll(++quiet < 20 ? 120 : 400);
}
function closeRequest(beacon = false) {
  const data = new URLSearchParams({ csrf: root.dataset.csrf, close: '1' });
  if (beacon) navigator.sendBeacon(io, data);
  else return fetch(io, { method: 'POST', body: data, credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' }, keepalive: true }).catch(() => {});
}
function stop(text, notify = false) {
  if (!running) return;
  running = false; clearTimeout(pollTimer);
  waiting.forEach(r => r.reject(new Error(text || 'Session fermée.'))); waiting.clear();
  document.body.removeAttribute('data-no-reload');
  setStatus(text || 'Session fermée.', 'err');
  if (notify) closeRequest();
}

// ---------------------------------------------------------------- interface
const pane = root.querySelector('.files-pane');
const tbody = root.querySelector('.files-table tbody');
const crumbs = root.querySelector('.files-crumbs');
const rootsEl = root.querySelector('.files-roots');
const statusEl = root.querySelector('.files-status');
const emptyEl = root.querySelector('.files-empty');
const truncEl = root.querySelector('.files-truncated');
const transfersEl = root.querySelector('.files-transfers');
const pasteBtn = root.querySelector('[data-files="paste"]');
const hiddenToggle = root.querySelector('[data-files="hidden"]');
const fileInput = root.querySelector('input[type="file"]');
const editor = root.querySelector('.files-editor');
const editorArea = editor.querySelector('textarea');
const editorPath = editor.querySelector('.files-editor-path');
const editorNote = editor.querySelector('.files-editor-note');
let info = null, current = null, showHidden = false, clip = null, listing = [];

function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
function setStatus(text, kind) { statusEl.textContent = text || ''; statusEl.dataset.kind = kind || ''; }
function fmtDate(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' }) + ' ' +
    d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}
function sep() { return info && info.sep ? info.sep : '/'; }
function join(dir, name) {
  if (sep() === '\\') return dir.replace(/\\+$/, '') + '\\' + name;
  return (dir === '/' ? '' : dir.replace(/\/+$/, '')) + '/' + name;
}
function icon(type, link) {
  if (type === 'dir') return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
  if (link) return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"/></svg>';
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>';
}
const TEXT_RE = /\.(txt|log|conf|cfg|ini|json|ya?ml|xml|md|csv|tsv|sh|bash|zsh|ps1|bat|py|pl|rb|js|ts|css|html?|service|env|toml|properties|list|sql|c|h|cpp|go|rs|java|php|lua)$/i;
function isText(name) { return TEXT_RE.test(name) || !name.includes('.'); }

function renderCrumbs(path, parent) {
  const windows = sep() === '\\';
  const parts = [];
  if (!windows) parts.push({ label: '/', path: '/' });
  const trimmed = path.replace(/[\\/]+$/, '');
  const pieces = trimmed.split(/[\\/]/).filter(Boolean);
  let acc = windows ? '' : '';
  pieces.forEach((piece, i) => {
    acc = windows ? (i === 0 ? piece + '\\' : join(acc, piece)) : join(acc || '/', piece);
    parts.push({ label: piece, path: acc });
  });
  crumbs.innerHTML = parts.map((p, i) =>
    '<button type="button" class="files-crumb" data-path="' + esc(p.path) + '">' + esc(p.label) + '</button>' +
    (i < parts.length - 1 ? '<span class="files-crumb-sep" aria-hidden="true">' + (windows ? '\\' : '/') + '</span>' : '')
  ).join('');
  root.querySelector('[data-files="up"]').disabled = !parent;
}
function renderRoots() {
  if (!info) return;
  rootsEl.innerHTML = info.roots.map(r =>
    '<button type="button" class="chip files-root" data-path="' + esc(r.path) + '">' + esc(r.name) + '</button>').join('');
}
function renderList(entries, truncated) {
  listing = entries;
  const rows = entries.filter(e => showHidden || !e.name.startsWith('.'));
  emptyEl.classList.toggle('hidden', rows.length > 0);
  truncEl.classList.toggle('hidden', !truncated);
  tbody.innerHTML = rows.map(e => {
    const isDir = e.type === 'dir';
    const nameCell = isDir
      ? '<button type="button" class="files-name link" data-open-dir>' + icon('dir', e.link) + '<span>' + esc(e.name) + '</span></button>'
      : '<span class="files-name">' + icon(e.type, e.link) + '<span>' + esc(e.name) + '</span></span>';
    const canEdit = e.type === 'file' && isText(e.name) && e.size <= EDIT_MAX;
    const windows = Number(root.dataset.windows) === 1;
    const menu =
      '<details class="menu files-menu"><summary class="btn icon sm" aria-label="Actions"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg></summary>' +
      '<div class="menu-list" role="menu">' +
      (e.type === 'file' ? '<button type="button" class="menu-item" data-act="download">Télécharger</button>' : '') +
      (canEdit ? '<button type="button" class="menu-item" data-act="edit">Modifier le texte</button>' : '') +
      '<button type="button" class="menu-item" data-act="rename">Renommer</button>' +
      '<button type="button" class="menu-item" data-act="copy">Copier</button>' +
      '<button type="button" class="menu-item" data-act="cut">Déplacer…</button>' +
      (windows ? '' : '<button type="button" class="menu-item" data-act="chmod">Droits…</button>') +
      '<button type="button" class="menu-item danger" data-act="delete">Supprimer</button>' +
      '</div></details>';
    return '<tr data-name="' + esc(e.name) + '" data-type="' + e.type + '">' +
      '<td>' + nameCell + '</td>' +
      '<td class="num muted">' + (e.type === 'file' ? human(e.size) : '—') + '</td>' +
      '<td class="muted nowrap small">' + fmtDate(e.mtime) + '</td>' +
      '<td class="num mono small muted">' + (e.mode ? e.mode.toString(8).padStart(3, '0') : '—') + '</td>' +
      '<td class="right">' + menu + '</td></tr>';
  }).join('');
}

async function navigate(path) {
  setStatus('Chargement…');
  try {
    const { header } = await request('list', { path });
    current = header.path;
    renderCrumbs(header.path, header.parent);
    renderList(header.entries, header.truncated);
    setStatus('');
  } catch (e) { setStatus(e.message, 'err'); }
}
function refresh() { if (current) navigate(current); }

// ---------------------------------------------------------------- transferts (barre de progression par fichier)
function transfer(label) {
  const el = document.createElement('div');
  el.className = 'files-xfer';
  el.innerHTML = '<div class="files-xfer-row"><span class="files-xfer-name mono small"></span><span class="files-xfer-pct small muted"></span></div><div class="meter"><i></i></div>';
  el.querySelector('.files-xfer-name').textContent = label;
  transfersEl.appendChild(el);
  return {
    set(done, total) {
      const pct = total ? Math.min(100, Math.round(100 * done / total)) : 0;
      el.querySelector('i').style.width = pct + '%';
      el.querySelector('.files-xfer-pct').textContent = total ? human(done) + ' / ' + human(total) : human(done);
    },
    done(text, kind) {
      el.querySelector('.files-xfer-pct').textContent = text;
      if (kind) el.classList.add(kind);
      setTimeout(() => el.remove(), kind === 'err' ? 6000 : 2500);
    }
  };
}
async function download(name) {
  const path = join(current, name);
  const t = transfer('↓ ' + name);
  const parts = []; let offset = 0, size = 0;
  try {
    for (;;) {
      const { header, data } = await request('read', { path, offset, length: CHUNK });
      size = header.size; parts.push(data); offset += data.length;
      t.set(offset, size);
      if (header.eof || data.length === 0) break;
    }
    const blob = new Blob(parts);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    t.done('terminé');
  } catch (e) { t.done(e.message, 'err'); }
}
async function upload(file) {
  const path = join(current, file.name);
  const t = transfer('↑ ' + file.name);
  let offset = 0;
  try {
    while (offset < file.size || offset === 0) {
      const slice = file.slice(offset, offset + CHUNK);
      const buf = new Uint8Array(await slice.arrayBuffer());
      const final = offset + buf.length >= file.size;
      await request('write', { path, offset, final }, buf);
      offset += buf.length;
      t.set(offset, file.size);
      if (final) break;
    }
    t.done('terminé');
    refresh();
  } catch (e) {
    t.done(e.message, 'err');
    request('write_cancel', { path }).catch(() => {});
  }
}

// ---------------------------------------------------------------- invites (nom, droits)
function prompt(title, label, value) {
  return new Promise(resolve => {
    const dlg = document.createElement('dialog');
    dlg.className = 'files-prompt';
    dlg.innerHTML = '<form method="dialog"><div class="dlg-h"><h2></h2></div><div class="dlg-b"><label class="field"><span></span><input type="text"></label></div>' +
      '<div class="dlg-f"><button type="button" class="btn" value="cancel">Annuler</button><button type="button" class="btn primary" value="ok">Valider</button></div></form>';
    dlg.querySelector('h2').textContent = title;
    dlg.querySelector('.field span').textContent = label;
    const input = dlg.querySelector('input');
    input.value = value || '';
    const done = v => { if (dlg.open) dlg.close(); dlg.remove(); resolve(v); };
    dlg.querySelector('[value="ok"]').onclick = () => done(input.value.trim());
    dlg.querySelector('[value="cancel"]').onclick = () => done(null);
    dlg.onclose = () => done(null);
    root.appendChild(dlg); dlg.showModal(); input.focus(); input.select();
    input.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); done(input.value.trim()); } });
  });
}

// ---------------------------------------------------------------- éditeur de texte
let editing = null;
async function openEditor(name) {
  const path = join(current, name);
  editorPath.textContent = path;
  editorArea.value = ''; editorNote.textContent = 'Chargement…';
  editor.querySelector('h2').textContent = 'Modifier ' + name;
  editing = path;
  editor.showModal();
  try {
    const parts = []; let offset = 0;
    for (;;) {
      const { header, data } = await request('read', { path, offset, length: CHUNK });
      parts.push(data); offset += data.length;
      if (header.eof || data.length === 0) break;
    }
    editorArea.value = dec.decode(concat(parts));
    editorNote.textContent = human(offset);
  } catch (e) { editorNote.textContent = e.message; }
}
function concat(parts) {
  let total = 0; parts.forEach(p => total += p.length);
  const out = new Uint8Array(total); let at = 0;
  parts.forEach(p => { out.set(p, at); at += p.length; });
  return out;
}
async function saveEditor() {
  if (!editing) return;
  const data = enc.encode(editorArea.value);
  editorNote.textContent = 'Enregistrement…';
  try {
    let offset = 0;
    do {
      const slice = data.subarray(offset, offset + CHUNK);
      const final = offset + slice.length >= data.length;
      await request('write', { path: editing, offset, final }, slice);
      offset += slice.length;
    } while (offset < data.length);
    editorNote.textContent = 'Enregistré.';
    toast('Fichier enregistré.', 'ok');
    editor.close(); editing = null; refresh();
  } catch (e) { editorNote.textContent = e.message; }
}

// ---------------------------------------------------------------- événements
root.addEventListener('click', async e => {
  const crumb = e.target.closest('.files-crumb, .files-root');
  if (crumb) { navigate(crumb.dataset.path); return; }
  const nav = e.target.closest('[data-files]');
  if (nav) {
    const act = nav.dataset.files;
    if (act === 'refresh') refresh();
    else if (act === 'up') { if (!nav.disabled) navigateUp(); }
    else if (act === 'mkdir') makeDir();
    else if (act === 'paste') doPaste();
    return;
  }
  const dir = e.target.closest('[data-open-dir]');
  if (dir) { navigate(join(current, dir.closest('tr').dataset.name)); return; }
  const item = e.target.closest('.menu-item[data-act]');
  if (item) { handleAction(item.dataset.act, item.closest('tr')); return; }
});
function navigateUp() {
  const parts = crumbs.querySelectorAll('.files-crumb');
  if (parts.length >= 2) navigate(parts[parts.length - 2].dataset.path);
}
async function makeDir() {
  const name = await prompt('Nouveau dossier', 'Nom du dossier', '');
  if (!name) return;
  try { await request('mkdir', { path: join(current, name) }); refresh(); }
  catch (err) { toast(err.message, 'error'); }
}
async function handleAction(act, row) {
  const name = row.dataset.name, type = row.dataset.type, path = join(current, name);
  if (act === 'download') return download(name);
  if (act === 'edit') return openEditor(name);
  if (act === 'rename') {
    const next = await prompt('Renommer', 'Nouveau nom', name);
    if (!next || next === name) return;
    try { await request('rename', { path, name: next }); refresh(); } catch (e) { toast(e.message, 'error'); }
    return;
  }
  if (act === 'copy' || act === 'cut') {
    clip = { path, name, type, mode: act === 'copy' ? 'copy' : 'move' };
    pasteBtn.hidden = false;
    pasteBtn.textContent = (act === 'copy' ? 'Coller' : 'Déplacer') + ' « ' + name + ' »';
    toast((act === 'copy' ? 'Copié' : 'À déplacer') + ' : ' + name + '. Ouvrez le dossier cible puis « ' + (act === 'copy' ? 'Coller' : 'Déplacer') + ' ».', 'ok');
    return;
  }
  if (act === 'chmod') {
    const mode = await prompt('Droits (octal)', 'Mode, par exemple 644 ou 755', (row.querySelector('.mono') || {}).textContent || '644');
    if (!mode) return;
    try { await request('chmod', { path, mode: parseInt(mode, 8) }); refresh(); } catch (e) { toast(e.message, 'error'); }
    return;
  }
  if (act === 'delete') {
    const ok = await confirmDialog('Supprimer ' + name, type === 'dir'
      ? 'Supprimer le dossier « ' + name +' » et tout son contenu sur le poste ? Cette action est définitive.'
      : 'Supprimer « ' + name + ' » sur le poste ? Cette action est définitive.', 'Supprimer');
    if (!ok) return;
    try { await request('delete', { path }); refresh(); } catch (e) { toast(e.message, 'error'); }
  }
}
async function doPaste() {
  if (!clip) return;
  try {
    await request(clip.mode, { path: clip.path, to: current });
    toast((clip.mode === 'copy' ? 'Copié' : 'Déplacé') + ' : ' + clip.name, 'ok');
    clip = null; pasteBtn.hidden = true; refresh();
  } catch (e) { toast(e.message, 'error'); }
}
hiddenToggle.addEventListener('click', () => {
  showHidden = !showHidden;
  const label = showHidden ? 'Masquer les fichiers cachés' : 'Afficher les fichiers cachés';
  hiddenToggle.setAttribute('aria-pressed', String(showHidden)); hiddenToggle.setAttribute('aria-label', label); hiddenToggle.title = label;
  renderList(listing, !truncEl.classList.contains('hidden'));
});
fileInput.addEventListener('change', () => { Array.from(fileInput.files).forEach(upload); fileInput.value = ''; });
editor.querySelector('[data-editor="save"]').addEventListener('click', saveEditor);
editor.querySelector('[data-editor="cancel"]').addEventListener('click', () => { editor.close(); editing = null; });

// Glisser-déposer : téléverse dans le dossier courant.
let dragDepth = 0;
pane.addEventListener('dragenter', e => { e.preventDefault(); if (++dragDepth === 1) pane.classList.add('dropping'); });
pane.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
pane.addEventListener('dragleave', e => { if (--dragDepth <= 0) { dragDepth = 0; pane.classList.remove('dropping'); } });
pane.addEventListener('drop', e => {
  e.preventDefault(); dragDepth = 0; pane.classList.remove('dropping');
  Array.from(e.dataTransfer.files).forEach(upload);
});

// ---------------------------------------------------------------- cycle de vie : ouverture paresseuse à l'onglet
async function openSession() {
  if (running || io) return;
  setStatus('Ouverture de la session…');
  document.body.dataset.noReload = '1';
  try {
    const body = new URLSearchParams({ csrf: root.dataset.csrf, kind: 'files' });
    const response = await fetch(root.dataset.open, { method: 'POST', body, credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } });
    if (!response.ok) throw new Error(response.status === 409 ? 'Agent trop ancien ou trop de sessions ouvertes.' : 'Ouverture refusée (' + response.status + ').');
    const d = await response.json();
    io = d.io; running = true; failures = 0;
    root.hidden = false;
    poll();
    const { header } = await request('hello', {});
    info = header; renderRoots();
    navigate(info.home || (info.sep === '\\' ? (info.roots[0] && info.roots[0].path) : '/'));
  } catch (e) {
    document.body.removeAttribute('data-no-reload');
    setStatus(e.message, 'err'); root.hidden = false;
  }
}
function isActive() {
  const panel = root.closest('.tab-panel');
  return panel && panel.classList.contains('on');
}
function maybeOpen() { if (isActive() && !io) openSession(); }

// Les onglets basculent via app.js (clic ou #ancre) ; on surveille la classe « on » du panneau.
new MutationObserver(maybeOpen).observe(root.closest('.tab-panel'), { attributes: true, attributeFilter: ['class'] });
maybeOpen();
window.addEventListener('pagehide', () => { if (running) { stop('Session fermée.'); closeRequest(true); } });
