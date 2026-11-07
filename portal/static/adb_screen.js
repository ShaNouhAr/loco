/* Contrôle de l'écran déverrouillé d'un appareil Android, par adb.
   Vidéo : flux H.264 de scrcpy (route .../adb/stream) décodé par WebCodecs dans un <canvas> (basse latence, décodage
   matériel) ; repli automatique sur le flux MJPEG (.../adb/screen) dans un <img> si WebCodecs manque ou échoue.
   Gestes : tap/swipe/touches/texte injectés par .../adb/input. L'écran verrouillé reste invisible (surface sécurisée). */
(function () {
  "use strict";
  var dlg = document.getElementById("adb-screen");
  if (!dlg) return;
  var D = dlg.dataset;
  var img = dlg.querySelector("[data-adb-screen-img]");
  var canvas = dlg.querySelector("[data-adb-screen-canvas]");
  var wrap = dlg.querySelector("[data-adb-screen-wrap]");
  var msg = dlg.querySelector("[data-adb-screen-msg]");
  var loading = msg ? msg.textContent : "";
  var hasWebCodecs = ("VideoDecoder" in window) && ("EncodedVideoChunk" in window);
  var display = img;                                   // élément réellement affiché (canvas en WebCodecs, sinon img)

  function form(obj) {
    var f = new FormData();
    f.append("csrf", D.csrf);
    for (var k in obj) if (Object.prototype.hasOwnProperty.call(obj, k)) f.append(k, obj[k]);
    return f;
  }
  function send(obj) { return fetch(D.urlInput, { method: "POST", headers: { "X-Requested-With": "fetch" }, body: form(obj) }).catch(function () {}); }
  function show(t) { if (msg) { msg.textContent = t; msg.style.display = t ? "" : "none"; } }

  // --- flux vidéo : WebCodecs (H.264 scrcpy) puis repli MJPEG --------------
  var abort = null, decoder = null;
  function h2(n) { return (n < 16 ? "0" : "") + n.toString(16); }
  function codecOf(au) {                               // chaîne « avc1.PPCCLL » lue dans le SPS (NAL 7) de l'unité
    for (var i = 0; i + 6 < au.length; i++) {
      if (au[i] === 0 && au[i + 1] === 0 && au[i + 2] === 1 && (au[i + 3] & 31) === 7) return "avc1." + h2(au[i + 4]) + h2(au[i + 5]) + h2(au[i + 6]);
      if (au[i] === 0 && au[i + 1] === 0 && au[i + 2] === 0 && au[i + 3] === 1 && (au[i + 4] & 31) === 7) return "avc1." + h2(au[i + 5]) + h2(au[i + 6]) + h2(au[i + 7]);
    }
    return "avc1.42e01e";
  }
  function stopVideo() {
    if (abort) { try { abort.abort(); } catch (e) {} abort = null; }
    if (decoder) { try { decoder.close(); } catch (e) {} decoder = null; }
  }
  function toMjpeg() {
    stopVideo();
    display = img; canvas.hidden = true; img.hidden = false; show(loading);
    img.src = D.urlScreen + "?t=" + Date.now();
  }
  function startWebCodecs() {
    display = canvas; canvas.hidden = false; img.hidden = true; show(loading);
    var ctx = canvas.getContext("2d"), configured = false, ts = 0, failed = false;
    function fail() { if (!failed) { failed = true; toMjpeg(); } }
    decoder = new VideoDecoder({
      output: function (frame) {
        if (canvas.width !== frame.displayWidth) { canvas.width = frame.displayWidth; canvas.height = frame.displayHeight; }
        ctx.drawImage(frame, 0, 0); frame.close(); show("");
      },
      error: fail
    });
    abort = new AbortController();
    fetch(D.urlStream, { signal: abort.signal }).then(function (resp) {
      if (!resp.ok || !resp.body) { fail(); return; }
      var reader = resp.body.getReader(), buf = new Uint8Array(0);
      function append(a, b) { var t = new Uint8Array(a.length + b.length); t.set(a); t.set(b, a.length); return t; }
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) return;
          buf = append(buf, r.value);
          while (buf.length >= 5) {
            var len = (buf[1] << 24) | (buf[2] << 16) | (buf[3] << 8) | buf[4];
            if (buf.length < 5 + len) break;
            var kf = buf[0] === 1, au = buf.slice(5, 5 + len);
            buf = buf.slice(5 + len);
            if (!configured) {
              if (!kf) continue;                        // commencer sur une image clé (SPS/PPS inclus)
              try { decoder.configure({ codec: codecOf(au), optimizeForLatency: true }); } catch (e) { fail(); return; }
              configured = true;
            }
            try { decoder.decode(new EncodedVideoChunk({ type: kf ? "key" : "delta", timestamp: ts, data: au })); } catch (e) {}
            ts += 16666;
          }
          return pump();
        });
      }
      return pump();
    }).catch(function (e) { if (!e || e.name !== "AbortError") fail(); });
  }

  img.addEventListener("load", function () { show(""); });
  img.addEventListener("error", function () { if (img.getAttribute("src")) show(D.msgErr); });

  function start() { if (hasWebCodecs) startWebCodecs(); else toMjpeg(); }
  function stop() { stopVideo(); img.removeAttribute("src"); canvas.hidden = true; img.hidden = true; }
  document.querySelectorAll('[data-open="#adb-screen"]').forEach(function (b) { b.addEventListener("click", function () { setTimeout(start, 30); }); });
  dlg.addEventListener("close", stop);

  // --- gestes : clic = tap, glisser = swipe (fractions de l'écran affiché) -
  var down = null;
  function frac(ev) {
    var r = display.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)),
             y: Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height)), t: Date.now() };
  }
  wrap.addEventListener("pointerdown", function (ev) { ev.preventDefault(); down = frac(ev); });
  document.addEventListener("pointerup", function (ev) {
    if (!down) return;
    var up = frac(ev);
    if (Math.hypot(up.x - down.x, up.y - down.y) < 0.02) send({ action: "tap", x: down.x, y: down.y });
    else send({ action: "swipe", x: down.x, y: down.y, x2: up.x, y2: up.y, ms: Math.min(800, Math.max(60, up.t - down.t)) });
    down = null;
  });

  // --- barre : navigation et texte ----------------------------------------
  dlg.querySelectorAll("[data-key]").forEach(function (b) { b.addEventListener("click", function () { send({ action: "key", key: b.dataset.key }); }); });
  var text = dlg.querySelector("[data-adb-screen-text]");
  function sendText() { if (text.value) { send({ action: "text", text: text.value }); text.value = ""; } }
  dlg.querySelector("[data-adb-screen-send]").addEventListener("click", sendText);
  text.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); sendText(); } });
})();
