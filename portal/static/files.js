/* Gestionnaire de fichiers de loco : une seule interface pour deux côtés.
   - Poste du parc (« relay ») : requêtes et fragments relayés par HTTPS jusqu'à l'agent, comme les terminaux ; aucune
     adresse ni aucun jeton de poste dans le navigateur, l'agent exécute chaque opération en root ou SYSTEM.
   - Partage du serveur loco (« share ») : routes /shares/<n>/files/… ; le portail fait faire chaque opération au service
     des fichiers des partages, avec les droits de la personne (lecture, ou lecture et écriture).
   Ce qu'un côté ne sait pas faire (archive ZIP, recherche dans les sous-dossiers, copie entre partages…) n'apparaît pas. */
const root = document.getElementById('file-manager');
if (!root) throw new Error('loco: gestionnaire de fichiers absent');   // poste hors ligne : panneau non rendu

// ---------------------------------------------------------------- outils
const enc = new TextEncoder(), dec = new TextDecoder();
const human = window.lrBytes || (n => n + ' o');
const toast = window.lrToast || (() => {});
// Traductions (app.js) : tr("Texte", { valeur }).
const tr = window.t || ((s, v) => Object.keys(v || {}).reduce((o, k) => o.split('%(' + k + ')s').join(v[k]), s));
const LANG = ({ en: 'en-US', es: 'es-ES' })[document.documentElement.lang] || 'fr-FR';
const confirmDialog = window.lrConfirm || ((t, x) => Promise.resolve(window.confirm(x || t)));
const EDIT_MAX = 2 * 1024 * 1024;          // fichier ouvert dans l'éditeur de texte, au plus
const PREVIEW_MAX = 64 * 1024 * 1024;      // aperçu lu par morceaux depuis un poste, au plus
const csrf = root.dataset.csrf;

function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
function fold(s) { return String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); }
function fmtDate(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(LANG, { day: '2-digit', month: 'short', year: 'numeric' }) + ' ' +
    d.toLocaleTimeString(LANG, { hour: '2-digit', minute: '2-digit' });
}
function extOf(name) { const i = name.lastIndexOf('.'); return i > 0 ? name.slice(i + 1).toLowerCase() : ''; }
function count(n, one, many) { return (n > 1 ? many : one).split('%(num)s').join(Number(n).toLocaleString(LANG)); }
const sleep = ms => new Promise(r => setTimeout(r, ms));
const bytes = typeof Uint8Array.fromBase64 === 'function' ? s => Uint8Array.fromBase64(s) : s => {
  const raw = atob(s), out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
};
const base64 = typeof Uint8Array.prototype.toBase64 === 'function' ? d => d.toBase64() : d => {
  let out = ''; for (let i = 0; i < d.length; i += 8192) out += String.fromCharCode(...d.subarray(i, i + 8192)); return btoa(out);
};

// Types de fichiers : aperçu dans la page (images, son, vidéo, PDF), éditeur de texte, icône.
const MIME = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp', avif: 'image/avif',
  bmp: 'image/bmp', ico: 'image/x-icon', mp4: 'video/mp4', m4v: 'video/mp4', webm: 'video/webm', ogv: 'video/ogg',
  mov: 'video/quicktime', mp3: 'audio/mpeg', m4a: 'audio/mp4', aac: 'audio/aac', ogg: 'audio/ogg', oga: 'audio/ogg',
  opus: 'audio/ogg', wav: 'audio/wav', flac: 'audio/flac', pdf: 'application/pdf' };
const TEXT_EXT = new Set(('txt log conf cfg ini json yaml yml xml md csv tsv sh bash zsh ps1 bat cmd py pl rb js mjs ts css html htm ' +
  'service env toml properties list sql c h cpp hpp go rs java php lua srt vtt nfo svg rules reg').split(' '));
const ARCHIVE_EXT = new Set('zip 7z rar tar gz tgz bz2 xz zst iso'.split(' '));
function kindOf(e) {
  if (e.type === 'dir') return 'dir';
  if (e.type !== 'file') return 'other';
  const ext = extOf(e.name), mime = MIME[ext] || '';
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('video/')) return 'video';
  if (mime.startsWith('audio/')) return 'audio';
  if (ext === 'pdf') return 'pdf';
  if (ARCHIVE_EXT.has(ext)) return 'archive';
  if (TEXT_EXT.has(ext) || !ext) return 'text';
  return 'file';
}
const PREVIEW_KINDS = new Set(['image', 'video', 'audio', 'pdf']);
const SVG = d => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>';
const ICONS = {
  dir: SVG('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
  file: SVG('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>'),
  text: SVG('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>'),
  image: SVG('<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m4 18 5-5 4 4 2.5-2.5L20 19"/>'),
  video: SVG('<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="m10 9.5 5 2.5-5 2.5z"/>'),
  audio: SVG('<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>'),
  pdf: SVG('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 15h2.5a1.5 1.5 0 0 0 0-3H8v5"/>'),
  archive: SVG('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M10 5h1M10 8h1M10 11h1M10 14h1v3h-1z"/>'),
  link: SVG('<path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"/>'),
  other: SVG('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>'),
};
function iconOf(e) { return e.link ? ICONS.link : ICONS[kindOf(e)] || ICONS.file; }

async function answer(response) {
  let data = null;
  try { data = await response.json(); } catch { /* corps vide ou non JSON */ }
  if (response.status === 401) throw new Error(tr('Session expirée : rechargez la page.'));
  if (!response.ok) {
    const err = new Error((data && data.error) || tr('Erreur %(status)s.', { status: response.status }));
    err.code = data && data.code; err.data = data; err.status = response.status;
    throw err;
  }
  return data;
}

// ================================================================ côté « poste » : session relayée vers l'agent
function relayBackend() {
  const CHUNK = 192 * 1024;           // fragment de lecture/écriture, sous le paquet de 256 Kio du relais
  let io = null, running = false, inputSeq = 0, outputAck = 0, queue = new Uint8Array(0), pending = null;
  let inFlight = false, failures = 0, quiet = 0, pollTimer = null, nextId = 1, inbuf = new Uint8Array(0);
  // Canal persistant quand le serveur l'offre (relay-link.js) : ce qu'il pousse est gardé ici jusqu'au prochain passage.
  let link = null, pushed = [], info = null;
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
    if (!running) return Promise.reject(new Error(tr('Session fermée.')));
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
      catch { stop(tr('Réponse du poste illisible.')); return; }
      const data = inbuf.slice(8 + hlen, 8 + hlen + dlen);
      inbuf = inbuf.slice(8 + hlen + dlen);
      const r = waiting.get(header.id);
      if (!r) continue;
      waiting.delete(header.id);
      if (header.ok) r.resolve({ header, data });
      else r.reject(new Error(header.error || tr('Opération refusée par le poste.')));
    }
  }
  // Une réponse du serveur, demandée ou poussée. Renvoie false si la session est terminée.
  function absorb(d) {
    if (pending && d.ack >= pending.seq) pending = null;
    for (const packet of d.packets) { if (packet.seq <= outputAck) continue; feed(bytes(packet.data)); outputAck = packet.seq; }
    if (d.state === 'closed' && !d.packets.length) { stop(d.error || tr('Session fermée.')); return false; }
    return true;
  }
  async function poll() {
    if (!running || inFlight) return;
    inFlight = true; let busy = false;
    try {
      // (Un élément vide demande seulement un échange : canal tout juste ouvert, refus poussé.)
      while (pushed.length) {
        const d = pushed.shift();
        if (!d) continue;
        busy = busy || d.packets.length > 0;
        if (!absorb(d)) return;
      }
      if (!pending && queue.length) {
        const data = queue.subarray(0, 64 * 1024); queue = queue.slice(data.length);
        pending = { seq: ++inputSeq, data: base64(data) };
      }
      const body = new URLSearchParams({ csrf, ack: String(outputAck) });
      if (pending) { body.set('seq', String(pending.seq)); body.set('data', pending.data); }
      const { status, data: d } = await (link ? link.exchange(body, 20000) : fetch(io, { method: 'POST', body,
        credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' }, signal: AbortSignal.timeout(20000) })
        .then(async r => ({ status: r.status, data: r.ok ? await r.json() : null })));
      if ([401, 403, 404, 409, 413].includes(status)) { stop(tr('Session de fichiers indisponible ou accès révoqué.'), true); return; }
      if (status !== 200) throw new Error('HTTP ' + status);
      if (!running) return;
      failures = 0; busy = busy || d.packets.length > 0;
      if (!absorb(d)) return;
    } catch (error) {
      if (++failures >= 10) { stop(tr('Connexion au serveur interrompue.'), true); return; }
    } finally { inFlight = false; }
    if (!running) return;
    // Avec le canal persistant, le serveur prévient : au repos, un simple signe de vie de temps en temps.
    if (busy || pending || queue.length || pushed.length) { quiet = 0; schedulePoll(0); }
    else if (link && link.live) schedulePoll(15000);
    else schedulePoll(++quiet < 20 ? 120 : 400);
  }
  function closeRequest(beacon = false) {
    const data = new URLSearchParams({ csrf, close: '1' });
    if (beacon) navigator.sendBeacon(io, data);
    else return fetch(io, { method: 'POST', body: data, credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' }, keepalive: true }).catch(() => {});
  }
  function stop(text, notify = false) {
    if (!running) return;
    running = false; clearTimeout(pollTimer);
    if (link) { link.close(); link = null; }
    pushed = [];
    waiting.forEach(r => r.reject(new Error(text || tr('Session fermée.')))); waiting.clear();
    if (window.locoHold) window.locoHold('files', false); else document.body.removeAttribute('data-no-reload');
    api.onClosed(text || tr('Session fermée.'));
    if (notify) closeRequest();
  }
  const windows = () => info && info.sep === '\\';
  function join(dir, name) {
    if (windows()) return dir.replace(/\\+$/, '') + '\\' + name;
    return (dir === '/' ? '' : dir.replace(/\/+$/, '')) + '/' + name;
  }
  async function exists(path) {
    const { header } = await request('stat', { path });
    return header.exists ? header : null;
  }
  async function readAll(path, limit, progress) {
    const parts = []; let offset = 0;
    for (;;) {
      const { header, data } = await request('read', { path, offset, length: CHUNK });
      if (limit && header.size > limit) throw new Error(tr('Fichier trop volumineux pour l’aperçu (%(size)s) : téléchargez-le.', { size: human(header.size) }));
      parts.push(data); offset += data.length;
      if (progress) progress(offset, header.size);
      if (progress && progress.cancelled) throw new Error(tr('Annulé.'));
      if (header.eof || data.length === 0) break;
    }
    return parts;
  }
  const unix = root.dataset.chmod ? root.dataset.chmod === '1' : Number(root.dataset.windows) !== 1;
  const api = {
    kind: 'relay', lazy: true, concurrency: 1,
    caps: new Set(['write', 'read', ...(unix ? ['chmod'] : []), ...(root.dataset.usage === '1' ? ['usage'] : [])]),
    onClosed() {},
    get sep() { return info && info.sep ? info.sep : '/'; },
    join,
    label: path => path,
    crumbs(path) {
      const win = windows(), parts = [];
      if (!win) parts.push({ label: '/', path: '/' });
      let acc = '';
      path.replace(/[\\/]+$/, '').split(/[\\/]/).filter(Boolean).forEach((piece, i) => {
        acc = win ? (i === 0 ? piece + '\\' : join(acc, piece)) : join(acc || '/', piece);
        parts.push({ label: piece, path: acc });
      });
      return parts;
    },
    async start() {
      const body = new URLSearchParams({ csrf, kind: 'files' });
      const response = await fetch(root.dataset.sessionUrl, { method: 'POST', body, credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } });
      if (response.status === 403) {
        const refusal = await response.json().catch(() => null);
        if (refusal && refusal.code === 'reserved') throw Object.assign(new Error(refusal.error), { code: 'reserved' });
      }
      if (!response.ok) throw new Error(response.status === 409 ? tr('Agent trop ancien ou trop de sessions ouvertes.') : tr('Ouverture refusée (%(status)s).', { status: response.status }));
      const d = await response.json();
      io = d.io; running = true; failures = 0;
      if (window.locoHold) window.locoHold('files', true); else document.body.dataset.noReload = '1';   // pas d'actualisation de la fiche
      if (root.dataset.link) {
        import(root.dataset.link).then(m => {
          if (running && io === d.io) link = m.relayLink(io, data => { pushed.push(data); schedulePoll(); });
        }).catch(() => { /* HTTPS seul */ });
      }
      poll();
      const { header } = await request('hello', {});
      info = header;
      return { home: info.home || (windows() ? (info.roots[0] && info.roots[0].path) : '/'), roots: info.roots || [] };
    },
    close(beacon) { if (running) { stop(tr('Session fermée.')); closeRequest(beacon); } },
    async list(path) {
      const { header } = await request('list', { path });
      return { path: header.path, parent: header.parent, entries: header.entries, truncated: header.truncated };
    },
    async usage(path) {
      const { header } = await request('usage', { path });
      return { path: header.path, parent: header.parent, entries: header.entries, total: header.total, partial: header.partial };
    },
    async mkdir(path) { await request('mkdir', { path }); },
    async mkdirs(base, rel) {
      let at = base;
      for (const piece of rel.split('/').filter(Boolean)) {
        at = join(at, piece);
        if (!await exists(at)) await request('mkdir', { path: at });
      }
      return at;
    },
    async newfile(path) {
      if (await exists(path)) throw new Error(tr('Un élément porte déjà ce nom.'));
      await request('write', { path, offset: 0, final: true }, new Uint8Array(0));
    },
    async rename(path, name) { await request('rename', { path, name }); },
    async chmod(path, mode) { await request('chmod', { path, mode }); },
    // Plusieurs éléments, un par un ; « Remplacer » retire d'abord ce qui porte le nom à destination.
    async transfer(op, paths, to, decisions, job) {
      job.total = paths.length;
      for (const path of paths) {
        if (job.cancelled) break;
        const name = path.split(/[\\/]/).filter(Boolean).pop();
        job.current = name;
        try {
          const decision = decisions[name];
          if (decision === 'skip') { job.count++; continue; }
          if (op !== 'delete' && decision === 'replace') await request('delete', { path: join(to, name) });
          await request(op, op === 'delete' ? { path } : { path, to });
        } catch (e) { job.errors.push({ name, error: e.message }); }
        job.count++; job.done = job.count; job.update();
      }
    },
    async conflicts(paths, to) {
      const { entries } = await this.list(to);
      const taken = new Set(entries.map(e => e.name));
      return paths.map(p => p.split(/[\\/]/).filter(Boolean).pop()).filter(n => taken.has(n));
    },
    async upload(task, progress) {
      const path = join(task.dir, task.name), file = task.file;
      let offset = 0;
      try {
        while (offset < file.size || offset === 0) {
          if (task.cancelled) throw new Error(tr('Annulé.'));
          const buf = new Uint8Array(await file.slice(offset, offset + CHUNK).arrayBuffer());
          const final = offset + buf.length >= file.size;
          await request('write', { path, offset, final }, buf);
          offset += buf.length;
          progress(offset, file.size);
          if (final) break;
        }
      } catch (e) {
        request('write_cancel', { path }).catch(() => {});
        throw e;
      }
      return { name: task.name };
    },
    async blob(entry, path, limit, progress) {
      const parts = await readAll(path, limit, progress);
      return new Blob(parts, { type: MIME[extOf(entry.name)] || 'application/octet-stream' });
    },
    async text(path) { return dec.decode(await new Blob(await readAll(path, EDIT_MAX)).arrayBuffer()); },
    async save(path, text) {
      const data = enc.encode(text);
      let offset = 0;
      do {
        const slice = data.subarray(offset, offset + CHUNK);
        const final = offset + slice.length >= data.length;
        await request('write', { path, offset, final }, slice);
        offset += slice.length;
      } while (offset < data.length);
    },
    async download(entry, path, t) {
      const parts = await readAll(path, 0, (done, total) => { t.set(done, total); progress(t); });
      saveBlob(new Blob(parts), entry.name);
      function progress(x) { if (x.cancelled) throw new Error(tr('Annulé.')); }
    },
  };
  return api;
}

// ================================================================ côté « partage » : service des fichiers du serveur
function shareBackend() {
  const BASE = root.dataset.base, CHUNK = 8 * 1024 * 1024;
  const shareId = Number(root.dataset.shareId), shareName = root.dataset.shareName;
  // Un téléchargement passe par un cadre caché qui lui est propre ; une réponse qui s'y affiche est un refus, montré ici.
  function hiddenFrame() {
    const frame = document.createElement('iframe');
    frame.className = 'files-dl'; frame.hidden = true; frame.title = 'download';
    frame.name = 'files-dl-' + Math.random().toString(36).slice(2);
    frame.addEventListener('load', () => {
      let href = '', text = '';
      try { href = frame.contentWindow.location.href; text = frame.contentDocument.body ? frame.contentDocument.body.textContent : ''; } catch { href = '?'; }
      if (href === 'about:blank') return;
      let message = tr('Téléchargement impossible.');
      try { message = JSON.parse(text).error || message; } catch { /* page d'erreur */ }
      toast(message, 'error');
    });
    setTimeout(() => frame.remove(), 120000);
    return frame;
  }
  function goFrame(url) { const frame = hiddenFrame(); frame.src = url; root.appendChild(frame); }
  const get = (path, params) => fetch(BASE + path + '?' + new URLSearchParams(params), { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } }).then(answer);
  const post = (path, fields) => fetch(BASE + path, { method: 'POST', credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' },
    body: new URLSearchParams(Object.assign({ csrf }, fields)) }).then(answer);
  const op = (name, args) => post('/op', { op: name, args: JSON.stringify(args) });
  const join = (dir, name) => (dir ? dir + '/' + name : name);
  async function follow(job, id) {
    for (let wait = 150; ; wait = Math.min(1000, wait * 1.5)) {
      await sleep(wait);
      const s = await get('/job/' + id, {});
      Object.assign(job, { total: s.total, done: s.done, count: s.count, current: s.current || job.current });
      job.update();
      if (s.state !== 'running') {
        job.errors.push(...s.errors.map(e => ({ name: e.name, error: e.error })));
        if (s.state === 'cancelled') job.cancelledByServer = true;
        return s;
      }
      if (job.cancelled && !job.cancelSent) { job.cancelSent = true; post('/job/' + id + '/cancel', {}).catch(() => {}); }
    }
  }
  const api = {
    kind: 'share', lazy: false, concurrency: 3, shareId, shareName,
    caps: new Set(['read', 'zip', 'search', 'usage', 'size', 'keepboth', 'direct', 'mkdirp', 'cross', 'jobs',
      ...(root.dataset.mode === 'write' ? ['write'] : [])]),
    full: root.dataset.full === '1',
    sep: '/',
    join,
    label: path => (path ? shareName + '/' + path : shareName),
    crumbs(path) {
      const parts = [{ label: shareName, path: '' }];
      let acc = '';
      path.split('/').filter(Boolean).forEach(piece => { acc = join(acc, piece); parts.push({ label: piece, path: acc }); });
      return parts;
    },
    async start() { return { home: root.dataset.start || '', roots: [] }; },
    close() {},
    async list(path) {
      const d = await get('/list', { path });
      this.full = d.full;
      if (d.mode === 'write') this.caps.add('write'); else this.caps.delete('write');
      return { path: d.path, parent: d.path ? d.path.split('/').slice(0, -1).join('/') : null, entries: d.entries,
        truncated: d.truncated, meta: d };
    },
    async usage(path) { const d = await get('/usage', { path }); return Object.assign(d, { parent: d.path ? d.path.split('/').slice(0, -1).join('/') : null }); },
    search: (path, q) => get('/search', { path, q }),
    size: path => get('/size', { path }),
    async mkdir(path) { await op('mkdir', { path }); },
    async mkdirs(base, rel) { const path = join(base, rel); await op('mkdir', { path, parents: true }); return path; },
    async newfile(path) { await op('newfile', { path }); },
    async rename(path, name) { return op('rename', { path, name }); },
    async transfer(kind, paths, to, decisions, job, from) {
      const args = kind === 'delete' ? { paths } : { paths, to, decisions, from: from || shareId };
      const { job: id } = await op(kind, args);
      job.server = id;
      return follow(job, id);
    },
    async conflicts(paths, to, from) { return (await op('conflicts', { paths, to, from: from || shareId })).conflicts; },
    async upload(task, progress) {
      const file = task.file;
      const { token } = await post('/upload', { dir: task.dir, name: task.name, size: String(file.size) });
      let offset = 0;
      try {
        while (offset < file.size) {
          if (task.cancelled) throw new Error(tr('Annulé.'));
          const end = Math.min(file.size, offset + CHUNK);
          let d = null;
          for (let attempt = 0; ; attempt++) {
            try {
              const r = await fetch(BASE + '/upload/' + token + '?offset=' + offset, { method: 'POST', body: file.slice(offset, end),
                credentials: 'same-origin', headers: { 'Content-Type': 'application/octet-stream', 'X-CSRF-Token': csrf, 'X-Requested-With': 'fetch' } });
              if (r.status === 409) {
                const x = await r.json().catch(() => ({}));
                if (x.code === 'offset') { d = { written: x.written }; break; }   // reprise là où le serveur en est
              }
              d = await answer(r);
              break;
            } catch (e) {
              if (e.status || attempt >= 3 || task.cancelled) throw e;          // refus du serveur : pas de nouvel essai
              await sleep(1000 * 2 ** attempt);
            }
          }
          offset = d.written;
          progress(offset, file.size);
        }
        for (;;) {
          try {
            return await post('/upload/' + token + '/done', { conflict: task.conflict || '', mtime: String(file.lastModified || ''),
              expect: task.expect != null ? String(task.expect) : '' });
          } catch (e) {
            if (e.code !== 'exists' || !task.ask) throw e;
            const decision = await task.ask(task.name);           // apparu pendant l'envoi : on demande, puis on conclut
            if (decision === 'skip') throw Object.assign(new Error(tr('Ignoré.')), { skipped: true });
            task.conflict = decision;
          }
        }
      } catch (e) {
        post('/upload/' + token + '/cancel', {}).catch(() => {});
        throw e;
      }
    },
    url: (path, download) => BASE + '/raw?' + new URLSearchParams(download ? { path, dl: '1' } : { path }),
    async blob(entry, path) { const r = await fetch(this.url(path), { credentials: 'same-origin' }); if (!r.ok) await answer(r); return r.blob(); },
    async text(path) {
      const r = await fetch(this.url(path, true), { credentials: 'same-origin' });
      if (!r.ok) await answer(r);
      const tag = (r.headers.get('ETag') || '').match(/-([0-9a-f]+)"$/);
      return { text: dec.decode(await r.arrayBuffer()), mtime: tag ? parseInt(tag[1], 16) : null };
    },
    async save(path, text, expect) {
      const slash = path.lastIndexOf('/');
      const task = { dir: slash < 0 ? '' : path.slice(0, slash), name: path.slice(slash + 1), conflict: 'replace', expect,
        file: new File([text], path.slice(slash + 1), { type: 'text/plain' }) };
      const r = await this.upload(task, () => {});
      return r && r.mtime;
    },
    download(entry, path) { goFrame(this.url(path, true) + '&_=' + Date.now()); },
    zip(dir, names) {
      const frame = hiddenFrame(), form = document.createElement('form');
      root.appendChild(frame);
      form.method = 'post'; form.action = BASE + '/zip'; form.target = frame.name; form.hidden = true;
      const add = (k, v) => { const i = document.createElement('input'); i.type = 'hidden'; i.name = k; i.value = v; form.appendChild(i); };
      add('csrf', csrf); add('path', dir); names.forEach(n => add('names', n));
      root.appendChild(form); form.submit(); form.remove();
    },
    // Presse-papiers gardé d'une page de partage à l'autre (copier dans un partage, coller dans un autre).
    loadClip() { try { const c = JSON.parse(sessionStorage.getItem('loco-files-clip') || 'null'); return c && c.paths ? c : null; } catch { return null; } },
    saveClip(c) { try { if (c) sessionStorage.setItem('loco-files-clip', JSON.stringify(c)); else sessionStorage.removeItem('loco-files-clip'); } catch { /* indisponible */ } },
    syncUrl(path) {
      const url = new URL(location.href);
      if (path) url.searchParams.set('path', path); else url.searchParams.delete('path');
      history.replaceState(history.state, '', url);
    },
  };
  return api;
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

const backend = root.dataset.backend === 'share' ? shareBackend() : relayBackend();
const can = cap => backend.caps.has(cap);
const writable = () => can('write');
const growable = () => writable() && !backend.full;          // partage plein : il ne peut plus que diminuer

// ================================================================ interface
const pane = root.querySelector('.files-pane');
const table = root.querySelector('.files-table');
const thead = table.querySelector('thead tr');
const tbody = table.querySelector('tbody');
const crumbsEl = root.querySelector('.files-crumbs');
const rootsEl = root.querySelector('.files-roots');
const statusEl = root.querySelector('.files-status');
const emptyEl = root.querySelector('.files-empty');
const truncEl = root.querySelector('.files-truncated');
const transfersEl = root.querySelector('.files-transfers');
const searchEl = root.querySelector('.files-search input');
const pasteBtn = root.querySelector('[data-files="paste"]');
const hiddenToggle = root.querySelector('[data-files="hidden"]');
const usageToggle = root.querySelector('[data-files="usage"]');
const metaEl = root.querySelector('.files-meta');
const selbar = root.querySelector('.files-selbar');
// Barre du bas : hors des panneaux qui défilent, avant les notifications (qu'elle remonte quand elle est là).
document.body.insertBefore(selbar, document.querySelector('.toasts'));

let current = null, parentPath = null, listing = [], truncated = false, mode = 'list', usageData = null, found = null;
let showHidden = localStorage.getItem('loco-files-hidden') === '1';
let sort = (() => { try { return JSON.parse(localStorage.getItem('loco-files-sort')) || { key: 'name', dir: 1 }; } catch { return { key: 'name', dir: 1 }; } })();
let filter = '', selected = new Set(), anchor = null, focusKey = null, navSeq = 0, lastMeta = null;
let back = [], forward = [], clip = backend.loadClip ? backend.loadClip() : null;
// Lignes affichées : chacune a sa clé (son nom ; dossier et nom dans les résultats d'une recherche, où deux
// fichiers peuvent porter le même nom).
let shown = [], byKey = new Map();
const keyOf = e => (mode === 'search' ? (e.dir || '') + '/' + e.name : e.name);
const entryOf = key => byKey.get(key);

function setStatus(text, kind) { statusEl.textContent = text || ''; statusEl.dataset.kind = kind || ''; }
function pathOf(name) { return backend.join(current, name); }
function pathOfEntry(e) { return mode === 'search' ? backend.join(e.dir || '', e.name) : pathOf(e.name); }
function visible() {
  if (mode === 'search') return found.entries;
  if (mode === 'usage') return usageData.entries;
  let list = listing.filter(e => showHidden || !e.name.startsWith('.'));
  if (filter) list = list.filter(e => fold(e.name).includes(filter));
  const k = sort.key, d = sort.dir;
  const by = k === 'size' ? (a, b) => (a.size - b.size) || a.name.localeCompare(b.name, LANG)
    : k === 'mtime' ? (a, b) => (a.mtime - b.mtime) || a.name.localeCompare(b.name, LANG)
    : (a, b) => a.name.localeCompare(b.name, LANG, { numeric: true, sensitivity: 'base' });
  return list.slice().sort((a, b) => ((a.type === 'dir') === (b.type === 'dir') ? d * by(a, b) : (a.type === 'dir' ? -1 : 1)));
}

// ---------------------------------------------------------------- rendu
function renderCrumbs() {
  const parts = backend.crumbs(current);
  crumbsEl.innerHTML = parts.map((p, i) =>
    '<button type="button" class="files-crumb" data-path="' + esc(p.path) + '"' + (i === parts.length - 1 ? ' aria-current="location"' : '') + '>' + esc(p.label) + '</button>' +
    // Pas de séparateur après la racine « / » : le chemin se lit « /root/Documents », pas « / / root ».
    (i < parts.length - 1 && p.path !== '/' ? '<span class="files-crumb-sep" aria-hidden="true">' + (backend.sep === '\\' ? '\\' : '/') + '</span>' : '')
  ).join('');
  root.querySelector('[data-files="up"]').disabled = parentPath == null;
  root.querySelector('[data-files="back"]').disabled = !back.length;
  root.querySelector('[data-files="forward"]').disabled = !forward.length;
}
function renderRoots(list) {
  if (!rootsEl) return;
  rootsEl.innerHTML = (list || []).map(r => '<button type="button" class="chip files-root" data-path="' + esc(r.path) + '">' + esc(r.name) + '</button>').join('');
  rootsEl.hidden = !(list || []).length;
}
function sortHead(key, label, cls) {
  const on = sort.key === key;
  return '<th class="' + (cls || '') + '" aria-sort="' + (on ? (sort.dir > 0 ? 'ascending' : 'descending') : 'none') + '">' +
    '<button type="button" class="files-sort' + (on ? ' on' : '') + '" data-sort="' + key + '">' + esc(label) +
    '<span class="files-sort-arrow" aria-hidden="true">' + (on ? (sort.dir > 0 ? '↑' : '↓') : '') + '</span></button></th>';
}
function renderHead() {
  const check = '<th class="files-check"><input type="checkbox" data-items-all aria-label="' + esc(tr('Tout sélectionner')) + '"></th>';
  const act = '<th class="files-act"><span class="visually-hidden">' + esc(tr('Actions')) + '</span></th>';
  if (mode === 'usage') {
    thead.innerHTML = '<th>' + esc(tr('Nom')) + '</th><th class="files-usage-colbar" aria-hidden="true"></th><th class="num">' + esc(tr('Taille')) + '</th>';
  } else if (mode === 'search') {
    thead.innerHTML = check + '<th>' + esc(tr('Nom')) + '</th><th class="hide-sm files-where">' + esc(tr('Dossier')) + '</th><th class="num files-size">' + esc(tr('Taille')) + '</th><th class="hide-sm nowrap files-date">' + esc(tr('Modifié')) + '</th>' + act;
  } else {
    thead.innerHTML = check + sortHead('name', tr('Nom')) + sortHead('size', tr('Taille'), 'num files-size') + sortHead('mtime', tr('Modifié'), 'hide-sm nowrap files-date') +
      (can('chmod') ? '<th class="num hide-sm files-mode">' + esc(tr('Droits')) + '</th>' : '') + act;
  }
  table.classList.toggle('usage', mode === 'usage');
}
const KEBAB = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="12" cy="5" r="1.9"/><circle cx="12" cy="12" r="1.9"/><circle cx="12" cy="19" r="1.9"/></svg>';
function rowHtml(e) {
  // Tout ce qui vient du poste ou du partage est échappé ici : un nom de fichier ne doit rien pouvoir injecter.
  const type = ['dir', 'file', 'other'].includes(e.type) ? e.type : 'other';
  const name = String(e.name), key = keyOf(e), on = selected.has(key);
  const nameCell = type === 'dir' || type === 'file'
    ? '<button type="button" class="files-name link" data-item-open>' + iconOf(e) + '<span>' + esc(name) + '</span></button>'
    : '<span class="files-name" title="' + esc(e.link ? tr('Lien symbolique : il n’est pas suivi.') : '') + '">' + iconOf(e) + '<span>' + esc(name) + '</span></span>';
  const where = mode === 'search' ? '<td class="hide-sm muted small files-where"><button type="button" class="linkbtn" data-reveal>' + esc(backend.label(e.dir || '')) + '</button></td>' : '';
  const chmod = mode === 'list' && can('chmod') ? '<td class="num mono small muted hide-sm">' + (Number.isInteger(e.mode) && e.mode > 0 ? e.mode.toString(8).padStart(3, '0') : '—') + '</td>' : '';
  return '<tr data-key="' + esc(key) + '" data-name="' + esc(name) + '" data-type="' + type + '" tabindex="-1"' +
    (on ? ' class="selected" aria-selected="true"' : ' aria-selected="false"') + (writable() && mode === 'list' ? ' draggable="true"' : '') + '>' +
    '<td class="files-check"><input type="checkbox" data-item-pick aria-label="' + esc(tr('Sélectionner « %(name)s »', { name })) + '"' + (on ? ' checked' : '') + '></td>' +
    '<td class="files-name-cell">' + nameCell + '</td>' + where +
    '<td class="num muted files-size">' + (type === 'file' ? human(e.size) : '—') + '</td>' +
    '<td class="muted nowrap small hide-sm">' + esc(fmtDate(Number(e.mtime))) + '</td>' + chmod +
    '<td class="right files-act"><button type="button" class="kebab files-more" data-more aria-haspopup="menu" aria-label="' + esc(tr('Actions pour « %(name)s »', { name })) + '" title="' + esc(tr('Actions')) + '">' + KEBAB + '</button></td></tr>';
}
function render() {
  renderHead();
  shown = visible();
  byKey = new Map(shown.map(e => [keyOf(e), e]));
  if (mode === 'usage') return renderUsage();
  tbody.innerHTML = shown.map(rowHtml).join('');
  const masked = mode === 'list' ? listing.length - listing.filter(e => showHidden || !e.name.startsWith('.')).length : 0;
  const q = searchEl ? searchEl.value.trim() : '';
  emptyEl.classList.toggle('hidden', shown.length > 0);
  if (mode === 'search') {
    emptyEl.textContent = tr('Aucun élément ne correspond à « %(q)s » dans ce dossier et ses sous-dossiers.', { q });
  } else if (filter) {
    emptyEl.innerHTML = esc(tr('Aucun élément de ce dossier ne correspond à « %(q)s ».', { q })) +
      (can('search') ? ' <button type="button" class="linkbtn" data-deep>' + esc(tr('Chercher aussi dans les sous-dossiers')) + '</button>' : '');
  } else if (masked && !shown.length) {
    emptyEl.innerHTML = esc(count(masked, tr('Aucun élément visible : %(num)s élément caché.'), tr('Aucun élément visible : %(num)s éléments cachés.'))) +
      ' <button type="button" class="linkbtn" data-show-hidden>' + esc(tr('Les afficher')) + '</button>';
  } else {
    emptyEl.innerHTML = esc(tr('Dossier vide.')) + (growable() ? ' <span class="muted">' + esc(tr('Déposez des fichiers ici ou utilisez « Téléverser ».')) + '</span>' : '');
  }
  if (mode === 'search' && found.partial) {
    truncEl.textContent = tr('Recherche arrêtée avant la fin (trop de résultats ou dossier trop grand) : précisez le texte cherché.');
    truncEl.classList.remove('hidden');
  } else {
    truncEl.textContent = tr('Dossier volumineux : seuls les premiers éléments sont affichés.');
    truncEl.classList.toggle('hidden', !(mode === 'list' && truncated));
  }
  if (can('search') && filter && mode === 'list' && shown.length) {
    tbody.insertAdjacentHTML('beforeend', '<tr class="files-deep"><td></td><td colspan="9"><button type="button" class="linkbtn" data-deep>' +
      esc(tr('Chercher « %(q)s » dans les sous-dossiers', { q })) + '</button></td></tr>');
  }
  renderSelection();
  renderSummary();
  focusRow(focusKey, false);
}
// Espace disque (type TreeSize) : taille de chaque élément, du plus gros au plus petit, avec une barre.
function renderUsage() {
  const top = shown.reduce((m, e) => Math.max(m, e.size), 0) || 1;
  emptyEl.classList.toggle('hidden', shown.length > 0);
  emptyEl.textContent = tr('Dossier vide.');
  truncEl.classList.add('hidden');
  tbody.innerHTML = shown.map(e => {
    const type = ['dir', 'file', 'other'].includes(e.type) ? e.type : 'other';
    const name = type === 'dir'
      ? '<button type="button" class="files-name link" data-item-open>' + ICONS.dir + '<span>' + esc(e.name) + '</span></button>'
      : '<span class="files-name">' + iconOf(e) + '<span>' + esc(e.name) + '</span></span>';
    return '<tr data-key="' + esc(e.name) + '" data-name="' + esc(e.name) + '" data-type="' + type + '"><td>' + name + '</td>' +
      '<td class="files-usage-bar"><div class="meter"><i data-width="' + Math.round(100 * e.size / top) + '"></i></div></td>' +
      '<td class="num mono">' + human(e.size) + '</td></tr>';
  }).join('');
  // Largeurs posées par le script : la politique de contenu du portail refuse les attributs « style ».
  tbody.querySelectorAll('[data-width]').forEach(i => { i.style.width = i.dataset.width + '%'; });
  setStatus(tr('%(total)s au total%(partial)s', { total: human(usageData.total || 0), partial: usageData.partial ? tr(' · relevé partiel (trop volumineux)') : '' }));
  renderSelection();
}
function renderSummary() {
  if (!shown.length && mode === 'list') { setStatus(''); return; }
  const dirs = shown.filter(e => e.type === 'dir').length, files = shown.filter(e => e.type === 'file');
  const size = files.reduce((s, e) => s + (e.size || 0), 0);
  const parts = [count(shown.length, tr('%(num)s élément'), tr('%(num)s éléments'))];
  if (dirs) parts.push(count(dirs, tr('%(num)s dossier'), tr('%(num)s dossiers')));
  if (files.length) parts.push(count(files.length, tr('%(num)s fichier'), tr('%(num)s fichiers')) + ' · ' + human(size));
  if (mode === 'search') parts.unshift(tr('Résultats de la recherche'));
  setStatus(parts.join(' · '));
}
function renderMeta(meta) {
  if (meta) lastMeta = meta;
  if (!metaEl || !meta) return;
  const limit = meta.limit || 0, used = meta.used;
  const pct = used != null && limit ? Math.min(100, Math.round(100 * used / limit)) : null;
  metaEl.querySelector('.files-meta-used').textContent = used != null ? tr('%(used)s sur %(limit)s', { used: human(used), limit: human(limit) }) : tr('place pas encore relevée');
  const bar = metaEl.querySelector('.meter');
  bar.className = 'meter level ' + (meta.level || '');
  bar.querySelector('i').style.width = (pct || 0) + '%';
  const pill = metaEl.querySelector('.files-meta-mode');
  pill.className = 'pill ' + (meta.full ? 'err' : meta.mode === 'write' ? '' : 'warn');
  pill.textContent = meta.full ? tr('plein : lecture seule') : meta.mode === 'write' ? tr('lecture et écriture') : tr('lecture seule');
}
function renderSelection() {
  root.querySelectorAll('[data-write]').forEach(el => { el.hidden = !writable(); });
  root.querySelectorAll('[data-grow]').forEach(el => { el.hidden = !growable() || mode !== 'list'; });
  root.classList.toggle('files-ro', !writable());
  const all = rows().length;
  const box = thead.querySelector('[data-items-all]');
  if (box) { box.checked = all > 0 && selected.size >= all; box.indeterminate = selected.size > 0 && selected.size < all; }
  renderPaste();
  const n = selected.size;
  selbar.classList.toggle('on', n > 0 && mode !== 'usage');
  if (!n) return;
  const items = [...selected].map(entryOf).filter(Boolean);
  const size = items.filter(e => e.type === 'file').reduce((s, e) => s + (e.size || 0), 0);
  selbar.querySelector('.count').textContent = count(n, tr('%(num)s sélectionné'), tr('%(num)s sélectionnés')) + (size ? ' · ' + human(size) : '');
  const hasDir = items.some(e => e.type === 'dir');
  selbar.querySelector('[data-sel="download"]').hidden = hasDir && !(can('zip') && mode === 'list');
  selbar.querySelector('[data-sel="copy"]').hidden = mode === 'search' && !can('cross');
  selbar.querySelector('[data-sel="cut"]').hidden = !writable() || mode !== 'list';
  selbar.querySelector('[data-sel="delete"]').hidden = !writable();
}
function renderPaste() {
  if (!pasteBtn) return;
  const ready = !!clip && growable() && mode === 'list' && (clip.share == null || clip.share === backend.shareId || can('cross'));
  pasteBtn.hidden = !ready;
  if (!ready) return;
  const n = clip.paths.length;
  pasteBtn.textContent = clip.mode === 'copy'
    ? (n > 1 ? tr('Coller %(num)s éléments', { num: n }) : tr('Coller « %(name)s »', { name: clip.names[0] }))
    : (n > 1 ? tr('Déplacer %(num)s éléments ici', { num: n }) : tr('Déplacer « %(name)s » ici', { name: clip.names[0] }));
}

// ---------------------------------------------------------------- navigation
async function navigate(path, opts = {}) {
  const seq = ++navSeq;
  setStatus(mode === 'usage' ? tr('Analyse de l’espace disque…') : tr('Chargement…'));
  try {
    const d = mode === 'usage' ? await backend.usage(path) : await backend.list(path);
    if (seq !== navSeq) return;                                  // une navigation plus récente l'emporte
    if (opts.history !== false && current != null && d.path !== current) { back.push(current); forward = []; }
    const changed = d.path !== current;
    current = d.path; parentPath = d.parent;
    if (mode === 'usage') usageData = d; else { listing = d.entries; truncated = d.truncated; }
    if (changed && !opts.keepSearch) clearSearch(false);
    if (mode === 'search') mode = 'list';
    if (changed) { selected = new Set(); anchor = null; focusKey = null; }
    if (opts.select) { selected = new Set([opts.select]); anchor = focusKey = opts.select; }
    renderMeta(d.meta);
    renderCrumbs();
    render();
    if (opts.select) focusRow(opts.select, true);
    if (backend.syncUrl) backend.syncUrl(current);
  } catch (e) { if (seq === navSeq) setStatus(e.message, 'err'); }
}
function refresh(opts = {}) { if (current != null) return navigate(current, Object.assign({ history: false, keepSearch: true }, opts)); }
function goUp() { if (parentPath != null) navigate(parentPath, { select: current.split(/[\\/]/).filter(Boolean).pop() }); }
function goBack() { if (back.length) { forward.push(current); navigate(back.pop(), { history: false }); } }
function goForward() { if (forward.length) { back.push(current); navigate(forward.pop(), { history: false }); } }

// ---------------------------------------------------------------- sélection et focus
function rows() { return [...tbody.querySelectorAll('tr[data-key]')]; }
function focusRow(key, scroll) {
  const all = rows(), row = all.find(r => r.dataset.key === key) || null;
  all.forEach(r => { r.tabIndex = -1; });
  const target = row || all[0];
  if (target) target.tabIndex = 0;
  if (row && scroll) { row.focus({ preventScroll: true }); row.scrollIntoView({ block: 'nearest' }); }
  focusKey = row ? key : null;
}
function setSelected(keys) {
  selected = new Set(keys);
  rows().forEach(r => {
    const on = selected.has(r.dataset.key);
    r.classList.toggle('selected', on); r.setAttribute('aria-selected', String(on));
    const box = r.querySelector('[data-item-pick]'); if (box) box.checked = on;
  });
  renderSelection();
}
function pick(key, ev) {
  const keys = rows().map(r => r.dataset.key);
  if (ev && ev.shiftKey && anchor != null && keys.includes(anchor)) {
    const a = keys.indexOf(anchor), b = keys.indexOf(key);
    const range = keys.slice(Math.min(a, b), Math.max(a, b) + 1);
    setSelected(ev.ctrlKey || ev.metaKey ? [...selected, ...range] : range);
  } else if (ev && (ev.ctrlKey || ev.metaKey || ev.type === 'toggle')) {
    const next = new Set(selected);
    if (next.has(key)) next.delete(key); else next.add(key);
    setSelected(next); anchor = key;
  } else {
    setSelected([key]); anchor = key;
  }
  focusRow(key, false);
}
function targets(key) {
  // Action sur une ligne : toute la sélection si la ligne en fait partie, sinon la ligne seule.
  if (key != null && !selected.has(key)) setSelected([key]);
  return [...selected].map(entryOf).filter(Boolean);
}
function pathsOf(items) { return items.map(pathOfEntry); }

// ---------------------------------------------------------------- recherche (dossier courant, puis sous-dossiers)
let filterTimer = 0;
function clearSearch(rerender = true) {
  if (!searchEl) return;
  searchEl.value = ''; filter = ''; found = null;
  if (mode === 'search') mode = 'list';
  if (rerender) render();
}
async function deepSearch() {
  const q = searchEl.value.trim();
  if (!q || !can('search')) return;
  setStatus(tr('Recherche de « %(q)s »…', { q }));
  try {
    const d = await backend.search(current, q);
    found = d; mode = 'search'; selected = new Set(); anchor = focusKey = null; render();
  } catch (e) { setStatus(e.message, 'err'); }
}

// ---------------------------------------------------------------- transferts et tâches (barre de progression)
const queued = [];
let active = 0;
function transfer(label, onCancel) {
  const el = document.createElement('div');
  el.className = 'files-xfer';
  el.innerHTML = '<div class="files-xfer-row"><span class="files-xfer-name small"></span>' +
    '<button type="button" class="files-xfer-cancel" title="' + esc(tr('Annuler')) + '" aria-label="' + esc(tr('Annuler')) + '">' +
    '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>' +
    '<div class="meter"><i></i></div><div class="files-xfer-pct small muted"></div>';
  el.querySelector('.files-xfer-name').textContent = label;
  transfersEl.appendChild(el);
  const start = Date.now();
  const t = {
    cancelled: false,
    set(done, total, text) {
      const pct = total ? Math.min(100, Math.round(100 * done / total)) : 0;
      el.querySelector('i').style.width = (total ? pct : 100) + '%';
      el.classList.toggle('indeterminate', !total);
      const elapsed = (Date.now() - start) / 1000;
      const rate = !text && elapsed > 0.4 && done > 0 ? ' · ' + human(done / elapsed) + '/s' : '';
      el.querySelector('.files-xfer-pct').textContent = text || ((total ? human(done) + ' / ' + human(total) + ' · ' + pct + ' %' : human(done)) + rate);
    },
    done(text, kind) {
      const cancel = el.querySelector('.files-xfer-cancel');
      if (cancel) cancel.remove();
      el.classList.remove('indeterminate');
      if (kind !== 'err') el.querySelector('i').style.width = '100%';
      el.querySelector('.files-xfer-pct').textContent = text;
      if (kind) el.classList.add(kind);
      setTimeout(() => el.remove(), kind === 'err' ? 8000 : 2500);
    },
  };
  el.querySelector('.files-xfer-cancel').addEventListener('click', () => { t.cancelled = true; if (onCancel) onCancel(); });
  return t;
}
function renderQueue() {
  let note = transfersEl.querySelector('.files-xfer-queue');
  if (!queued.length) { if (note) note.remove(); return; }
  if (!note) {
    note = document.createElement('div'); note.className = 'files-xfer-queue small muted';
    note.innerHTML = '<span></span> <button type="button" class="linkbtn">' + esc(tr('Tout annuler')) + '</button>';
    note.querySelector('button').addEventListener('click', () => { queued.splice(0).forEach(q => q.reject(Object.assign(new Error(tr('Annulé.')), { skipped: true }))); renderQueue(); });
    transfersEl.prepend(note);
  }
  note.querySelector('span').textContent = count(queued.length, tr('%(num)s fichier en attente'), tr('%(num)s fichiers en attente'));
}
// File d'attente des téléversements : quelques fichiers à la fois (un seul vers un poste).
function slot(fn) {
  return new Promise((resolve, reject) => {
    const run = () => { active++; fn().then(resolve, reject).finally(() => { active--; const next = queued.shift(); renderQueue(); if (next) next.run(); }); };
    if (active < backend.concurrency) run(); else { queued.push({ run, reject }); renderQueue(); }
  });
}
// Copie, déplacement, suppression : une tâche, suivie dans la même zone.
async function runJob(kind, paths, to, decisions, from) {
  const labels = { copy: tr('Copie'), move: tr('Déplacement'), delete: tr('Suppression') };
  const job = { total: 0, done: 0, count: 0, current: '', errors: [], cancelled: false, update() {} };
  const t = transfer(labels[kind] + ' · ' + count(paths.length, tr('%(num)s élément'), tr('%(num)s éléments')), () => { job.cancelled = true; });
  job.update = () => {
    if (backend.kind === 'relay') t.set(job.count, job.total, (job.current ? job.current + ' · ' : '') + job.count + ' / ' + job.total);
    else if (kind === 'delete' || !job.total) t.set(job.count, 0, (job.current ? job.current + ' · ' : '') + count(job.count, tr('%(num)s élément traité'), tr('%(num)s éléments traités')));
    else t.set(job.done, job.total);
  };
  try {
    await backend.transfer(kind, paths, to, decisions, job, from);
    if (job.cancelled || job.cancelledByServer) t.done(tr('annulé'));
    else if (job.errors.length) {
      t.done(count(job.errors.length, tr('%(num)s élément non traité'), tr('%(num)s éléments non traités')), 'err');
      toast(job.errors.slice(0, 3).map(e => (e.name ? '« ' + e.name + ' » : ' : '') + e.error).join(' · '), 'error');
    } else t.done(tr('terminé'));
  } catch (e) { t.done(e.message, 'err'); }
  refresh();
}

// ---------------------------------------------------------------- fenêtres (nom, conflit, propriétés, aperçu, éditeur)
function makeDialog(cls, html) {
  const dlg = document.createElement('dialog');
  dlg.className = cls; dlg.innerHTML = html;
  root.appendChild(dlg);
  dlg.addEventListener('close', () => setTimeout(() => dlg.remove(), 0));
  return dlg;
}
function prompt(title, label, value, selectStem) {
  return new Promise(resolve => {
    const dlg = makeDialog('files-prompt', '<form method="dialog"><div class="dlg-h"><h2></h2></div><div class="dlg-b"><label class="field"><span></span><input type="text" spellcheck="false" autocomplete="off"></label></div>' +
      '<div class="dlg-f"><button type="button" class="btn" value="cancel">' + esc(tr('Annuler')) + '</button><button type="button" class="btn primary" value="ok">' + esc(tr('Valider')) + '</button></div></form>');
    dlg.querySelector('h2').textContent = title;
    dlg.querySelector('.field span').textContent = label;
    const input = dlg.querySelector('input');
    input.value = value || '';
    let answered = false;
    const done = v => { if (answered) return; answered = true; if (dlg.open) dlg.close(); resolve(v); };
    dlg.querySelector('[value="ok"]').onclick = () => done(input.value.trim());
    dlg.querySelector('[value="cancel"]').onclick = () => done(null);
    dlg.addEventListener('close', () => done(null));
    dlg.showModal(); input.focus();
    const dot = selectStem ? input.value.lastIndexOf('.') : -1;
    if (dot > 0) input.setSelectionRange(0, dot); else input.select();
    input.addEventListener('keydown', ev => { if (ev.key === 'Enter') { ev.preventDefault(); done(input.value.trim()); } });
  });
}
// Noms déjà pris à destination : remplacer (fusionner un dossier téléversé), garder les deux, ou ignorer ; une fois
// pour tous si la personne le veut.
async function askConflicts(names, where, kinds, merge) {
  const decisions = {};
  let all = null;
  for (let i = 0; i < names.length; i++) {
    const name = names[i];
    if (all) { decisions[name] = all; continue; }
    const a = await askOne(name, where, kinds[name] === 'dir', names.length - i - 1, merge);
    if (!a) return null;
    decisions[name] = a.decision;
    if (a.all) all = a.decision;
  }
  return decisions;
}
function askOne(name, where, isDir, others, merge) {
  return new Promise(resolve => {
    const keep = can('keepboth') || merge;
    const dlg = makeDialog('files-conflict', '<div class="dlg-h"><h2></h2></div><div class="dlg-b stack-sm"><p class="muted"></p>' +
      (others ? '<label class="check"><input type="checkbox" data-all> <span></span></label>' : '') + '</div>' +
      '<div class="dlg-f"><button type="button" class="btn ghost" value="">' + esc(tr('Annuler')) + '</button>' +
      '<button type="button" class="btn" value="skip">' + esc(tr('Ignorer')) + '</button>' +
      (keep ? '<button type="button" class="btn" value="rename">' + esc(tr('Garder les deux')) + '</button>' : '') +
      '<button type="button" class="btn primary" value="replace">' + esc(isDir && merge ? tr('Fusionner') : tr('Remplacer')) + '</button></div>');
    dlg.querySelector('h2').textContent = tr('« %(name)s » existe déjà', { name });
    dlg.querySelector('p').textContent = isDir && merge
      ? tr('Un dossier porte déjà ce nom dans « %(where)s ». « Fusionner » y ajoute le contenu envoyé, en remplaçant les fichiers de même nom.', { where })
      : isDir ? tr('Un dossier porte déjà ce nom dans « %(where)s ». « Remplacer » le retire, avec tout son contenu, au profit du nouveau.', { where })
      : tr('Un élément porte déjà ce nom dans « %(where)s ».', { where });
    if (others) dlg.querySelector('[data-all] + span').textContent = count(others, tr('Faire de même pour l’autre conflit'), tr('Faire de même pour les %(num)s autres conflits'));
    let answered = false;
    const done = v => { if (answered) return; answered = true; if (dlg.open) dlg.close(); resolve(v); };
    dlg.querySelectorAll('.dlg-f button').forEach(b => b.addEventListener('click', () =>
      done(b.value ? { decision: b.value, all: !!(dlg.querySelector('[data-all]') || {}).checked } : null)));
    dlg.addEventListener('close', () => done(null));
    dlg.showModal(); dlg.querySelector('[value="replace"]').focus();
  });
}
async function properties(items) {
  const one = items.length === 1 ? items[0] : null;
  const kv = [];
  const kindLabel = e => e.link ? tr('Lien symbolique') : e.type === 'dir' ? tr('Dossier')
    : e.type === 'file' ? (extOf(e.name) ? tr('Fichier %(ext)s', { ext: extOf(e.name).toUpperCase() }) : tr('Fichier')) : tr('Autre');
  if (one) {
    kv.push([tr('Type'), kindLabel(one)], [tr('Emplacement'), backend.label(mode === 'search' ? one.dir || '' : current)]);
    if (one.type === 'file') kv.push([tr('Taille'), human(one.size) + ' (' + Number(one.size).toLocaleString(LANG) + ' ' + tr('octets') + ')']);
    kv.push([tr('Modifié'), fmtDate(Number(one.mtime))]);
    if (Number.isInteger(one.mode) && one.mode > 0 && can('chmod')) kv.push([tr('Droits'), one.mode.toString(8).padStart(3, '0')]);
  } else {
    const size = items.filter(e => e.type === 'file').reduce((s, e) => s + (e.size || 0), 0);
    kv.push([tr('Sélection'), count(items.length, tr('%(num)s élément'), tr('%(num)s éléments'))], [tr('Fichiers sélectionnés'), human(size)]);
  }
  const dirs = items.filter(e => e.type === 'dir' && !e.link);
  const dlg = makeDialog('files-props', '<div class="dlg-h"><h2></h2></div><div class="dlg-b"><dl class="kv files-kv"></dl></div>' +
    '<div class="dlg-f"><button type="button" class="btn" value="close">' + esc(tr('Fermer')) + '</button></div>');
  dlg.querySelector('h2').textContent = one ? one.name : tr('Propriétés');
  const list = dlg.querySelector('.kv');
  const add = (k, v) => { const dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = k; dd.textContent = v; list.append(dt, dd); return dd; };
  kv.forEach(([k, v]) => add(k, v));
  dlg.querySelector('[value="close"]').onclick = () => dlg.close();
  dlg.showModal();
  if (dirs.length && can('size')) {
    const dd = add(one ? tr('Contenu') : tr('Contenu des dossiers'), tr('Calcul…'));
    let size = 0, files = 0, folders = 0, partial = false;
    try {
      for (const d of dirs) {
        const r = await backend.size(pathOfEntry(d));
        size += r.size; files += r.files; folders += r.dirs; partial = partial || r.partial;
      }
      dd.textContent = human(size) + ' · ' + count(files, tr('%(num)s fichier'), tr('%(num)s fichiers')) + ' · ' +
        count(folders, tr('%(num)s dossier'), tr('%(num)s dossiers')) + (partial ? ' · ' + tr('relevé partiel') : '');
    } catch (e) { dd.textContent = e.message; }
  }
}
function shortcuts() {
  const keys = [[[tr('Entrée')], tr('Ouvrir')], [[tr('Espace')], tr('Sélectionner ou désélectionner')],
    [[tr('Maj'), tr('clic')], tr('Sélectionner une plage')], [['Ctrl', tr('clic')], tr('Ajouter à la sélection')],
    [['Ctrl', 'A'], tr('Tout sélectionner')], [['Ctrl', 'C'], tr('Copier')], [['Ctrl', 'X'], tr('Couper')], [['Ctrl', 'V'], tr('Coller')],
    [['F2'], tr('Renommer')], [[tr('Suppr')], tr('Supprimer')], [[tr('Retour arrière')], tr('Dossier parent')],
    [['Alt', '←'], tr('Dossier précédent')], [['Alt', '→'], tr('Dossier suivant')], [['/'], tr('Rechercher')],
    [[tr('Échap')], tr('Tout désélectionner')], [[tr('Clic droit')], tr('Actions sur l’élément')]];
  const dlg = makeDialog('files-keys', '<div class="dlg-h"><h2></h2></div><div class="dlg-b"><dl class="kv files-kv files-keys-list"></dl></div>' +
    '<div class="dlg-f"><button type="button" class="btn" value="close">' + esc(tr('Fermer')) + '</button></div>');
  dlg.querySelector('h2').textContent = tr('Raccourcis clavier');
  const list = dlg.querySelector('.kv');
  keys.forEach(([chips, label]) => {
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.innerHTML = chips.map(c => '<kbd>' + esc(c) + '</kbd>').join(' + ');
    dd.textContent = label; list.append(dt, dd);
  });
  dlg.querySelector('[value="close"]').onclick = () => dlg.close();
  dlg.showModal();
}

// Aperçu : images, son, vidéo et PDF, avec les voisins du dossier (← →).
let viewer = null;
function preview(entry) {
  const list = shown.filter(e => PREVIEW_KINDS.has(kindOf(e)));
  let index = list.indexOf(entry);
  if (index < 0) return;
  let objectUrl = null, seq = 0;
  const revoke = () => { if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; } };
  if (viewer) viewer.close();
  const dlg = viewer = makeDialog('files-preview', '<div class="dlg-h"><div class="files-preview-t"><h2></h2><span class="small muted files-preview-n"></span></div>' +
    '<div class="row-actions"><button type="button" class="btn sm" data-pv="download">' + esc(tr('Télécharger')) + '</button>' +
    (can('direct') ? '<a class="btn sm ghost" data-pv="tab" target="_blank" rel="noopener">' + esc(tr('Nouvel onglet')) + '</a>' : '') +
    '<button type="button" class="dlg-x" data-pv="close" aria-label="' + esc(tr('Fermer')) + '"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div></div>' +
    '<div class="files-preview-b"><button type="button" class="files-preview-nav prev" data-pv="prev" aria-label="' + esc(tr('Précédent')) + '">‹</button>' +
    '<div class="files-preview-stage"></div><button type="button" class="files-preview-nav next" data-pv="next" aria-label="' + esc(tr('Suivant')) + '">›</button></div>');
  const stage = dlg.querySelector('.files-preview-stage');
  dlg.addEventListener('close', () => { stage.innerHTML = ''; revoke(); if (viewer === dlg) viewer = null; });
  dlg.addEventListener('click', ev => {
    const b = ev.target.closest('[data-pv]');
    if (!b) { if (ev.target === dlg) dlg.close(); return; }
    const act = b.dataset.pv;
    if (act === 'close') dlg.close();
    else if (act === 'prev') show(index - 1);
    else if (act === 'next') show(index + 1);
    else if (act === 'download') download([list[index]]);
  });
  dlg.addEventListener('keydown', ev => {
    if (ev.target.closest('video, audio')) return;
    if (ev.key === 'ArrowLeft') { ev.preventDefault(); show(index - 1); }
    if (ev.key === 'ArrowRight') { ev.preventDefault(); show(index + 1); }
  });
  async function show(i) {
    if (i < 0 || i >= list.length) return;
    index = i;
    const e = list[i], k = kindOf(e), mine = ++seq, path = pathOfEntry(e);
    dlg.querySelector('h2').textContent = e.name;
    dlg.querySelector('.files-preview-n').textContent = (list.length > 1 ? (i + 1) + ' / ' + list.length + ' · ' : '') + human(e.size);
    dlg.querySelector('.prev').disabled = i === 0;
    dlg.querySelector('.next').disabled = i === list.length - 1;
    dlg.querySelector('.files-preview-b').classList.toggle('single', list.length < 2);
    const tab = dlg.querySelector('[data-pv="tab"]');
    revoke();
    stage.innerHTML = '<div class="files-preview-wait muted">' + esc(tr('Chargement…')) + '</div>';
    let src;
    try {
      if (can('direct')) src = backend.url(path);
      else {
        const blob = await backend.blob(e, path, PREVIEW_MAX);
        if (mine !== seq) return;
        src = objectUrl = URL.createObjectURL(blob);
      }
    } catch (err) { if (mine === seq) stage.innerHTML = '<div class="files-preview-wait err-text">' + esc(err.message) + '</div>'; return; }
    if (tab) tab.href = src;
    const media = k === 'image' ? Object.assign(document.createElement('img'), { alt: e.name })
      : k === 'video' ? Object.assign(document.createElement('video'), { controls: true, autoplay: true, playsInline: true })
      : k === 'audio' ? Object.assign(document.createElement('audio'), { controls: true, autoplay: true })
      : Object.assign(document.createElement('iframe'), { title: e.name });
    media.className = 'files-preview-media ' + k;
    media.addEventListener('error', () => {
      if (mine === seq) stage.innerHTML = '<div class="files-preview-wait err-text">' + esc(tr('Aperçu impossible : le navigateur ne lit peut-être pas ce format. Téléchargez le fichier.')) + '</div>';
    });
    media.src = src;
    stage.innerHTML = ''; stage.appendChild(media);
  }
  dlg.showModal();
  show(index);
}

// Éditeur de texte (lecture seule sans le droit d'écrire) ; Ctrl+S enregistre.
async function openEditor(e, path) {
  const ro = !writable();
  const dlg = makeDialog('files-editor', '<form method="dialog" class="files-editor-form"><div class="dlg-h"><h2></h2><span class="mono small muted files-editor-path"></span></div>' +
    '<div class="dlg-b"><textarea spellcheck="false" wrap="off"' + (ro ? ' readonly' : '') + '></textarea></div>' +
    '<div class="dlg-f"><span class="files-editor-note muted small"></span><button type="button" class="btn" value="cancel">' + esc(tr('Fermer')) + '</button>' +
    (ro ? '' : '<button type="button" class="btn primary" value="save">' + esc(tr('Enregistrer')) + '</button>') + '</div></form>');
  const area = dlg.querySelector('textarea'), note = dlg.querySelector('.files-editor-note');
  area.setAttribute('aria-label', tr('Contenu du fichier'));
  dlg.querySelector('h2').textContent = ro ? tr('Lire « %(name)s »', { name: e.name }) : tr('Modifier « %(name)s »', { name: e.name });
  dlg.querySelector('.files-editor-path').textContent = backend.label(path);
  note.textContent = tr('Chargement…');
  let original = '', expect = null, closing = false;
  const dirty = () => !ro && area.value !== original;
  async function close() {
    if (dirty()) {
      const ok = await confirmDialog(tr('Fermer sans enregistrer ?'), tr('Les modifications de « %(name)s » seront perdues.', { name: e.name }), tr('Fermer sans enregistrer'));
      if (!ok) return;
    }
    closing = true; dlg.close();
  }
  dlg.addEventListener('cancel', ev => { if (!closing && dirty()) { ev.preventDefault(); close(); } });
  dlg.querySelector('[value="cancel"]').onclick = close;
  async function save() {
    if (ro) return;
    note.textContent = tr('Enregistrement…');
    try {
      const mtime = await backend.save(path, area.value, expect);
      if (mtime != null) expect = mtime;
      original = area.value;
      note.textContent = tr('Enregistré.'); toast(tr('Fichier enregistré.'), 'ok');
      refresh();
    } catch (err) { note.textContent = err.message; }
  }
  const saveBtn = dlg.querySelector('[value="save"]');
  if (saveBtn) saveBtn.onclick = save;
  area.addEventListener('keydown', ev => { if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 's') { ev.preventDefault(); save(); } });
  dlg.showModal();
  try {
    const got = await backend.text(path);
    const text = typeof got === 'string' ? got : got.text;
    expect = typeof got === 'string' ? null : got.mtime;
    area.value = original = text;
    note.textContent = human(enc.encode(text).length);
    if (!ro) area.focus();
  } catch (err) { note.textContent = err.message; }
}

// ---------------------------------------------------------------- actions
function open(e) {
  if (!e) return;
  if (e.type === 'dir') return navigate(pathOfEntry(e));
  if (e.type !== 'file') return;
  const k = kindOf(e);
  if (PREVIEW_KINDS.has(k)) return preview(e);
  if (k === 'text' && e.size <= EDIT_MAX) return openEditor(e, pathOfEntry(e));
  download([e]);
}
async function download(items) {
  const files = items.filter(e => e.type === 'file'), dirs = items.filter(e => e.type === 'dir');
  if (can('zip') && mode === 'list' && (dirs.length || files.length > 1)) {
    backend.zip(current, items.map(e => e.name));
    toast(tr('Archive ZIP en préparation : le téléchargement va commencer.'), 'ok');
    return;
  }
  if (dirs.length) toast(can('zip') ? tr('Ouvrez le dossier pour le télécharger en ZIP.') : tr('Les dossiers ne se téléchargent pas depuis un poste : ouvrez-les et choisissez leurs fichiers.'), 'error');
  for (const e of files) {
    if (can('direct')) { backend.download(e, pathOfEntry(e)); await sleep(300); continue; }
    const t = transfer('↓ ' + e.name);
    try { await backend.download(e, pathOfEntry(e), t); t.done(tr('terminé')); } catch (err) { t.done(err.message, 'err'); }
  }
}
async function rename(e) {
  const next = await prompt(tr('Renommer'), tr('Nouveau nom'), e.name, e.type !== 'dir');
  if (!next || next === e.name) return;
  try { await backend.rename(pathOf(e.name), next); await refresh({ select: next }); }
  catch (err) { toast(err.message, 'error'); }
}
async function removeItems(items) {
  if (!items.length) return;
  const one = items.length === 1 ? items[0] : null;
  const where = backend.kind === 'share' ? tr('du partage') : tr('du poste');
  const ok = await confirmDialog(
    one ? tr('Supprimer « %(name)s » ?', { name: one.name }) : tr('Supprimer %(num)s éléments ?', { num: items.length }),
    one && one.type === 'dir' ? tr('Le dossier « %(name)s » et tout son contenu seront supprimés %(where)s. Cette action est définitive.', { name: one.name, where })
      : one ? tr('« %(name)s » sera supprimé %(where)s. Cette action est définitive.', { name: one.name, where })
      : tr('Les éléments choisis, avec le contenu des dossiers, seront supprimés %(where)s. Cette action est définitive.', { where }),
    tr('Supprimer'));
  if (!ok) return;
  const paths = pathsOf(items);
  setSelected([]);
  await runJob('delete', paths, null, {});
}
function toClip(items, how) {
  if (!items.length) return;
  clip = { mode: how, paths: pathsOf(items), names: items.map(e => e.name), types: items.map(e => e.type),
    share: backend.shareId != null ? backend.shareId : null, dir: mode === 'search' ? null : current };
  if (backend.saveClip) backend.saveClip(clip);
  renderPaste();
  toast(how === 'copy'
    ? count(items.length, tr('Copié : ouvrez le dossier de destination, puis « Coller ».'), tr('%(num)s éléments copiés : ouvrez le dossier de destination, puis « Coller ».'))
    : count(items.length, tr('Coupé : ouvrez le dossier de destination, puis « Déplacer ici ».'), tr('%(num)s éléments coupés : ouvrez le dossier de destination, puis « Déplacer ici ».')), 'ok');
}
async function paste(to = current, given = clip, how = given && given.mode) {
  if (!given || !growable()) return;
  const from = given.share != null ? given.share : undefined;
  if (from !== undefined && from !== backend.shareId && !can('cross')) return;
  const sameDir = (from === undefined || from === backend.shareId) && given.dir === to;
  if (how === 'move' && sameDir) { toast(tr('Ces éléments sont déjà dans ce dossier.')); return; }
  let decisions = {};
  try {
    if (!(how === 'copy' && sameDir && can('keepboth'))) {
      const names = await backend.conflicts(given.paths, to, from);
      if (names.length) {
        const kinds = {};
        given.names.forEach((n, i) => { kinds[n] = (given.types || [])[i]; });
        decisions = await askConflicts(names, backend.label(to), kinds, false);
        if (!decisions) return;
      }
    }
  } catch (e) { toast(e.message, 'error'); return; }
  if (how === 'move' && given === clip) { clip = null; if (backend.saveClip) backend.saveClip(null); renderPaste(); }
  await runJob(how, given.paths, to, decisions, from);
}
async function makeDir() {
  const name = await prompt(tr('Nouveau dossier'), tr('Nom du dossier'), tr('Nouveau dossier'));
  if (!name) return;
  try { await backend.mkdir(pathOf(name)); await refresh({ select: name }); }
  catch (err) { toast(err.message, 'error'); }
}
async function makeFile() {
  const name = await prompt(tr('Nouveau fichier texte'), tr('Nom du fichier'), tr('nouveau.txt'), true);
  if (!name) return;
  try {
    await backend.newfile(pathOf(name));
    await refresh({ select: name });
    openEditor({ name, size: 0, type: 'file' }, pathOf(name));
  } catch (err) { toast(err.message, 'error'); }
}
async function chmod(e) {
  const value = await prompt(tr('Droits (octal)'), tr('Mode, par exemple 644 ou 755'), Number.isInteger(e.mode) ? e.mode.toString(8).padStart(3, '0') : '644');
  if (!value) return;
  if (!/^[0-7]{3,4}$/.test(value)) { toast(tr('Mode invalide : trois ou quatre chiffres de 0 à 7.'), 'error'); return; }
  try { await backend.chmod(pathOf(e.name), parseInt(value, 8)); await refresh(); } catch (err) { toast(err.message, 'error'); }
}
function toggleUsage() {
  mode = mode === 'usage' ? 'list' : 'usage';
  const label = mode === 'usage' ? tr('Revenir aux fichiers') : tr('Espace disque : voir ce qui occupe le plus');
  usageToggle.setAttribute('aria-pressed', String(mode === 'usage'));
  usageToggle.title = label; usageToggle.setAttribute('aria-label', label);
  if (searchEl) { clearSearch(false); searchEl.disabled = mode === 'usage'; }
  hiddenToggle.disabled = mode === 'usage';
  selected = new Set();
  if (current != null) navigate(current, { history: false });
}
function toggleHidden() {
  showHidden = !showHidden;
  localStorage.setItem('loco-files-hidden', showHidden ? '1' : '0');
  hiddenLabel();
  render();
}
function hiddenLabel() {
  const label = showHidden ? tr('Masquer les fichiers cachés') : tr('Afficher les fichiers cachés');
  hiddenToggle.setAttribute('aria-pressed', String(showHidden)); hiddenToggle.setAttribute('aria-label', label); hiddenToggle.title = label;
}

// ---------------------------------------------------------------- téléversements (fichiers, dossiers entiers)
async function uploadItems(items, to = current) {
  if (!growable() || !items.length) return;
  const files = items.filter(i => i.file), dirs = items.filter(i => i.dir);
  const total = files.reduce((s, i) => s + i.file.size, 0);
  const meta = backend.kind === 'share' ? lastMeta : null;
  if (meta && meta.limit && meta.used != null && total > meta.limit - meta.used) {
    toast(tr('Pas assez de place dans le partage : %(need)s à envoyer, %(free)s avant sa taille limite.', { need: human(total), free: human(Math.max(0, meta.limit - meta.used)) }), 'error');
    return;
  }
  // Premier niveau : ce qui porte déjà son nom à destination est soumis à la personne avant le moindre envoi.
  const top = {};
  files.forEach(i => { const n = i.rel ? i.rel.split('/')[0] : i.file.name; top[n] = top[n] || (i.rel ? 'dir' : 'file'); });
  dirs.forEach(i => { top[i.dir.split('/')[0]] = 'dir'; });
  let taken;
  try { taken = new Set((to === current ? listing : (await backend.list(to)).entries).map(e => e.name)); }
  catch (e) { toast(e.message, 'error'); return; }
  const conflicts = Object.keys(top).filter(n => taken.has(n));
  let decisions = {};
  if (conflicts.length) {
    decisions = await askConflicts(conflicts, backend.label(to), top, true);
    if (!decisions) return;
  }
  // « Garder les deux » pour un dossier : son contenu part dans « nom (2) ».
  const renamed = {};
  for (const n of conflicts) if (decisions[n] === 'rename' && top[n] === 'dir') renamed[n] = freeName(n, taken);
  const topOf = rel => { const [first, ...rest] = rel.split('/'); return [renamed[first] || first, ...rest].join('/'); };
  const skipped = n => decisions[n] === 'skip';
  // Dossiers d'abord (vides compris), du moins profond au plus profond.
  const needed = new Set();
  dirs.forEach(i => { if (!skipped(i.dir.split('/')[0])) needed.add(topOf(i.dir)); });
  files.forEach(i => { if (i.rel && !skipped(i.rel.split('/')[0])) needed.add(topOf(i.rel)); });
  const made = new Map();
  try {
    for (const rel of [...needed].sort((a, b) => a.split('/').length - b.split('/').length)) made.set(rel, await backend.mkdirs(to, rel));
  } catch (e) { toast(e.message, 'error'); refresh(); return; }
  let ok = 0, failed = 0, passed = 0;
  const ask = name => askOne(name, backend.label(to), false, 0, false).then(a => (a ? a.decision : 'skip'));
  await Promise.all(files.map(i => {
    const first = i.rel ? i.rel.split('/')[0] : i.file.name;
    if (skipped(first)) { passed++; return Promise.resolve(); }
    const task = { file: i.file, dir: i.rel ? made.get(topOf(i.rel)) : to, name: i.file.name, conflict: null, ask, cancelled: false };
    if (decisions[first] === 'replace') task.conflict = 'replace';                     // fichier remplacé, ou dossier fusionné
    if (!i.rel && decisions[first] === 'rename') { if (can('keepboth')) task.conflict = 'rename'; else task.name = freeName(first, taken); }
    return slot(async () => {
      const t = transfer('↑ ' + (i.rel ? i.rel + '/' : '') + i.file.name, () => { task.cancelled = true; });
      try {
        await backend.upload(task, (done, size) => t.set(done, size));
        t.done(tr('terminé')); ok++;
      } catch (e) { if (e.skipped) { t.done(e.message); passed++; } else { t.done(e.message, 'err'); failed++; } }
      if (to === current) scheduleRefresh();
    }).catch(() => { passed++; });
  }));
  await refresh();
  if (files.length > 1 || failed) {
    toast(tr('Téléversement terminé : %(ok)s envoyés, %(failed)s en échec, %(skipped)s ignorés.', { ok, failed, skipped: passed }), failed ? 'error' : 'ok');
  }
}
function freeName(name, taken) {
  const dot = name.lastIndexOf('.'), stem = dot > 0 ? name.slice(0, dot) : name, ext = dot > 0 ? name.slice(dot) : '';
  for (let n = 2; ; n++) { const c = stem + ' (' + n + ')' + ext; if (!taken.has(c)) return c; }
}
let refreshTimer = 0;
function scheduleRefresh() { clearTimeout(refreshTimer); refreshTimer = setTimeout(() => refresh(), 600); }
// Glissé depuis l'ordinateur : fichiers et dossiers entiers (repérés ici, avant le premier « await »).
function dropped(dt) {
  const entries = [...(dt.items || [])].filter(i => i.kind === 'file').map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  const plain = [...dt.files];
  return (async () => {
    if (!entries.length) return plain.map(file => ({ file, rel: '' }));
    const out = [];
    async function walk(entry, rel) {
      if (entry.isFile) out.push({ file: await new Promise((res, rej) => entry.file(res, rej)), rel });
      else if (entry.isDirectory) {
        const here = rel ? rel + '/' + entry.name : entry.name;
        out.push({ dir: here });
        const reader = entry.createReader();
        for (;;) {
          const batch = await new Promise((res, rej) => reader.readEntries(res, rej));
          if (!batch.length) break;
          for (const child of batch) await walk(child, here);
        }
      }
    }
    for (const entry of entries) await walk(entry, '');
    return out;
  })();
}
function fromInput(input) {
  return [...input.files].map(file => ({ file, rel: file.webkitRelativePath ? file.webkitRelativePath.split('/').slice(0, -1).join('/') : '' }));
}

// ---------------------------------------------------------------- menu d'actions (clic droit, bouton ⋮)
let menu = null;
function closeMenu() { if (menu) { const m = menu; menu = null; try { m.hidePopover(); } catch { /* sans popover */ } m.remove(); } }
function openMenu(items, x, y, anchorEl) {
  closeMenu();
  const list = document.createElement('div');
  list.className = 'menu-list files-ctx'; list.setAttribute('role', 'menu');
  list.innerHTML = items.map(it => it === '-' ? '<hr>' : '<button type="button" class="menu-item' + (it.danger ? ' danger' : '') + '" role="menuitem" data-ctx="' + it.act + '">' +
    esc(it.label) + (it.key ? '<span class="shortcut">' + esc(it.key) + '</span>' : '') + '</button>').join('');
  root.appendChild(list);
  menu = list;
  if (typeof list.showPopover === 'function') { list.setAttribute('popover', 'manual'); list.showPopover(); }
  else list.classList.add('files-ctx-abs');
  const w = list.offsetWidth, h = list.offsetHeight, vw = document.documentElement.clientWidth, vh = window.innerHeight, edge = 8;
  if (anchorEl) { const r = anchorEl.getBoundingClientRect(); x = r.right - w; y = r.bottom + 6; if (y + h > vh - edge) y = r.top - 6 - h; }
  list.style.left = Math.max(edge, Math.min(x, vw - w - edge)) + 'px';
  list.style.top = Math.max(edge, Math.min(y, vh - h - edge)) + 'px';
  const actions = Object.fromEntries(items.filter(it => it !== '-').map(it => [it.act, it.run]));
  list.addEventListener('click', ev => { const b = ev.target.closest('[data-ctx]'); if (!b) return; closeMenu(); actions[b.dataset.ctx](); });
  list.addEventListener('keydown', ev => {
    const buttons = [...list.querySelectorAll('.menu-item')], i = buttons.indexOf(document.activeElement);
    if (ev.key === 'ArrowDown') { ev.preventDefault(); buttons[(i + 1) % buttons.length].focus(); }
    if (ev.key === 'ArrowUp') { ev.preventDefault(); buttons[(i - 1 + buttons.length) % buttons.length].focus(); }
    if (ev.key === 'Escape') { ev.preventDefault(); closeMenu(); if (anchorEl) anchorEl.focus(); }
    if (ev.key === 'Tab') closeMenu();
  });
  const first = list.querySelector('.menu-item');
  if (first) first.focus({ preventScroll: true });
}
document.addEventListener('pointerdown', ev => { if (menu && !menu.contains(ev.target)) closeMenu(); }, true);
window.addEventListener('scroll', ev => { if (menu && !menu.contains(ev.target)) closeMenu(); }, true);
window.addEventListener('resize', closeMenu);
function itemMenu(items) {
  const one = items.length === 1 ? items[0] : null, k = one ? kindOf(one) : '', dirs = items.some(e => e.type === 'dir');
  const editable = one && k === 'text' && one.size <= EDIT_MAX;
  const out = [];
  if (one && (one.type === 'dir' || one.type === 'file')) {
    out.push({ act: 'open', key: tr('Entrée'), run: () => open(one),
      label: one.type === 'dir' ? tr('Ouvrir') : PREVIEW_KINDS.has(k) ? tr('Aperçu') : editable ? (writable() ? tr('Modifier le texte') : tr('Lire le texte')) : tr('Télécharger') });
  }
  if (mode === 'search' && one) out.push({ act: 'reveal', label: tr('Afficher dans son dossier'), run: () => reveal(one) });
  const zip = can('zip') && mode === 'list' && (dirs || items.length > 1);
  if ((zip || !dirs) && !(one && one.type === 'file' && !PREVIEW_KINDS.has(k) && !editable)) {
    out.push({ act: 'download', label: zip ? tr('Télécharger en ZIP') : tr('Télécharger'), run: () => download(items) });
  }
  if (writable() && one && mode === 'list') out.push({ act: 'rename', label: tr('Renommer'), key: 'F2', run: () => rename(one) });
  out.push('-');
  if (mode === 'list' || can('cross')) out.push({ act: 'copy', label: tr('Copier'), key: 'Ctrl+C', run: () => toClip(items, 'copy') });
  if (writable() && mode === 'list') out.push({ act: 'cut', label: tr('Couper'), key: 'Ctrl+X', run: () => toClip(items, 'move') });
  if (one && one.type === 'dir' && clip && growable() && mode === 'list') out.push({ act: 'paste-in', label: tr('Coller dans « %(name)s »', { name: one.name }), run: () => paste(pathOf(one.name)) });
  if (one && can('chmod') && writable() && mode === 'list') out.push({ act: 'chmod', label: tr('Droits…'), run: () => chmod(one) });
  out.push({ act: 'props', label: tr('Propriétés'), run: () => properties(items) });
  if (writable()) out.push('-', { act: 'delete', label: tr('Supprimer'), key: tr('Suppr'), danger: true, run: () => removeItems(items) });
  return out.filter((it, i, a) => !(it === '-' && (i === 0 || a[i - 1] === '-' || i === a.length - 1)));
}
function backgroundMenu() {
  const out = [];
  if (growable() && mode === 'list') {
    out.push({ act: 'mkdir', label: tr('Nouveau dossier'), run: makeDir }, { act: 'newfile', label: tr('Nouveau fichier texte'), run: makeFile },
      { act: 'upload', label: tr('Téléverser des fichiers…'), run: () => root.querySelector('[data-files-input="files"]').click() });
    if (pasteBtn && !pasteBtn.hidden) out.push({ act: 'paste', label: pasteBtn.textContent, key: 'Ctrl+V', run: () => paste() });
    out.push('-');
  }
  if (mode === 'list' && shown.length) out.push({ act: 'all', label: tr('Tout sélectionner'), key: 'Ctrl+A', run: () => setSelected(rows().map(r => r.dataset.key)) });
  out.push({ act: 'refresh', label: tr('Actualiser'), run: () => refresh() }, { act: 'keys', label: tr('Raccourcis clavier'), run: shortcuts });
  return out;
}
function reveal(e) { navigate(e.dir || '', { select: e.name }); }

// ---------------------------------------------------------------- événements
root.addEventListener('click', ev => {
  if (ev.target.closest('dialog, .files-ctx')) return;
  if (ev.target.closest('[data-show-hidden]')) { toggleHidden(); return; }
  if (ev.target.closest('[data-deep]')) { deepSearch(); return; }
  const crumb = ev.target.closest('.files-crumb, .files-root');
  if (crumb) { navigate(crumb.dataset.path); return; }
  const sortBtn = ev.target.closest('[data-sort]');
  if (sortBtn) {
    const key = sortBtn.dataset.sort;
    sort = { key, dir: sort.key === key ? -sort.dir : (key === 'name' ? 1 : -1) };
    localStorage.setItem('loco-files-sort', JSON.stringify(sort));
    render(); return;
  }
  const nav = ev.target.closest('[data-files]');
  if (nav) {
    const actions = { refresh: () => refresh(), up: goUp, back: goBack, forward: goForward, mkdir: makeDir, newfile: makeFile,
      paste: () => paste(), usage: toggleUsage, hidden: toggleHidden, keys: shortcuts };
    const run = actions[nav.dataset.files];
    if (run && !nav.disabled) { const m = nav.closest('details.menu'); if (m) m.open = false; run(); }
    return;
  }
  const row = ev.target.closest('tr[data-key]');
  if (!row) return;
  const key = row.dataset.key;
  if (mode === 'usage') { if (ev.target.closest('[data-item-open]')) navigate(pathOf(row.dataset.name)); return; }
  if (ev.target.closest('[data-more]')) { openMenu(itemMenu(targets(key)), 0, 0, ev.target.closest('[data-more]')); return; }
  if (ev.target.closest('[data-reveal]')) { reveal(entryOf(key)); return; }
  if (ev.target.closest('[data-item-pick]')) { pick(key, { type: 'toggle' }); return; }
  if (ev.target.closest('[data-item-open]') && !ev.ctrlKey && !ev.metaKey && !ev.shiftKey) { open(entryOf(key)); return; }
  if (ev.target.closest('td')) pick(key, ev);
});
root.addEventListener('change', ev => {
  if (ev.target.matches('[data-items-all]')) setSelected(ev.target.checked ? rows().map(r => r.dataset.key) : []);
  const input = ev.target.closest('[data-files-input]');
  if (input && input.files.length) {
    const m = input.closest('details.menu'); if (m) m.open = false;
    uploadItems(fromInput(input)); input.value = '';
  }
});
tbody.addEventListener('dblclick', ev => {
  const row = ev.target.closest('tr[data-key]');
  if (row && mode !== 'usage' && !ev.target.closest('[data-item-pick], [data-more], [data-item-open]')) open(entryOf(row.dataset.key));
});
pane.addEventListener('contextmenu', ev => {
  if (mode === 'usage' || ev.target.closest('input, textarea')) return;
  ev.preventDefault();
  const row = ev.target.closest('tr[data-key]');
  if (row) { const items = targets(row.dataset.key); focusRow(row.dataset.key, false); openMenu(itemMenu(items), ev.clientX, ev.clientY); }
  else { setSelected([]); openMenu(backgroundMenu(), ev.clientX, ev.clientY); }
});
if (searchEl) {
  searchEl.addEventListener('input', () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => { filter = fold(searchEl.value.trim()); if (mode === 'search') mode = 'list'; render(); }, 120);
  });
  searchEl.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') { ev.preventDefault(); if (can('search') && searchEl.value.trim()) deepSearch(); }
    if (ev.key === 'Escape') { ev.preventDefault(); clearSearch(); searchEl.blur(); }
    if (ev.key === 'ArrowDown') { ev.preventDefault(); const first = rows()[0]; if (first) { first.focus(); focusKey = first.dataset.key; } }
  });
}
// Clavier : la liste se pilote comme un explorateur de fichiers.
root.addEventListener('keydown', ev => {
  if (ev.target.closest('dialog, .files-ctx') || mode === 'usage') return;
  if (ev.target.matches('input, textarea, select') && !ev.target.matches('[data-item-pick], [data-items-all]')) return;
  const key = ev.key, ctrl = ev.ctrlKey || ev.metaKey, all = rows();
  const at = all.findIndex(r => r === document.activeElement || r.contains(document.activeElement));
  const cur = at >= 0 ? all[at].dataset.key : focusKey;
  const move = to => {
    if (!all.length) return;
    const row = all[Math.max(0, Math.min(all.length - 1, to))];
    row.focus(); focusKey = row.dataset.key; all.forEach(r => { r.tabIndex = r === row ? 0 : -1; });
    if (ev.shiftKey) pick(row.dataset.key, { shiftKey: true }); else if (!ctrl) { setSelected([row.dataset.key]); anchor = row.dataset.key; }
  };
  if (key === 'ArrowDown') { ev.preventDefault(); move(at + 1); }
  else if (key === 'ArrowUp' && !ev.altKey) { ev.preventDefault(); move(at - 1); }
  else if (key === 'Home' && at >= 0) { ev.preventDefault(); move(0); }
  else if (key === 'End' && at >= 0) { ev.preventDefault(); move(all.length - 1); }
  else if (key === ' ' && cur != null && at >= 0) { ev.preventDefault(); pick(cur, { type: 'toggle' }); }
  else if (key === 'Enter' && cur != null && at >= 0 && !ev.target.closest('button')) { ev.preventDefault(); open(entryOf(cur)); }
  else if ((key === 'Backspace' && !ev.target.closest('button')) || (ev.altKey && key === 'ArrowUp')) { ev.preventDefault(); goUp(); }
  else if (ev.altKey && key === 'ArrowLeft') { ev.preventDefault(); goBack(); }
  else if (ev.altKey && key === 'ArrowRight') { ev.preventDefault(); goForward(); }
  else if (key === 'Delete' && selected.size && writable()) { ev.preventDefault(); removeItems(targets()); }
  else if (key === 'F2' && cur != null && writable() && mode === 'list') { ev.preventDefault(); rename(entryOf(cur)); }
  else if (ctrl && key.toLowerCase() === 'a' && mode === 'list') { ev.preventDefault(); setSelected(all.map(r => r.dataset.key)); }
  else if (ctrl && key.toLowerCase() === 'c' && selected.size) { ev.preventDefault(); toClip(targets(), 'copy'); }
  else if (ctrl && key.toLowerCase() === 'x' && selected.size && writable() && mode === 'list') { ev.preventDefault(); toClip(targets(), 'move'); }
  else if (ctrl && key.toLowerCase() === 'v' && clip && pasteBtn && !pasteBtn.hidden) { ev.preventDefault(); paste(); }
  else if (key === 'Escape' && selected.size) { ev.preventDefault(); setSelected([]); }
  else if (key === '/' && searchEl && !searchEl.disabled) { ev.preventDefault(); searchEl.focus(); searchEl.select(); }
  else if (key === 'ContextMenu' && cur != null) { ev.preventDefault(); const r = all[Math.max(0, at)]; openMenu(itemMenu(targets(cur)), 0, 0, r && r.querySelector('[data-more]')); }
});
// Barre de sélection (bas de l'écran).
selbar.addEventListener('click', ev => {
  const b = ev.target.closest('[data-sel]');
  if (!b) return;
  const items = targets();
  ({ download: () => download(items), copy: () => toClip(items, 'copy'), cut: () => toClip(items, 'move'),
    delete: () => removeItems(items), clear: () => setSelected([]) })[b.dataset.sel]();
});

// Glisser-déposer : depuis l'ordinateur (téléversement), ou des lignes vers un dossier (déplacer ; Ctrl : copier).
const INTERNAL = 'application/x-loco-files';
let dragDepth = 0, lit = null;
tbody.addEventListener('dragstart', ev => {
  const row = ev.target.closest('tr[data-key]');
  if (!row || mode !== 'list') return;
  const items = targets(row.dataset.key);
  ev.dataTransfer.setData(INTERNAL, JSON.stringify(items.map(e => e.name)));
  ev.dataTransfer.effectAllowed = 'copyMove';
  // Chrome : un fichier glissé hors de la page s'enregistre sur l'ordinateur.
  if (items.length === 1 && items[0].type === 'file' && can('direct')) {
    ev.dataTransfer.setData('DownloadURL', 'application/octet-stream:' + items[0].name + ':' + new URL(backend.url(pathOf(items[0].name), true), location.href).href);
  }
});
function dropTarget(ev) {
  const row = ev.target.closest('tr[data-type="dir"]'), crumb = ev.target.closest('.files-crumb');
  if (row && mode === 'list' && !selected.has(row.dataset.key)) return { el: row, path: pathOf(row.dataset.name) };
  if (crumb && crumb.dataset.path !== current) return { el: crumb, path: crumb.dataset.path };
  return null;
}
function light(el) { if (lit !== el) { if (lit) lit.classList.remove('files-drop-on'); lit = el; if (el) el.classList.add('files-drop-on'); } }
root.addEventListener('dragover', ev => {
  const types = [...ev.dataTransfer.types], internal = types.includes(INTERNAL), external = types.includes('Files');
  if ((!internal && !external) || !growable() && !(internal && writable())) return;
  const target = dropTarget(ev);
  if (internal && !target) { light(null); return; }
  ev.preventDefault();
  ev.dataTransfer.dropEffect = internal ? (ev.ctrlKey || ev.altKey ? 'copy' : 'move') : 'copy';
  light(target ? target.el : null);
});
root.addEventListener('drop', ev => {
  const target = dropTarget(ev);
  light(null); dragDepth = 0; pane.classList.remove('dropping');
  const raw = ev.dataTransfer.getData(INTERNAL);
  if (raw) {
    if (!target) return;
    ev.preventDefault();
    const names = JSON.parse(raw), items = names.map(n => listing.find(e => e.name === n)).filter(Boolean);
    const how = ev.ctrlKey || ev.altKey ? 'copy' : 'move';
    paste(target.path, { mode: how, paths: items.map(e => pathOf(e.name)), names: items.map(e => e.name), types: items.map(e => e.type),
      share: backend.shareId != null ? backend.shareId : null, dir: current }, how);
    return;
  }
  if (![...ev.dataTransfer.types].includes('Files') || !growable()) return;
  ev.preventDefault();
  dropped(ev.dataTransfer).then(items => uploadItems(items, target ? target.path : current)).catch(e => toast(e.message, 'error'));
});
pane.addEventListener('dragenter', ev => {
  if (![...ev.dataTransfer.types].includes('Files') || !growable()) return;
  ev.preventDefault(); if (++dragDepth === 1) pane.classList.add('dropping');
});
pane.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; pane.classList.remove('dropping'); } });
root.addEventListener('dragend', () => light(null));

// ---------------------------------------------------------------- cycle de vie
backend.onClosed = text => { setStatus(text, 'err'); };
hiddenLabel();
async function start() {
  if (start.done) return;
  start.done = true;
  setStatus(tr('Ouverture de la session…'));
  root.hidden = false;
  try {
    const info = await backend.start();
    renderRoots(info.roots);
    await navigate(info.home, { history: false });
  } catch (e) {
    if (e.code !== 'reserved') { setStatus(e.message, 'err'); return; }
    // Poste réservé par un autre compte : son mot de passe (fenêtre de la fiche), puis la session s'ouvre.
    setStatus('');
    emptyEl.classList.remove('hidden');
    emptyEl.innerHTML = esc(e.message) + ' <button type="button" class="btn sm" data-reserved-unlock>' + esc(tr('Saisir le mot de passe')) + '</button>';
    emptyEl.querySelector('[data-reserved-unlock]').addEventListener('click', () => document.dispatchEvent(new CustomEvent('loco:unlock')));
    document.addEventListener('loco:unlocked', () => { start.done = false; emptyEl.classList.add('hidden'); start(); }, { once: true });
  }
}
if (backend.lazy) {
  // Fiche du poste : la session s'ouvre à la première visite de l'onglet Fichiers (app.js bascule la classe « on »).
  const panel = root.closest('.tab-panel');
  const watch = () => {
    if (!panel || panel.classList.contains('on')) start();
    else if (selected.size) setSelected([]);                     // onglet quitté : la barre de sélection s'efface
  };
  if (panel) new MutationObserver(watch).observe(panel, { attributes: true, attributeFilter: ['class'] });
  watch();
  window.addEventListener('pagehide', () => backend.close(true));
} else start();
