/* Canal persistant d'une session distante (WebSocket) : mêmes échanges que les allers-retours HTTPS, mais le serveur
   pousse ce qui arrive, sans attendre d'être interrogé. Si le canal n'est pas offert ou tombe, les échanges repartent
   par HTTPS : le protocole (paquets numérotés, acquittements) est le même, la session ne s'en aperçoit pas. Un échange
   HTTPS de temps en temps garde le cookie de session frais : seule une réponse HTTP le renouvelle. */
export function relayLink(io, onPush) {
  const address = new URL(io.replace(/\/io$/, '/ws'), window.location.href);
  address.protocol = address.protocol === 'https:' ? 'wss:' : 'ws:';
  const waiting = new Map();
  // lastHttp : la page vient d'être servie par HTTP, son cookie est frais — le premier échange passe donc par le canal
  // dès qu'il est ouvert (le serveur ne pousse rien avant d'avoir eu un échange par ce canal).
  let socket = null, open = false, counter = 0, retryIn = 30000, lastHttp = Date.now(), closed = false, timer = null;
  function drop(ws) {
    if (socket !== ws) return;
    socket = null; open = false;
    waiting.forEach(w => w.reject(new Error('canal fermé'))); waiting.clear();
    try { ws.close(); } catch (e) { /* déjà fermé */ }
    if (closed) return;
    clearTimeout(timer); timer = setTimeout(connect, retryIn); retryIn = Math.min(600000, retryIn * 2);
  }
  function connect() {
    if (closed || socket) return;
    let ws;
    try { ws = new WebSocket(address); } catch (e) { return; }      // navigateur ou politique qui refuse : HTTPS seul
    socket = ws;
    ws.onopen = () => { if (socket !== ws) return; open = true; retryIn = 30000; onPush(null); };
    ws.onmessage = event => {
      let m;
      try { m = JSON.parse(event.data); } catch (e) { return; }
      const w = m.re && waiting.get(m.re);
      if (w) { waiting.delete(m.re); w.resolve({ status: m.status, data: m.body }); }
      else if (!m.re) onPush(m.status === 200 ? m.body : null);
    };
    ws.onclose = ws.onerror = () => drop(ws);
  }
  async function viaFetch(body, timeout) {
    lastHttp = Date.now();
    const response = await fetch(io, { method: 'POST', body, credentials: 'same-origin',
      headers: { 'X-Requested-With': 'fetch' }, signal: AbortSignal.timeout(timeout) });
    return { status: response.status, data: response.ok ? await response.json() : null };
  }
  function viaSocket(ws, body, timeout) {
    const message = { re: ++counter };
    body.forEach((value, name) => { message[name] = value; });
    return new Promise((resolve, reject) => {
      const guard = setTimeout(() => { waiting.delete(message.re); reject(new Error('délai dépassé')); }, timeout);
      waiting.set(message.re, { resolve: v => { clearTimeout(guard); resolve(v); }, reject: e => { clearTimeout(guard); reject(e); } });
      try { ws.send(JSON.stringify(message)); } catch (e) { waiting.delete(message.re); clearTimeout(guard); reject(e); }
    });
  }
  // Un échange : { status, data } comme une réponse HTTP. onPush(data) reçoit ce que le serveur pousse de lui-même
  // (null : rien à afficher, mais un échange est à refaire — canal tout juste ouvert, refus).
  async function exchange(body, timeout = 10000) {
    const ws = socket;
    if (ws && open && Date.now() - lastHttp < 240000) {
      try { return await viaSocket(ws, body, timeout); } catch (e) { drop(ws); }
    }
    return viaFetch(body, timeout);
  }
  connect();
  return {
    exchange,
    get live() { return open; },
    close() { closed = true; clearTimeout(timer); if (socket) drop(socket); },
  };
}
