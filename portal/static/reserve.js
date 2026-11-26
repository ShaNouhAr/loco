/* Poste réservé par un autre compte : avant d'ouvrir son terminal, son bureau à distance, ses fichiers ou son écran
   adb, le mot de passe choisi par son propriétaire ; l'ouverture demandée est rejouée une fois l'accès ouvert. Sans ce
   script, le serveur montre la même demande sur une page à part. */
(function () {
  "use strict";
  var state = document.getElementById("reservation-state");
  var dialog = document.getElementById("device-unlock");
  if (!state || !dialog) return;
  var form = dialog.querySelector("[data-unlock-form]");
  var error = dialog.querySelector("[data-unlock-error]");
  var tr = window.t || function (s) { return s; };
  var pending = null;
  var locked = function () { return state.dataset.locked === "1"; };

  function ask(next) {
    pending = next;
    error.hidden = true;
    form.reset();
    dialog.showModal();
    form.querySelector("input[type=password]").focus();
  }
  // Formulaires d'ouverture (terminal, bureau, console, terminal adb) : retenus tant que le mot de passe manque.
  document.addEventListener("submit", function (e) {
    if (!locked() || !e.target.matches("form[data-protected]")) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    ask({ form: e.target, submitter: e.submitter || null });
  }, true);
  // Boutons qui ouvrent une fenêtre protégée (écran adb).
  document.addEventListener("click", function (e) {
    var el = e.target.closest && e.target.closest("[data-protected-open]");
    if (!el || !locked()) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    ask({ click: el });
  }, true);
  // Gestionnaire de fichiers refusé : il demande le mot de passe par cet évènement.
  document.addEventListener("loco:unlock", function () { ask(null); });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var button = form.querySelector("button.primary");
    button.disabled = true;
    fetch(form.action, { method: "POST", body: new URLSearchParams(new FormData(form)), credentials: "same-origin",
                         headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        button.disabled = false;
        if (!res.ok) {
          error.textContent = res.d.error || tr("Mot de passe refusé.");
          error.hidden = false;
          form.querySelector("input[type=password]").select();
          return;
        }
        state.dataset.locked = "0";
        dialog.close();
        var note = document.querySelector(".reserve-note span");
        if (note && res.d.until_text) note.textContent = tr("Accès ouvert par le mot de passe jusqu'à %(time)s.", { time: res.d.until_text });
        var next = pending;
        pending = null;
        document.dispatchEvent(new CustomEvent("loco:unlocked"));
        if (next && next.form) next.form.requestSubmit(next.submitter || undefined);
        else if (next && next.click) next.click.click();
      })
      .catch(function () {
        button.disabled = false;
        error.textContent = tr("Connexion au serveur interrompue.");
        error.hidden = false;
      });
  });
})();
