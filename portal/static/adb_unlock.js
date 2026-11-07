/* Déverrouillage d'un appareil Android depuis sa fiche : pavé de schéma, PIN, mot de passe, réveil et calage de la
   grille. Appelle les routes /fleet/<id>/adb/{wake,unlock,calibrate} (JSON). L'écran verrouillé est une surface
   sécurisée : on ne l'affiche pas, la personne saisit elle-même le secret et loco l'injecte par adb. */
(function () {
  "use strict";
  var dlg = document.getElementById("adb-unlock");
  if (!dlg) return;
  var D = dlg.dataset;
  var statusEl = dlg.querySelector("[data-adb-status]");
  var pad = dlg.querySelector("[data-adb-pad]");
  var trace = dlg.querySelector("[data-adb-trace]");
  var sequence = [];

  function form(obj) {
    var f = new FormData();
    f.append("csrf", D.csrf);
    for (var k in obj) if (Object.prototype.hasOwnProperty.call(obj, k)) f.append(k, obj[k]);
    return f;
  }

  function post(url, body, btn) {
    if (btn) btn.classList.add("loading");
    return fetch(url, { method: "POST", headers: { "X-Requested-With": "fetch" }, body: body })
      .then(function (r) { return r.json().then(function (j) { return j; }, function () { return {}; }); })
      .catch(function () { return { error: " " }; })
      .then(function (res) { if (btn) btn.classList.remove("loading"); return res; });
  }

  function say(msg, kind) {
    statusEl.textContent = msg || "";
    statusEl.className = msg ? ("small st " + (kind || "")) : "small muted";
  }
  function done(res, lockedMsg) {
    if (res.error) say(res.error, "err");
    else if (res.locked) say(lockedMsg || D.msgLocked, "err");
    else say(D.msgUnlocked, "ok");
  }

  // Méthodes (onglets) ------------------------------------------------------
  dlg.querySelectorAll("[data-adb-method]").forEach(function (b) {
    b.addEventListener("click", function () {
      dlg.querySelectorAll("[data-adb-method]").forEach(function (x) { x.classList.toggle("on", x === b); });
      var m = b.dataset.adbMethod;
      dlg.querySelectorAll("[data-adb-pane]").forEach(function (p) { p.hidden = p.dataset.adbPane !== m; });
      say("");
    });
  });

  // Pavé de schéma ----------------------------------------------------------
  var DOT = { 1: [50, 50], 2: [150, 50], 3: [250, 50], 4: [50, 150], 5: [150, 150], 6: [250, 150],
              7: [50, 250], 8: [150, 250], 9: [250, 250] };
  function redraw() {
    if (trace) trace.setAttribute("points", sequence.map(function (n) { return DOT[n].join(","); }).join(" "));
    pad.querySelectorAll(".pattern-dot").forEach(function (d) {
      d.classList.toggle("on", sequence.indexOf(+d.dataset.dot) >= 0);
    });
  }
  function addDot(n) { if (n && sequence.indexOf(n) < 0) { sequence.push(n); redraw(); } }
  function dotAt(ev) {
    var el = document.elementFromPoint(ev.clientX, ev.clientY);
    var dot = el && el.closest ? el.closest(".pattern-dot") : null;
    return dot ? +dot.dataset.dot : 0;
  }
  var drawing = false;
  pad.addEventListener("pointerdown", function (ev) { ev.preventDefault(); drawing = true; say(""); addDot(dotAt(ev)); });
  pad.addEventListener("pointermove", function (ev) { if (drawing) addDot(dotAt(ev)); });
  document.addEventListener("pointerup", function () { drawing = false; });
  dlg.querySelector("[data-adb-clear]").addEventListener("click", function () { sequence = []; redraw(); say(""); });

  // Réveil ------------------------------------------------------------------
  dlg.querySelector("[data-adb-wake]").addEventListener("click", function () {
    say(D.msgSending);
    post(D.urlWake, form({}), this).then(function (res) {
      if (res.error) say(res.error, "err"); else say(res.locked ? D.msgWake : D.msgUnlocked, "ok");
    });
  });

  // Déverrouillage ----------------------------------------------------------
  function unlock(btn) {
    var method = btn.dataset.adbUnlock, body = { method: method };
    if (method === "pattern") {
      if (sequence.length < 2) { say(D.msgDraw, "err"); return; }
      body.pattern = sequence.join(",");
    } else {
      var inp = dlg.querySelector('[data-adb-code="' + method + '"]');
      if (!inp.value) { say(D.msgEmpty, "err"); return; }
      body.code = inp.value;
    }
    say(D.msgSending);
    post(D.urlUnlock, form(body), btn).then(function (res) { done(res); });
  }
  dlg.querySelectorAll("[data-adb-unlock]").forEach(function (b) {
    b.addEventListener("click", function () { unlock(b); });
  });

  // Calage de la grille -----------------------------------------------------
  function setGeo(g) {
    ["cx", "cy", "dx", "dy"].forEach(function (k) {
      var inp = dlg.querySelector('[data-geo="' + k + '"]');
      if (inp && g[k] != null) inp.value = (+g[k]).toFixed(3);
    });
  }
  var preset = dlg.querySelector("[data-adb-preset]");
  if (preset) preset.addEventListener("change", function () {
    var v = preset.value.split(",");
    setGeo({ cx: v[0], cy: v[1], dx: v[2], dy: v[3] });
  });
  var saveBtn = dlg.querySelector("[data-adb-geo-save]");
  if (saveBtn) saveBtn.addEventListener("click", function () {
    var body = {};
    dlg.querySelectorAll("[data-geo]").forEach(function (i) { body[i.dataset.geo] = i.value; });
    say(D.msgSending);
    post(D.urlCalibrate, form(body), saveBtn).then(function (res) {
      say(res.error || D.msgSaved, res.error ? "err" : "ok");
    });
  });
  var resetBtn = dlg.querySelector("[data-adb-geo-reset]");
  if (resetBtn) resetBtn.addEventListener("click", function () {
    say(D.msgSending);
    post(D.urlCalibrate, form({ reset: "1" }), resetBtn).then(function (res) {
      if (res.geometry) setGeo(res.geometry);
      say(res.error || D.msgSaved, res.error ? "err" : "ok");
    });
  });

  redraw();
})();
