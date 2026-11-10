/* Clés d'accès (passkeys, WebAuthn) : connexion sans mot de passe depuis la page de connexion, et ajout d'une clé dans
   Mon compte. Le navigateur dialogue avec l'appareil ; ce script ne fait que traduire entre le JSON de loco et les
   tampons d'octets de l'API, puis renvoie la réponse au serveur, qui la vérifie. Rien n'est affiché si le navigateur
   ne propose pas les clés d'accès (page non sécurisée, certificat non reconnu, navigateur ancien). */
(function () {
  "use strict";
  if (!window.PublicKeyCredential || !navigator.credentials) {
    var note = document.querySelector("[data-passkey-unsupported]");
    if (note) note.hidden = false;
    return;
  }

  function bytes(text) {
    var raw = atob(text.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((text.length + 3) % 4));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out.buffer;
  }
  function text(buffer) {
    var view = new Uint8Array(buffer), raw = "";
    for (var i = 0; i < view.length; i++) raw += String.fromCharCode(view[i]);
    return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function post(url, fields) {
    var body = new FormData();
    Object.keys(fields).forEach(function (k) { body.append(k, fields[k]); });
    return fetch(url, { method: "POST", headers: { "X-Requested-With": "fetch" }, body: body, credentials: "same-origin" })
      .then(function (r) { return r.json().then(function (j) { return j; }, function () { return { error: "" }; }); });
  }

  function show(el, message) {
    if (!el) return;
    el.textContent = message || "";
    el.hidden = !message;
  }

  // Vers la page indiquée par le serveur ; si seule l'ancre change (Mon compte), la page est rechargée pour de bon.
  function go(url) {
    var target = new URL(url, window.location.href);
    if (target.hash && target.pathname === window.location.pathname && target.search === window.location.search) {
      window.location.hash = target.hash;
      window.location.reload();
    } else {
      window.location.assign(target.href);
    }
  }

  // Une demande à la fois ; l'abandon par la personne (fenêtre fermée) ne laisse aucun message d'erreur technique.
  function run(button, error, failed, steps) {
    if (button.classList.contains("loading")) return;
    button.classList.add("loading");
    show(error, "");
    steps().then(function (answer) {
      if (answer && answer.redirect) { go(answer.redirect); return; }
      button.classList.remove("loading");
      show(error, (answer && answer.error) || failed);
    }).catch(function (e) {
      button.classList.remove("loading");
      show(error, e && e.name === "NotAllowedError" ? "" : failed);
    });
  }

  // --- Connexion ---------------------------------------------------------------------------------------------------
  var login = document.querySelector("[data-passkey-login]");
  if (login) {
    document.querySelectorAll("[data-passkey-shown]").forEach(function (el) { el.hidden = false; });
    var loginError = document.querySelector("[data-passkey-error]");
    login.addEventListener("click", function () {
      var d = login.dataset;
      run(login, loginError, d.failed, function () {
        return post(d.options, { csrf: d.csrf }).then(function (o) {
          if (!o.challenge) return o;
          return navigator.credentials.get({ publicKey: { challenge: bytes(o.challenge), rpId: o.rpId, timeout: o.timeout,
                                                          userVerification: o.userVerification, allowCredentials: [] } })
            .then(function (c) {
              var r = c.response;
              return post(d.verify, { csrf: d.csrf, next: d.next, response: JSON.stringify({ id: text(c.rawId), response: {
                clientDataJSON: text(r.clientDataJSON), authenticatorData: text(r.authenticatorData), signature: text(r.signature),
                userHandle: r.userHandle ? text(r.userHandle) : "" } }) });
            });
        });
      });
    });
  }

  // --- Mon compte : ajouter une clé ----------------------------------------------------------------------------
  var add = document.querySelector("[data-passkey-add]");
  if (add) {
    add.hidden = false;
    var addError = add.querySelector("[data-passkey-error]");
    add.addEventListener("submit", function (event) {
      event.preventDefault();
      var d = add.dataset, button = add.querySelector("button"), csrf = add.elements.csrf.value;
      run(button, addError, d.failed, function () {
        return post(d.options, { csrf: csrf, password: add.elements.password.value }).then(function (o) {
          if (!o.challenge) return o;
          o.challenge = bytes(o.challenge);
          o.user.id = bytes(o.user.id);
          o.excludeCredentials = (o.excludeCredentials || []).map(function (c) { return { type: c.type, id: bytes(c.id) }; });
          return navigator.credentials.create({ publicKey: o }).then(function (c) {
            var r = c.response;
            return post(d.verify, { csrf: csrf, name: add.elements.name.value, response: JSON.stringify({ id: text(c.rawId), response: {
              clientDataJSON: text(r.clientDataJSON), attestationObject: text(r.attestationObject),
              transports: r.getTransports ? r.getTransports() : [] } }) });
          });
        });
      });
    });
  }
})();
