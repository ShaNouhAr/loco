/* loco — interactions du portail, sans dépendance. */
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  // Traductions : t(texte) renvoie la version de la langue de la page (textes fournis par le portail).
  var I18N = {};
  try { I18N = JSON.parse(($("#i18n-js") || {}).textContent || "{}"); } catch (e) { I18N = {}; }
  var t = function (text, values) {
    var out = I18N[text] || text;
    if (values) Object.keys(values).forEach(function (k) { out = out.split("%(" + k + ")s").join(values[k]); });
    return out;
  };
  window.t = t;

  function appLogo(img) {
    if (img.matches && img.matches("img[data-app-logo]")) {
      img.parentNode.classList.toggle("has-logo", img.complete && img.naturalWidth > 0);
    }
  }
  document.addEventListener("load", function (e) { appLogo(e.target); }, true);
  document.addEventListener("error", function (e) { appLogo(e.target); }, true);
  $$("img[data-app-logo]").forEach(appLogo);

  /* ---------------------------------------------------------------- navigation */

  var navGroups = $$(".nav-group");
  function openNav(group, open) {
    if (open) navGroups.forEach(function (other) { if (other !== group) openNav(other, false); });
    group.open = open;
    $("summary", group).setAttribute("aria-expanded", String(open));
  }
  navGroups.forEach(function (group) {
    var summary = $("summary", group);
    summary.addEventListener("click", function (e) {
      e.preventDefault();
      openNav(group, !group.open);
    });
    group.addEventListener("pointerenter", function (e) {
      if (e.pointerType === "mouse") openNav(group, true);
    });
    group.addEventListener("pointerleave", function (e) {
      if (e.pointerType === "mouse" && !group.contains(document.activeElement)) openNav(group, false);
    });
    group.addEventListener("focusout", function () {
      setTimeout(function () { if (!group.contains(document.activeElement)) openNav(group, false); }, 0);
    });
    group.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && group.open) {
        e.preventDefault();
        openNav(group, false);
        summary.focus();
      } else if (e.key === "ArrowDown" && e.target === summary) {
        e.preventDefault();
        openNav(group, true);
        $(".nav-panel a", group).focus();
      }
    });
    group.addEventListener("toggle", function () { summary.setAttribute("aria-expanded", String(group.open)); });
  });
  document.addEventListener("pointerdown", function (e) {
    navGroups.forEach(function (group) { if (!group.contains(e.target)) openNav(group, false); });
  });

  /* ---------------------------------------------------------------- formats */

  var LANG = document.documentElement.lang || "fr";
  var UNITS = LANG === "en" ? ["B", "KB", "MB", "GB", "TB"] : ["o", "Ko", "Mo", "Go", "To"];
  function bytes(n) {
    n = Number(n) || 0;
    var i = 0;
    while (n >= 1024 && i < UNITS.length - 1) { n /= 1024; i++; }
    var num = n.toFixed(n < 10 ? 2 : 1);
    return (i === 0 ? String(Math.round(n)) : LANG === "en" ? num : num.replace(".", ",")) + " " + UNITS[i];
  }
  window.lrBytes = bytes;

  function get(obj, path) {
    return path.split(".").reduce(function (o, k) { return o == null ? undefined : o[k]; }, obj);
  }

  // Met à jour les éléments [data-bind] à partir d'un objet JSON (actualisation en direct).
  function bind(data) {
    $$("[data-bind]").forEach(function (el) {
      var v = get(data, el.dataset.bind);
      if (v === undefined) return;
      switch (el.dataset.fmt) {
        case "bytes": el.textContent = bytes(v); break;
        case "pct": el.textContent = Math.round(v) + " %"; break;
        case "meter": el.style.width = Math.max(0, Math.min(100, v)) + "%"; break;
        case "service":
          el.className = "st " + (v ? "ok" : "err");
          el.textContent = v ? t("actif") : t("arrêté");
          break;
        default: el.textContent = typeof v === "number" ? v.toLocaleString(LANG === "en" ? "en-US" : "fr-FR") : v;
      }
    });
  }
  window.lrBind = bind;

  /* ---------------------------------------------------------------- thème */

  /* Apparence : thème, accent, transparence et fond. Connecté → enregistré sur le compte (POST, dé-rebondi) ;
     déconnecté (page de connexion) → mémorisé dans le navigateur. Le menu rapide et la fenêtre « Apparence »
     partagent ce contrôleur ; theme.js a déjà appliqué l'apparence avant l'affichage. */
  (function () {
    var root = document.documentElement;
    var ap = $("#appearance-dialog");
    var isUser = function () { return root.dataset.appearance === "user"; };
    var alphaPct = function (a) { return Math.max(0, Math.min(100, (a - 55) / 45 * 100)); };

    function applyAlpha(a) {
      a = parseInt(a, 10) || 100;
      if (a >= 55 && a < 100) { root.dataset.alpha = a; root.style.setProperty("--surface-alpha", (a / 100).toFixed(2)); }
      else { delete root.dataset.alpha; root.style.removeProperty("--surface-alpha"); }
    }
    function current() {
      return { theme: root.dataset.theme || "oled", accent: root.dataset.accent || "neutral",
               alpha: parseInt(root.dataset.alpha, 10) || 100, bg: root.dataset.bg || "" };
    }
    function setBg(v) { if (v) root.dataset.bg = v; else delete root.dataset.bg; }

    function sync() {
      var c = current();
      $$("[data-set-theme]").forEach(function (b) { b.setAttribute("aria-checked", b.dataset.setTheme === c.theme); });
      $$("[data-set-accent]").forEach(function (b) { b.setAttribute("aria-checked", b.dataset.setAccent === c.accent); });
      $$("[data-set-bg]").forEach(function (b) { b.setAttribute("aria-checked", b.dataset.setBg === c.bg); });
      $$("[data-preset]").forEach(function (b) {
        b.setAttribute("aria-checked", b.dataset.theme === c.theme && b.dataset.accent === c.accent &&
          (parseInt(b.dataset.alpha, 10) || 100) === c.alpha && (b.dataset.bg || "") === c.bg);
      });
      if (ap) {
        var range = ap.querySelector("[data-set-alpha]");
        if (range) { range.value = c.alpha; range.style.setProperty("--range-pct", alphaPct(c.alpha).toFixed(0) + "%"); }
        var lbl = ap.querySelector("[data-alpha-val]"); if (lbl) lbl.textContent = c.alpha + " %";
      }
    }

    var saveTimer = null;
    function persist() {
      var c = current();
      if (isUser()) {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(function () {
          var fd = new FormData();
          fd.append("csrf", ap ? ap.dataset.csrf : "");
          fd.append("theme", c.theme); fd.append("accent", c.accent);
          fd.append("alpha", c.alpha); fd.append("bg", c.bg);
          fetch("/account/appearance", { method: "POST", body: fd, credentials: "same-origin" }).catch(function () {});
        }, 250);
      } else {
        try {
          localStorage.setItem("lr-theme", c.theme); localStorage.setItem("lr-accent", c.accent);
          localStorage.setItem("lr-alpha", String(c.alpha)); localStorage.setItem("lr-bg", c.bg);
        } catch (err) { /* navigation privée */ }
      }
    }

    function apply(c, save) {
      if (c.theme) root.dataset.theme = c.theme;
      if (c.accent) root.dataset.accent = c.accent;
      if ("bg" in c) setBg(c.bg);
      if ("alpha" in c) applyAlpha(c.alpha);
      sync();
      if (save !== false) persist();
    }
    sync();

    function mine(show) {
      $$("[data-bg-mine]").forEach(function (b) { b.classList.toggle("hidden", !show); });
      $$("[data-bg-remove]").forEach(function (b) { b.classList.toggle("hidden", !show); });
    }
    function bust() {
      var u = "url(\"/appearance/background.img?t=" + Date.now() + "\")";
      $$(".bg-layer, .ap-bg-mine").forEach(function (el) { el.style.backgroundImage = u; });
      var prev = ap && ap.querySelector('.ap-preview[data-bg="u"]'); if (prev) prev.style.backgroundImage = u;
    }
    function removeBg() {
      var fd = new FormData(); fd.append("csrf", ap ? ap.dataset.csrf : "");
      fetch("/account/appearance/background/remove", { method: "POST", body: fd, credentials: "same-origin" }).catch(function () {});
      mine(false);
      $$(".bg-layer, .ap-bg-mine").forEach(function (el) { el.style.backgroundImage = ""; });
      if (current().bg === "u") apply({ bg: "" }); else sync();
    }

    document.addEventListener("click", function (e) {
      var el = e.target.closest("[data-set-theme],[data-set-accent],[data-set-bg],[data-preset],[data-reset],[data-bg-remove],[data-open-appearance]");
      if (!el) return;
      if (el.hasAttribute("data-open-appearance")) {
        e.preventDefault();
        var menu = el.closest("details.menu"); if (menu) menu.removeAttribute("open");
        if (ap) { ap.showModal(); sync(); }
        return;
      }
      if (el.dataset.setTheme) apply({ theme: el.dataset.setTheme });
      else if (el.dataset.setAccent) apply({ accent: el.dataset.setAccent });
      else if (el.hasAttribute("data-set-bg")) {
        var nb = el.dataset.setBg, patch = { bg: nb };
        // Choisir une photo alors que les panneaux sont opaques : on rend le fond visible (transparence douce).
        if ((nb.indexOf("w:") === 0 || nb === "u") && current().alpha === 100) patch.alpha = 85;
        apply(patch);
      }
      else if (el.hasAttribute("data-preset")) apply({ theme: el.dataset.theme, accent: el.dataset.accent, alpha: parseInt(el.dataset.alpha, 10) || 100, bg: el.dataset.bg || "" });
      else if (el.hasAttribute("data-reset")) {
        var def = { theme: "oled", accent: "neutral", alpha: 100, bg: "" };
        try { if (ap && ap.dataset.default) def = JSON.parse(ap.dataset.default); } catch (e) { /* défaut de repli */ }
        apply(def);
      }
      else if (el.hasAttribute("data-bg-remove")) removeBg();
    });

    if (ap) {
      var range = ap.querySelector("[data-set-alpha]");
      if (range) range.addEventListener("input", function () {
        applyAlpha(this.value);
        var lbl = ap.querySelector("[data-alpha-val]"); if (lbl) lbl.textContent = this.value + " %";
        this.style.setProperty("--range-pct", alphaPct(this.value).toFixed(0) + "%");
        persist();
      });
      var upload = ap.querySelector("[data-bg-upload]"), msg = ap.querySelector("[data-bg-msg]");
      if (upload) upload.addEventListener("change", function () {
        var file = this.files && this.files[0]; this.value = ""; if (!file) return;
        if (msg) { msg.classList.remove("err"); msg.textContent = t("Envoi…"); }
        var fd = new FormData(); fd.append("csrf", ap.dataset.csrf); fd.append("background", file);
        fetch("/account/appearance/background", { method: "POST", body: fd, credentials: "same-origin" })
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }, function () { return { ok: r.ok, j: {} }; }); })
          .then(function (res) {
            if (!res.ok) {
              if (msg) { msg.classList.add("err"); msg.textContent = res.j.error === "trop-grand" ? t("Image trop lourde (8 Mo maximum).") : t("Format d'image non reconnu (PNG, JPEG, GIF ou WebP)."); }
              return;
            }
            bust(); mine(true);
            apply({ bg: "u", alpha: current().alpha === 100 ? 85 : current().alpha });
            if (msg) msg.textContent = "";
          })
          .catch(function () { if (msg) { msg.classList.add("err"); msg.textContent = t("Échec de l'envoi."); } });
      });

      // Logo global de l'instance (admin) : l'envoi change le logo pour tout le monde -> on recharge pour l'appliquer partout.
      var logoUp = ap.querySelector("[data-logo-upload]"), logoMsg = ap.querySelector("[data-logo-msg]");
      if (logoUp) logoUp.addEventListener("change", function () {
        var file = this.files && this.files[0]; this.value = ""; if (!file) return;
        if (logoMsg) { logoMsg.classList.remove("err"); logoMsg.textContent = t("Envoi…"); }
        var fd = new FormData(); fd.append("csrf", ap.dataset.csrf); fd.append("logo", file);
        fetch("/appearance/logo", { method: "POST", body: fd, credentials: "same-origin" })
          .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }, function () { return { ok: r.ok, j: {} }; }); })
          .then(function (res) {
            if (!res.ok) {
              if (logoMsg) { logoMsg.classList.add("err"); logoMsg.textContent = res.j.error === "trop-grand" ? t("Logo trop lourd (2 Mo maximum).") : t("Format non reconnu (PNG, JPEG, GIF, WebP ou SVG)."); }
              return;
            }
            location.reload();
          })
          .catch(function () { if (logoMsg) { logoMsg.classList.add("err"); logoMsg.textContent = t("Échec de l'envoi."); } });
      });
      var logoRm = ap.querySelector("[data-logo-remove]");
      if (logoRm) logoRm.addEventListener("click", function () {
        var fd = new FormData(); fd.append("csrf", ap.dataset.csrf);
        fetch("/appearance/logo/remove", { method: "POST", body: fd, credentials: "same-origin" })
          .then(function () { location.reload(); }).catch(function () {});
      });
    }
  })();

  /* ------------------------------------------------ avertissements qu'on peut fermer */
  // Reste masqué tant que le jeton ne change pas (ex. tant que le nombre d'échecs n'augmente pas).
  $$("[data-dismissible]").forEach(function (box) {
    var key = "lr-dismiss-" + box.dataset.dismissible, token = box.dataset.token || "1";
    try { if (localStorage.getItem(key) === token) { box.remove(); return; } } catch (err) { /* navigation privée */ }
    var btn = $("[data-dismiss]", box);
    if (btn) btn.addEventListener("click", function () {
      try { localStorage.setItem(key, token); } catch (err) { /* navigation privée */ }
      box.remove();
    });
  });

  /* ------------------------------------------------ test d'un canal d'alerte (page Alertes) */
  $$("[data-test-channel]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var form = btn.closest("form");
      var out = form.querySelector('[data-test-result="' + btn.dataset.testChannel + '"]');
      var data = new FormData(form);
      data.set("channel", btn.dataset.testChannel);
      btn.disabled = true;
      if (out) { out.textContent = ""; out.className = "test-result"; }
      fetch("/alerts/test", { method: "POST", body: data }).then(function (r) { return r.json(); })
        .then(function (j) { if (out) { out.textContent = j.message; out.className = "test-result " + (j.ok ? "ok" : "err"); } })
        .catch(function () { if (out) { out.textContent = t("Envoi impossible."); out.className = "test-result err"; } })
        .finally(function () { btn.disabled = false; });
    });
  });

  /* ------------------------------------------------ test de la destination hors-site (page Système) */
  $$("[data-test-offsite]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var form = btn.closest("form");
      var out = form.querySelector("[data-offsite-result]");
      var data = new FormData(form);
      btn.disabled = true;
      if (out) { out.textContent = ""; out.className = "test-result"; }
      fetch("/system/offsite/test", { method: "POST", body: data }).then(function (r) { return r.json(); })
        .then(function (j) { if (out) { out.textContent = j.message; out.className = "test-result " + (j.ok ? "ok" : "err"); } })
        .catch(function () { if (out) { out.textContent = t("Envoi impossible."); out.className = "test-result err"; } })
        .finally(function () { btn.disabled = false; });
    });
  });

  /* ---------------------------------------------------------------- notifications */

  function toast(msg, kind) {
    var box = $(".toasts");
    var el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    box.appendChild(el);
    var close = function () { el.classList.add("out"); setTimeout(function () { el.remove(); }, 200); };
    var timer = setTimeout(close, kind === "error" ? 8000 : 4000);
    el.addEventListener("click", function () { clearTimeout(timer); close(); });
  }
  window.lrToast = toast;
  $$("[data-flash]").forEach(function (el) { toast(el.dataset.flash, el.dataset.kind); });

  /* ---------------------------------------------------------------- copie */

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    var ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } finally { document.body.removeChild(ta); }
    return Promise.resolve();
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".copy");
    if (!btn) return;
    copyText($("code", btn.parentElement).textContent.trim()).then(function () {
      btn.classList.add("done"); btn.textContent = t("Copié");
      setTimeout(function () { btn.classList.remove("done"); btn.textContent = t("Copier"); }, 1500);
    });
  });

  /* ---------------------------------------------------------------- onglets */

  document.addEventListener("click", function (e) {
    var b = e.target.closest(".tabs [data-tab]");
    if (!b) return;
    var tabs = b.closest(".tabs");
    $$("[data-tab]", tabs).forEach(function (x) { x.setAttribute("aria-selected", x === b); });
    var scope = tabs.parentElement;
    $$("[data-panel]", scope).forEach(function (p) {
      var up = p.parentElement.closest("[data-panel]");
      if (up && scope.contains(up)) return;     // panneau d'un groupe d'onglets imbriqué : il garde son état
      p.classList.toggle("on", p.dataset.panel === b.dataset.tab);
    });
    if (b.dataset.cmd && tabs.dataset.target) $(tabs.dataset.target).textContent = b.dataset.cmd;
    // Onglet mémorisé dans l'adresse : conservé lors du rechargement automatique de la page.
    if (tabs.hasAttribute("data-hash")) history.replaceState(null, "", "#" + b.dataset.tab);
  });
  // Onglet désigné par l'adresse (#onglet) : au chargement et quand l'adresse change sur la même page.
  function tabFromHash() {
    $$(".tabs[data-hash]").forEach(function (tabs) {
      var wanted = location.hash.slice(1), b = wanted && $('[data-tab="' + wanted.replace(/[^a-z0-9-]/gi, "") + '"]', tabs);
      if (b && b.getAttribute("aria-selected") !== "true") b.click();
    });
  }
  tabFromHash();
  window.addEventListener("hashchange", tabFromHash);

  /* ---------------------------------------------------------------- filtres appliqués au choix */

  // Formulaire de filtres (GET) : l'adresse ne garde que les champs remplis (« ?compte=marie » et non « &q= »).
  function go(form) {
    var params = new URLSearchParams();
    new FormData(form).forEach(function (v, k) { if (v !== "") params.append(k, v); });
    var query = params.toString();
    location.href = (form.getAttribute("action") || location.pathname) + (query ? "?" + query : "");
  }

  document.addEventListener("change", function (e) {
    if (!e.target.matches("select[data-autosubmit], input[type=checkbox][data-autosubmit], input[type=file][data-autosubmit]")) return;
    if (e.target.type === "file" && !e.target.files.length) return;    // sélection annulée
    var form = e.target.form;
    if (form.dataset.confirm) {
      // Confirmation d'abord : l'interrupteur reprend son état, la page affichera le nouveau après validation.
      if (e.target.type === "checkbox") e.target.checked = !e.target.checked;
      form.requestSubmit();
    } else if (form.hasAttribute("data-clean")) go(form); else form.submit();
  });

  /* ---------------------------------------------------------------- menus « Plus » */

  document.addEventListener("click", function (e) {
    $$("details.menu[open]").forEach(function (m) {
      if (!m.contains(e.target)) m.open = false;
      // Après le traitement du clic (envoi du formulaire, confirmation) : le menu se referme.
      else if (e.target.closest(".menu-item")) setTimeout(function () { m.open = false; }, 0);
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") $$("details.menu[open]").forEach(function (m) { m.open = false; $("summary", m).focus(); });
  });

  /* ---------------------------------------------------------------- brancher un poste Linux */

  // Commande composée à partir des choix (système, ce que loco fournit, EPEL, autorité à importer).
  $$("[data-setup]").forEach(function (box) {
    if (!$("#cmd-setup")) return;      // composeur de la page Paquets uniquement (évite toute collision d'attribut)
    var FLAGS = { apt: { repos: "--no-proxy", cache: "--cache-only" }, rpm: { repos: "--no-cache", cache: "--no-local" } };
    var update = function () {
      var os = ($('input[name="setup_os"]:checked', box) || {}).value || "apt";
      var mode = ($('input[name="setup_mode"]:checked', box) || {}).value || "full";
      var epel = $('input[name="setup_epel"]', box), trust = $('input[name="setup_trust"]', box);
      var epelRow = $("[data-setup-epel]", box);
      if (epelRow) epelRow.hidden = os !== "rpm" || mode === "remove";
      var args = [];
      if (trust && trust.checked && mode !== "remove") args.push("--trust-ca");
      if (mode === "remove") args.push("--remove");
      else if (FLAGS[os][mode]) args.push(FLAGS[os][mode]);
      if (os === "rpm" && epel && epel.checked && mode !== "remove") args.push("--epel");
      var base = trust && trust.checked && mode !== "remove" && box.dataset.http ? box.dataset.http : box.dataset.https;
      $("#cmd-setup").textContent = "curl -fsSL " + base + (os === "rpm" ? "/rpm/setup.sh" : "/linux/setup.sh") +
        " | sudo sh" + (args.length ? " -s -- " + args.join(" ") : "");
      $$("[data-setup-hint]", box).forEach(function (h) { h.hidden = h.dataset.setupHint !== os; });
    };
    box.addEventListener("change", update);
    update();
  });

  /* ---------------------------------------------------------------- lignes cliquables */

  // Listes (parc, scripts, déploiements, groupes) : toute la ligne ouvre l'élément, sauf les commandes qu'elle contient.
  $$("table[data-row-links] tbody tr").forEach(function (row) {
    var link = $("a.host, a.strong-link", row);
    if (!link) return;
    row.classList.add("row-link");
    row.addEventListener("click", function (e) {
      if (e.defaultPrevented || e.target.closest("a, button, input, select, textarea, label, summary, details, form")) return;
      if (window.getSelection && String(window.getSelection())) return;      // sélection de texte : ne pas naviguer
      if (e.ctrlKey || e.metaKey || e.button === 1) window.open(link.href, "_blank"); else link.click();
    });
  });

  /* ------------------------------------------------ sélection de postes + mise à jour groupée (Mon parc) */
  var selBar = $("[data-select-bar]");
  if (selBar) {
    var selTable = $("#fleet-table"), selAll = $("[data-select-all]");
    var selBoxes = function () { return $$("[data-select]", selTable); };
    var selChecked = function () { return selBoxes().filter(function (b) { return b.checked; }); };
    var selSync = function () {
      var boxes = selBoxes(), n = selChecked().length;
      selBar.hidden = n === 0;
      if (n) document.body.dataset.noReload = "1"; else delete document.body.dataset.noReload;   // ne pas rafraîchir pendant une sélection
      var c = $("[data-select-count]"); if (c) c.textContent = n;
      if (selAll) { selAll.checked = n > 0 && n === boxes.length; selAll.indeterminate = n > 0 && n < boxes.length; }
    };
    selTable.addEventListener("change", function (e) { if (e.target.matches("[data-select]")) selSync(); });
    if (selAll) selAll.addEventListener("change", function () { selBoxes().forEach(function (b) { b.checked = selAll.checked; }); selSync(); });
    var selClear = $("[data-select-clear]");
    if (selClear) selClear.addEventListener("click", function () { selBoxes().forEach(function (b) { b.checked = false; }); selSync(); });
    var selSubmit = $("[data-select-submit]");
    if (selSubmit) selSubmit.addEventListener("click", function () {
      var ids = selChecked().map(function (b) { return b.dataset.id; });
      if (!ids.length) return;
      confirmDialog(selSubmit.dataset.confirm, selSubmit.dataset.confirmText, selSubmit.dataset.confirmOk).then(function (ok) {
        if (!ok) return;
        var data = new FormData();
        data.set("csrf", selSubmit.dataset.csrf);
        ids.forEach(function (id) { data.append("ids", id); });
        var rb = $("[data-select-reboot]"); if (rb && rb.checked) data.set("reboot", "1");
        selSubmit.disabled = true; selSubmit.classList.add("loading");
        fetch(selSubmit.dataset.action, { method: "POST", body: data })
          .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
          .then(function () { delete document.body.dataset.noReload; window.location.reload(); })
          .catch(function () { selSubmit.disabled = false; selSubmit.classList.remove("loading"); toast(t("Action impossible."), "error"); });
      });
    });
    selSync();
  }

  /* ---------------------------------------------------------------- aller à une page (Ctrl+K) */

  var cmdk = $("#cmdk");
  if (cmdk) {
    var cmdkInput = $("input", cmdk);
    var cmdkList = $(".cmdk-list", cmdk);
    var cmdkPages = null, cmdkHits = [], cmdkCurrent = 0;
    var cmdkFold = function (text) { return text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase(); };
    var cmdkCollect = function () {
      var seen = {};
      cmdkPages = [];
      var add = function (a, group) {
        var href = a.getAttribute("href");
        if (!href || href.charAt(0) === "#" || seen[href]) return;
        seen[href] = true;
        var title = ($("strong", a) || a).textContent.replace(/\s+/g, " ").trim();
        var sub = $("small", a);
        cmdkPages.push({ href: href, title: title, sub: sub ? sub.textContent.trim() : group, key: cmdkFold(title + " " + group + " " + (sub ? sub.textContent : "")) });
      };
      $$(".nav > a").forEach(function (a) { add(a, "Navigation"); });
      $$(".nav-group").forEach(function (g) {
        var label = $(".nav-long", g) || $("summary span", g);
        $$(".nav-panel a", g).forEach(function (a) { add(a, label ? label.textContent : ""); });
      });
      var extra = $("#cmdk-links");
      if (extra) $$("a", extra.content).forEach(function (a) { add(a, a.dataset.group || ""); });
      $$(".userbox a").forEach(function (a) { add(a, "Mon compte"); });
    };
    var cmdkDevicesDone = false;
    var cmdkLoadDevices = function () {               // postes du parc, ajoutés à la palette une seule fois
      if (cmdkDevicesDone) return;
      cmdkDevicesDone = true;
      var url = cmdk.dataset.devices;
      if (!url || !cmdkPages) return;
      fetch(url, { headers: { "X-Requested-With": "fetch" } }).then(function (r) {
        return r.ok ? r.json() : null;
      }).then(function (data) {
        if (!data || !data.devices) return;
        var group = t("Mon parc");
        data.devices.forEach(function (d) {
          if (!d.href) return;
          cmdkPages.push({ href: d.href, title: d.title, sub: d.sub || group,
                           key: cmdkFold(d.title + " " + (d.sub || "") + " " + group) });
        });
        if (cmdk.open) cmdkRender();
      }).catch(function () {});
    };
    var cmdkRender = function () {
      var words = cmdkFold(cmdkInput.value).split(/\s+/).filter(Boolean);
      cmdkHits = cmdkPages.filter(function (p) { return words.every(function (w) { return p.key.indexOf(w) >= 0; }); });
      cmdkCurrent = Math.min(cmdkCurrent, Math.max(cmdkHits.length - 1, 0));
      cmdkList.textContent = "";
      cmdkHits.forEach(function (p, i) {
        var a = document.createElement("a");
        a.className = "cmdk-item" + (i === cmdkCurrent ? " on" : "");
        a.href = p.href;
        a.setAttribute("role", "option");
        a.setAttribute("aria-selected", String(i === cmdkCurrent));
        var strong = document.createElement("strong"); strong.textContent = p.title;
        var s = document.createElement("span"); s.textContent = p.sub;
        a.appendChild(strong); a.appendChild(s);
        a.addEventListener("mousemove", function () { if (cmdkCurrent !== i) { cmdkCurrent = i; cmdkRender(); } });
        cmdkList.appendChild(a);
      });
      if (!cmdkHits.length) {
        var none = document.createElement("div");
        none.className = "cmdk-empty";
        none.textContent = t("Aucune page ne correspond.");
        cmdkList.appendChild(none);
      }
      var on = $(".cmdk-item.on", cmdkList);
      if (on) on.scrollIntoView({ block: "nearest" });
    };
    var cmdkOpen = function () {
      if (cmdk.open) return;
      if (!cmdkPages) cmdkCollect();
      cmdkLoadDevices();
      cmdkInput.value = "";
      cmdkCurrent = 0;
      cmdkRender();
      cmdk.showModal();
      cmdkInput.focus();
    };
    $$("[data-cmdk]").forEach(function (b) { b.addEventListener("click", cmdkOpen); });
    cmdkInput.addEventListener("input", function () { cmdkCurrent = 0; cmdkRender(); });
    cmdkInput.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (cmdkHits.length) { cmdkCurrent = (cmdkCurrent + (e.key === "ArrowDown" ? 1 : cmdkHits.length - 1)) % cmdkHits.length; cmdkRender(); }
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (cmdkHits[cmdkCurrent]) window.location.href = cmdkHits[cmdkCurrent].href;
      }
    });
    cmdk.addEventListener("click", function (e) { if (e.target === cmdk) cmdk.close(); });   // clic hors de la fenêtre
    document.addEventListener("keydown", function (e) {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (cmdk.open) cmdk.close(); else cmdkOpen();
        return;
      }
      // « / » : champ de recherche de la page, sauf pendant une saisie.
      var typing = e.target.closest && e.target.closest("input, textarea, select, [contenteditable], .remote-display");
      if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey && !$("dialog[open]")) {
        var search = $$("main input[type=search]").filter(function (i) { return i.offsetParent !== null; })[0];
        if (search) { e.preventDefault(); search.focus(); search.select(); }
      }
    });
  }

  /* ---------------------------------------------------------------- confirmation */

  function confirmDialog(title, text, okLabel) {
    return new Promise(function (resolve) {
      var dlg = $("#confirm");
      $("h2", dlg).textContent = title;
      $(".dlg-b p", dlg).textContent = text || "";
      $("[data-ok]", dlg).textContent = okLabel || t("Confirmer");
      // Rouge réservé aux actions destructrices ou perturbantes ; les autres confirmations restent un bouton principal.
      var danger = /^(Supprimer|Retirer|Vider|Oublier|Abandonner|Annuler|Redémarrer|Réinitialiser|Révoquer|Delete|Remove|Empty|Forget|Abandon|Cancel|Restart|Reboot|Reset|Revoke|Vaciar|Eliminar|Quitar|Revocar)/.test(okLabel || "");
      $("[data-ok]", dlg).classList.toggle("danger", danger);
      $("[data-ok]", dlg).classList.toggle("primary", !danger);
      var answered = false;
      var done = function (v) { if (answered) return; answered = true; dlg.close(); resolve(v); };
      $("[data-ok]", dlg).onclick = function () { done(true); };
      $("[data-cancel]", dlg).onclick = function () { done(false); };
      dlg.onclose = function () { done(false); };
      dlg.showModal();
    });
  }
  window.lrConfirm = confirmDialog;

  /* ---------------------------------------------------------------- formulaires */

  function send(form, btn) {
    var prog = $(".progress", form);
    var hasFile = $$('input[type="file"]', form).some(function (i) { return i.files && i.files.length; });
    if (btn) btn.classList.add("loading");
    var xhr = new XMLHttpRequest();
    // A field named "action" shadows the form's action property.
    xhr.open("POST", form.getAttribute("action") || window.location.href);
    xhr.setRequestHeader("X-Requested-With", "fetch");
    xhr.responseType = "json";
    if (prog && hasFile) {
      prog.classList.add("on");
      xhr.upload.onprogress = function (ev) {
        if (!ev.lengthComputable) return;
        var p = Math.round(100 * ev.loaded / ev.total);
        $(".meter > i", prog).style.width = p + "%";
        $("[data-pct]", prog).textContent = p + " %";
        $("[data-size]", prog).textContent = bytes(ev.loaded) + " / " + bytes(ev.total);
      };
    }
    var fail = function (msg) { if (btn) btn.classList.remove("loading"); toast(msg, "error"); };
    xhr.onload = function () {
      var r = xhr.response;
      if (xhr.status < 400 && r && r.redirect) { window.location.href = r.redirect; return; }
      fail(xhr.status === 400 ? t("Session expirée : rechargez la page.") : t("Erreur %(status)s.", { status: xhr.status }));
    };
    xhr.onerror = function () { fail(t("Le serveur ne répond pas.")); };
    xhr.send(new FormData(form));
  }

  document.addEventListener("submit", function (e) {
    var form = e.target;
    var btn = e.submitter || $("button:not([type=button])", form);
    if (form.dataset.confirm && !form.dataset.confirmed) {
      e.preventDefault();
      confirmDialog(form.dataset.confirm, form.dataset.confirmText, form.dataset.confirmOk).then(function (ok) {
        if (!ok) return;
        form.dataset.confirmed = "1";
        if (form.hasAttribute("data-ajax")) send(form, btn); else form.submit();
      });
      return;
    }
    if (form.hasAttribute("data-ajax")) { e.preventDefault(); send(form, btn); }
    else if (form.hasAttribute("data-clean")) { e.preventDefault(); go(form); }
    else if (btn) btn.classList.add("loading");
  });

  /* ---------------------------------------------------------------- dépôt de fichiers */

  $$(".drop").forEach(function (drop) {
    var input = $('input[type="file"]', drop);
    var list = $(".files", drop);
    // Le champ natif (libellés dans la langue du navigateur) laisse place à une consigne et un bouton.
    var ui = document.createElement("div");
    var hint = document.createElement("span");
    var pick = document.createElement("button");
    ui.className = "drop-ui";
    hint.textContent = input.multiple ? t("Déposez les fichiers ici ou") : t("Déposez le fichier ici ou");
    pick.type = "button";
    pick.className = "btn sm";
    pick.textContent = t("Parcourir…");
    pick.addEventListener("click", function () { input.click(); });
    ui.appendChild(hint);
    ui.appendChild(pick);
    drop.insertBefore(ui, input);
    drop.classList.add("enhanced");
    var show = function () {
      list.textContent = Array.prototype.map.call(input.files, function (f) { return f.name + "  " + bytes(f.size); }).join("\n");
    };
    drop.addEventListener("dragover", function (e) { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", function () { drop.classList.remove("over"); });
    drop.addEventListener("drop", function (e) {
      e.preventDefault(); drop.classList.remove("over");
      if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); }
    });
    input.addEventListener("change", show);
  });

  /* ---------------------------------------------------------------- fenêtres */
  // Fenêtre à rouvrir au chargement (saisie refusée par le serveur : on la retrouve telle quelle).
  $$("dialog[data-open-on-load]").forEach(function (d) { d.showModal(); });

  document.addEventListener("click", function (e) {
    var open = e.target.closest("[data-open]");
    if (open) {
      var dlg = $(open.dataset.open);
      if (open.dataset.app) fillEdit(dlg, JSON.parse(open.dataset.app));
      if (open.dataset.fill) fillForm(dlg, JSON.parse(open.dataset.fill));
      dlg.showModal();
      var first = $("input[type=text], input[type=url]", dlg);
      if (first) first.focus();
      return;
    }
    var close = e.target.closest("[data-close]");
    if (close) close.closest("dialog").close();
  });

  // Fenêtre partagée par plusieurs lignes : champs et textes repris de data-fill (clé -> champ du formulaire ou [data-fill-text]).
  function fillForm(dlg, values) {
    var form = $("form", dlg);
    Object.keys(values).forEach(function (k) {
      if (form && form.elements[k]) form.elements[k].value = values[k];
      $$('[data-fill-text="' + k + '"]', dlg).forEach(function (el) { el.textContent = values[k]; });
    });
  }

  function fillEdit(dlg, app) {
    var form = $("form", dlg);
    form.setAttribute("action", form.dataset.base.replace("__slug__", app.slug));
    $("[data-title]", dlg).textContent = app.name;
    ["name", "version", "args", "url", "kind", "detect"].forEach(function (k) { if (form.elements[k]) form.elements[k].value = app[k] || ""; });
    $("[data-file]", dlg).textContent = app.filename ? app.filename + "  sha256:" + app.sha256.slice(0, 16) : "";
    $('input[type="file"]', form).value = "";
    $(".files", form).textContent = "";
    appFormState(form);
  }

  // Ajout / modification d'un logiciel : type déduit du lien ou du fichier, rappel des arguments silencieux d'un exe.
  var KINDS = { exe: "exe", msi: "msi", msu: "msu", ps1: "ps1" };
  function appFormState(form) {
    var el = form.elements;
    var file = el.file && el.file.files && el.file.files[0];
    var source = file ? file.name : (el.url && el.url.value) || "";
    var ext = (source.split(/[?#]/)[0].match(/\.([a-z0-9]+)$/i) || [])[1];
    ext = ext ? ext.toLowerCase() : "";
    var chosen = el.kind.value && el.kind.value !== "auto" ? el.kind.value : "";
    var kind = chosen || KINDS[ext] || (ext ? "file" : "");
    var badge = $("[data-kind-badge]", form);
    badge.textContent = "";
    if (kind && !chosen) {
      badge.appendChild(document.createTextNode(t("Type détecté :") + " "));
      var b = document.createElement("b"); b.textContent = kind; badge.appendChild(b);
      if (kind === "file") badge.appendChild(document.createTextNode(" " + t("(copié tel quel sur le poste)")));
    }
    var needsArgs = kind === "exe" && !el.args.value.trim();
    var warn = $("[data-args-warn]", form);
    if (warn) warn.hidden = !needsArgs;
    var adv = $("[data-adv]", form);
    if (adv && needsArgs && !adv.dataset.touched) adv.open = true;
    var summary = $("[data-adv-summary]", form);
    if (summary) summary.textContent = (chosen || t("type automatique")) + " · " +
      (el.args.value.trim() ? el.args.value.trim() : needsArgs ? t("arguments à renseigner") : t("arguments par défaut"));
  }
  $$("form[data-app-form]").forEach(function (form) {
    ["input", "change"].forEach(function (type) { form.addEventListener(type, function () { appFormState(form); }); });
    var adv = $("[data-adv]", form);
    if (adv) $("summary", adv).addEventListener("click", function () { adv.dataset.touched = "1"; });
    appFormState(form);
  });

  /* ---------------------------------------------------------------- filtre */

  $$("[data-filter]").forEach(function (input) {
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      var shown = 0;
      $$(input.dataset.filter).forEach(function (row) {
        var hit = !q || (row.dataset.search || row.textContent).toLowerCase().indexOf(q) >= 0;
        row.classList.toggle("hidden", !hit);
        var note = row.nextElementSibling;
        if (note && note.classList.contains("note-row")) note.classList.toggle("hidden", !hit);
        if (hit) shown++;
      });
      if (input.dataset.empty) $(input.dataset.empty).classList.toggle("hidden", shown > 0);
    });
  });

  /* ------------------------------------------------- paquets : recherche + pastilles */

  $$("[data-pkgs]").forEach(function (box) {
    var rows = $$(box.dataset.pkgs + " tbody tr");
    var search = $("input[type=search]", box);
    var chips = $$("[data-chip]", box);
    var empty = box.dataset.empty ? $(box.dataset.empty) : null;
    var mode = "all";
    var apply = function () {
      var q = (search ? search.value : "").trim().toLowerCase();
      var shown = 0;
      rows.forEach(function (row) {
        var okText = !q || (row.dataset.search || row.textContent).toLowerCase().indexOf(q) >= 0;
        var okChip = mode === "all" || !!row.dataset[mode];   // pastille « x » : lignes data-x
        var hit = okText && okChip;
        row.classList.toggle("hidden", !hit);
        if (hit) shown++;
      });
      if (empty) empty.classList.toggle("hidden", shown > 0);
    };
    if (search) search.addEventListener("input", apply);
    chips.forEach(function (chip) {
      chip.addEventListener("click", function () {
        mode = chip.dataset.chip;
        chips.forEach(function (c) {
          c.classList.toggle("on", c === chip);
          c.setAttribute("aria-pressed", String(c === chip));
        });
        apply();
      });
    });
  });

  /* ---------------------------------------------------------------- sélection des logiciels */

  var selbar = $(".selbar");
  if (selbar) {
    var base = selbar.dataset.base;
    var all = $("#pick-all");
    var boxes = function () { return $$("input.pick:not(:disabled)"); };
    var refresh = function () {
      var picked = boxes().filter(function (c) { return c.checked; });
      picked.forEach(function (c) { c.closest("tr").classList.add("sel"); });
      boxes().filter(function (c) { return !c.checked; }).forEach(function (c) { c.closest("tr").classList.remove("sel"); });
      var slugs = picked.map(function (c) { return c.value; });
      $(".count", selbar).textContent = slugs.length > 1 ? t("%(num)s sélectionnés", { num: slugs.length }) : t("%(num)s sélectionné", { num: slugs.length });
      $("code", selbar).textContent = "irm '" + base + slugs.join(",") + "' | iex";
      ["data-deploy-selected", "data-group-selected"].forEach(function (attribute) {
        var link = $("[" + attribute + "]", selbar);
        if (!link) return;
        if (!link.dataset.base) link.dataset.base = link.getAttribute("href");
        var destination = new URL(link.dataset.base, window.location.href);
        slugs.forEach(function (slug) { destination.searchParams.append("app", slug); });
        link.href = destination.href;
      });
      selbar.classList.toggle("on", slugs.length > 0);
      if (all) all.checked = slugs.length > 0 && slugs.length === boxes().length;
    };
    document.addEventListener("change", function (e) { if (e.target.classList.contains("pick")) refresh(); });
    if (all) all.addEventListener("change", function () { boxes().forEach(function (c) { if (!c.closest("tr").classList.contains("hidden")) c.checked = all.checked; }); refresh(); });
    $("[data-clear]", selbar).addEventListener("click", function () { boxes().forEach(function (c) { c.checked = false; }); refresh(); });
    refresh();
  }

  /* ---------------------------------------------------------------- modèles de formulaire */

  // Liste de modèles : l'option choisie remplit les champs du formulaire (data-fill = {nom du champ: valeur}).
  document.addEventListener("change", function (e) {
    if (!e.target.matches("select[data-preset]")) return;
    var option = e.target.selectedOptions[0], form = e.target.form;
    if (!option || !option.dataset.fill || !form) return;
    var values = JSON.parse(option.dataset.fill);
    Object.keys(values).forEach(function (name) { var field = form.elements[name]; if (field) field.value = values[name]; });
  });

  /* ---------------------------------------------------------------- mot de passe (Mon compte) */

  // Règles vérifiées en direct (les mêmes que le serveur) et affichage du mot de passe à la demande.
  $$("[data-password-form]").forEach(function (form) {
    var fresh = $("input[name=new]", form), confirm = $("input[name=confirm]", form);
    var min = Number(form.dataset.min) || 12, user = (form.dataset.username || "").toLowerCase();
    var check = function () {
      var v = fresh.value, rules = {
        length: v.length >= min,
        username: !!v && (!user || v.toLowerCase().indexOf(user) < 0),
        variety: new Set(v).size >= 5,
        match: !!v && v === confirm.value
      };
      $$("[data-rule]", form).forEach(function (li) { li.classList.toggle("ok", rules[li.dataset.rule]); });
    };
    fresh.addEventListener("input", check); confirm.addEventListener("input", check); check();
  });
  // Afficher / masquer : bouton œil sur chaque champ de mot de passe (ajouté ici, pour toutes les pages).
  var EYE = '<svg class="eye" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>' +
            '<svg class="eye-off" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M10.6 5.1A10.4 10.4 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-2.2 3.2M6.6 6.6C3.7 8.4 2 12 2 12s3.5 7 10 7c1.7 0 3.2-.4 4.5-1.1M9.9 9.9a3 3 0 0 0 4.2 4.2M2 2l20 20"/></svg>';
  window.lrEyeIcons = EYE;
  $$('input[type="password"]').forEach(function (input) {
    var wrap = input.parentElement;
    if (!wrap.classList.contains("password-field")) {
      wrap = document.createElement("span"); wrap.className = "password-field";
      input.parentNode.insertBefore(wrap, input); wrap.appendChild(input);
    }
    if ($(".reveal", wrap)) return;
    var b = document.createElement("button");
    b.type = "button"; b.className = "reveal"; b.innerHTML = EYE;
    b.setAttribute("aria-pressed", "false"); b.setAttribute("aria-label", t("Afficher le mot de passe")); b.title = t("Afficher le mot de passe");
    wrap.appendChild(b);
  });
  // Générer un mot de passe : lisible (sans caractères ambigus), 4 groupes de 5, aléa du navigateur ; affiché en clair.
  var PW_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  function randomPassword() {
    var bytes = new Uint32Array(20), out = "";
    crypto.getRandomValues(bytes);
    for (var i = 0; i < 20; i++) out += (i && i % 5 === 0 ? "-" : "") + PW_ALPHABET[bytes[i] % PW_ALPHABET.length];
    return out;
  }
  function checkPwRule(input) {
    var rule = document.getElementById(input.getAttribute("aria-describedby") || "");
    if (!rule) return;
    var v = input.value, form = input.form, user = form && form.username ? form.username.value.trim().toLowerCase() : "";
    var ok = v.length >= +input.dataset.pwRules && new Set(v).size >= 5 && !(user && v.toLowerCase().indexOf(user) >= 0);
    rule.classList.toggle("ok", !!v && ok);
    rule.classList.toggle("bad", !!v && !ok);
    rule.textContent = !v ? t("Vide : un mot de passe sera généré à la création") :
      ok ? t("Mot de passe valide") : t("%(n)s caractères minimum, sans le nom du compte", {n: input.dataset.pwRules});
  }
  $$("input[data-pw-rules]").forEach(function (input) {
    var update = function () { checkPwRule(input); };
    input.addEventListener("input", update);
    if (input.form && input.form.username) input.form.username.addEventListener("input", update);
    update();
  });
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-generate]");
    if (!b) return;
    var input = document.getElementById(b.dataset.generate), eye = $(".reveal", input.parentElement);
    input.value = randomPassword();
    input.type = "text";
    if (eye) { eye.setAttribute("aria-pressed", "true"); eye.title = t("Masquer le mot de passe"); eye.setAttribute("aria-label", eye.title); }
    b.classList.remove("spun"); void b.offsetWidth; b.classList.add("spun");
    input.dispatchEvent(new Event("input", {bubbles: true}));
    input.focus(); input.select();
  });
  /* ---------------------------------------------------------------- choix conditionnels et planification */

  // [data-show-for="nom:v1 v2"] : visible (et ses champs envoyés) seulement si le bouton radio « nom » coché vaut v1 ou v2.
  function syncSwitches(form) {
    // Choix qui change la destination du formulaire (nouveau dépôt APT ou RPM).
    var target = form.querySelector("input[data-action]:checked");
    if (target) form.setAttribute("action", target.dataset.action);
    $$("[data-show-for]", form).forEach(function (el) {
      var spec = el.dataset.showFor.split(":"), checked = form.querySelector('input[name="' + spec[0] + '"]:checked');
      var on = !!checked && spec[1].split(" ").indexOf(checked.value) >= 0;
      el.hidden = !on;
      $$("input, select, textarea", el).forEach(function (i) { i.disabled = !on; });
    });
    var alt = form.dataset.altWhen;                // formulaire à deux issues (exécuter maintenant / planifier)
    if (alt) {
      var parts = alt.split(":"), picked = form.querySelector('input[name="' + parts[0] + '"]:checked');
      var on2 = !!picked && picked.value === parts[1];
      ["confirm", "confirmText", "confirmOk"].forEach(function (k) {
        if (form.dataset[k + "Base"] === undefined) form.dataset[k + "Base"] = form.dataset[k] || "";
        form.dataset[k] = on2 ? form.dataset[k + "Alt"] : form.dataset[k + "Base"];
      });
      $$("[data-alt]", form).forEach(function (b) {
        var label = $("[data-label]", b) || b;
        if (b.dataset.base === undefined) b.dataset.base = label.textContent;
        label.textContent = on2 ? b.dataset.alt : b.dataset.base;
      });
    }
    $$("[data-planner]", form).forEach(renderPlanner);
  }
  var EN = LANG === "en";
  var DAYS_FR = EN ? ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                   : ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
  var MONTHS_FR = EN ? ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
                     : ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
  function ordinal(n) {
    if (!EN) return n === 1 ? "1er" : String(n);
    var s = n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" })[n % 10] || "th";
    return n + s;
  }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function dayIndex(d) { return (d.getDay() + 6) % 7; }        // lundi = 0, comme côté serveur
  function joinFr(a) { return a.length < 2 ? a.join("") : a.slice(0, -1).join(", ") + (EN ? " and " : " et ") + a[a.length - 1]; }
  function whenFr(d) {
    var year = d.getFullYear() !== new Date().getFullYear() ? " " + d.getFullYear() : "", hm = pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    if (EN) return DAYS_FR[dayIndex(d)] + ", " + MONTHS_FR[d.getMonth()] + " " + d.getDate() + (year ? "," + year : "") + " at " + hm;
    return DAYS_FR[dayIndex(d)] + " " + ordinal(d.getDate()) + " " + MONTHS_FR[d.getMonth()] + year + " à " + hm;
  }
  function plannerState(box) {
    var rec = box.querySelector('input[name="recurrence"]:checked'), date = box.querySelector('input[name="start_date"]').value;
    var time = box.querySelector('input[name="at_time"]').value;
    return { rec: rec ? rec.value : "once", date: date, time: time, monthday: parseInt(box.querySelector('select[name="monthday"]').value, 10),
             days: $$('input[name="weekdays"]:checked', box).map(function (i) { return parseInt(i.value, 10); }) };
  }
  // Même calcul que schedules.next_occurrence : première occurrence strictement après maintenant.
  function nextRun(st) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(st.date) || !/^\d{2}:\d{2}$/.test(st.time)) return null;
    var p = st.date.split("-").map(Number), h = st.time.split(":").map(Number), now = new Date();
    var start = new Date(p[0], p[1] - 1, p[2]), at = function (d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate(), h[0], h[1]); };
    if (st.rec === "once") { var once = at(start); return once > now ? once : null; }
    var day = start > now ? start : new Date(now.getFullYear(), now.getMonth(), now.getDate());
    if (st.rec === "monthly") {
      for (var m = 0; m < 25; m++) {
        var y = day.getFullYear(), mo = day.getMonth() + m, last = new Date(y, mo + 1, 0).getDate();
        var c = at(new Date(y, mo, Math.min(st.monthday, last)));
        if (c > now && c >= start) return c;
      }
      return null;
    }
    var allowed = st.rec === "weekly" ? st.days : [0, 1, 2, 3, 4, 5, 6];
    for (var o = 0; o < 8; o++) {
      var d = new Date(day.getFullYear(), day.getMonth(), day.getDate() + o), w = at(d);
      if (allowed.indexOf(dayIndex(d)) >= 0 && w > now) return w;
    }
    return null;
  }
  function describeFr(st) {
    var at = st.time;
    if (st.rec === "once") { var p = st.date.split("-").map(Number), h = st.time.split(":").map(Number); return t("Le %(when)s", { when: whenFr(new Date(p[0], p[1] - 1, p[2], h[0], h[1])) }); }
    if (st.rec === "daily") return t("Chaque jour à %(at)s", { at: at });
    if (st.rec === "weekly") {
      var days = st.days.slice().sort();
      if (days.length === 7) return t("Chaque jour à %(at)s", { at: at });
      if (days.join() === "0,1,2,3,4") return t("Du lundi au vendredi à %(at)s", { at: at });
      if (days.join() === "5,6") return t("Le week-end à %(at)s", { at: at });
      return t("Chaque %(days)s à %(at)s", { days: joinFr(days.map(function (d) { return DAYS_FR[d]; })), at: at });
    }
    if (st.monthday === 31) return t("Le dernier jour de chaque mois à %(at)s", { at: at });
    return t("Le %(day)s de chaque mois à %(at)s", { day: ordinal(st.monthday), at: at });
  }

  function renderPlanner(box) {
    var st = plannerState(box), out = $("[data-summary]", box), text = $("span", out), label = $("[data-date-label]", box);
    if (label) label.textContent = st.rec === "once" ? t("Date") : t("À partir du");
    var next = st.rec === "weekly" && !st.days.length ? null : nextRun(st);
    out.classList.toggle("warn", !next);
    if (st.rec === "weekly" && !st.days.length) text.textContent = t("Choisissez au moins un jour de la semaine.");
    else if (!st.date || !st.time) text.textContent = t("Indiquez une date et une heure.");
    else text.textContent = describeFr(st) + (next ? (st.rec === "once" ? "" : " · " + t("prochaine exécution : %(when)s", { when: whenFr(next) })) : " · " + t("cet instant est déjà passé"));
  }
  $$("form").forEach(function (form) {
    if (!form.querySelector("[data-show-for], [data-planner], [data-alt-when]") && !form.dataset.altWhen) return;
    syncSwitches(form);
    form.addEventListener("change", function () { syncSwitches(form); });
    form.addEventListener("input", function (e) { if (e.target.closest("[data-planner]")) syncSwitches(form); });
  });

  document.addEventListener("click", function (e) {
    var b = e.target.closest(".reveal");
    if (!b) return;
    var input = $("input", b.parentElement), show = input.type === "password", label = show ? t("Masquer le mot de passe") : t("Afficher le mot de passe");
    input.type = show ? "text" : "password";
    b.setAttribute("aria-pressed", String(show)); b.setAttribute("aria-label", label); b.title = label;
  });

  /* ---------------------------------------------------------------- membres d'un groupe */

  // Compteur des cases cochées (groupes, préparation d'un déploiement) et lignes cochées seules (éditeur de groupe).
  function syncPicked() {
    $$("[data-checked-count]").forEach(function (el) {
      el.textContent = $$(el.dataset.checkedCount).filter(function (c) { return c.checked; }).length;
    });
    $$("tr input[name=members]").forEach(function (c) { c.closest("tr").classList.toggle("picked", c.checked); });
  }
  document.addEventListener("change", function (e) {
    if (e.target.matches("input[type=checkbox], input[type=radio]")) syncPicked();
    if (e.target.matches("[data-only-checked]")) $(e.target.dataset.onlyChecked).classList.toggle("only-picked", e.target.checked);
  });
  if ($("[data-checked-count]")) syncPicked();

  /* ---------------------------------------------------------------- jauges */

  $$("[data-width]").forEach(function (el) { el.style.width = Math.max(0, Math.min(100, Number(el.dataset.width))) + "%"; });

  /* ---------------------------------------------------------------- actualisation en direct */

  function poll(url, every, fn) {
    var busy = false;
    var tick = function () {
      if (document.hidden || busy) return;
      busy = true;
      fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
        .then(function (r) { if (r.status === 401) { window.location.reload(); throw new Error("401"); } if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(fn).catch(function () {}).then(function () { busy = false; });
    };
    setInterval(tick, every);
    return tick;
  }

  // Texte sûr dans un élément comme dans un attribut entre guillemets.
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Journal Windows Update : remplace les lignes, en signalant les nouvelles.
  function renderWuLog(d) {
    var body = $("#wu-log"), none = $("#wu-none");
    var known = {};
    Array.prototype.forEach.call(body.rows, function (r) { known[r.dataset.key] = 1; });
    body.innerHTML = d.recent.map(function (r) {
      var key = r.time + r.client + r.file;
      var file = r.file.length > 70 ? r.file.slice(0, 70) + "…" : r.file;
      var who = r.name ? '<span>' + esc(r.name) + '</span><span class="sub mono">' + esc(r.client) + (r.shared ? ' · ' + esc(t('adresse partagée (NAT)')) : '') + '</span>'
                       : '<span class="mono">' + esc(r.client) + '</span>';
      var source = r.source === "cache" ? '<span class="st ok">cache</span>' : '<span class="st">' + esc(r.source) + '</span>';
      return '<tr' + (known[key] ? "" : ' class="new"') + ' data-key="' + esc(key) + '">' +
        '<td class="mono muted nowrap">' + esc(r.time) + '</td><td class="nowrap">' + who + '</td>' +
        '<td><span class="mono">' + esc(file) + '</span><span class="sub">' + esc(r.host) + '</span></td>' +
        '<td class="num nowrap">' + (r.size ? bytes(r.size) : "—") + '</td>' +
        '<td class="nowrap" title="' + esc(r.cache) + '">' + source + '</td></tr>';
    }).join("");
    none.classList.toggle("hidden", d.recent.length > 0);
  }

  // Catalogue Windows : progression des téléchargements, rechargement quand un état change.
  function renderApps(d) {
    var changed = false;
    d.apps.forEach(function (a) {
      var row = $('[data-slug="' + a.slug + '"]');
      if (!row) return;
      if (row.dataset.status !== a.status) { changed = true; return; }
      var el = $("[data-progress]", row);
      if (!el || !a.progress.done) return;
      el.textContent = a.progress.total
        ? t("téléchargement %(pct)s %%", { pct: Math.round(100 * a.progress.done / a.progress.total) }).replace("%%", "%")
        : t("téléchargement %(size)s", { size: bytes(a.progress.done) });
    });
    if (changed) window.location.reload();
  }

  // Opération de l'appliance : journal en direct, rechargement à la fin (le portail redémarre pendant
  // une mise à jour : les requêtes échouent quelques secondes, l'actualisation reprend ensuite).
  function renderJob(d) {
    var log = $("#job-log");
    var bottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.textContent = d.log || t("Journal vide.");
    if (bottom) log.scrollTop = log.scrollHeight;
    if (d.state !== "queued" && d.state !== "running") window.location.reload();
  }

  // Réseau en direct (vue d'ensemble) : débit calculé entre deux relevés des compteurs de l'interface, courbe des
  // 2 dernières minutes avec une échelle commune aux deux sens pour qu'ils restent comparables.
  var net = $("[data-net]");
  if (net) {
    var SPAN = 60, EVERY = 2000, samples = [], previous = null;
    var cells = $$("[data-net-dir]", net);
    var bits = function (v) {
      var units = ["bit/s", "kbit/s", "Mbit/s", "Gbit/s"], i = 0;
      while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
      var s = i === 0 ? String(Math.round(v)) : v < 10 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : String(Math.round(v));
      return (LANG === "en" ? s : s.replace(".", ",")) + " " + units[i];
    };
    var note = function (cell, dir) {
      var peak = Math.max.apply(null, samples.map(function (p) { return p[dir]; }));
      $("[data-net-note]", cell).textContent = t("pic sur %(s)s s : %(rate)s", { s: Math.round(samples.length * EVERY / 1000), rate: bits(peak) });
    };
    var render = function () {
      var top = Math.max(1000, Math.max.apply(null, samples.map(function (p) { return Math.max(p.rx, p.tx); })));
      var step = 120 / (SPAN - 1);
      cells.forEach(function (cell) {
        var dir = cell.dataset.netDir;
        var points = samples.map(function (p, i) {
          return [(120 - (samples.length - 1 - i) * step).toFixed(2), (38 - 34 * p[dir] / top).toFixed(2)];
        });
        var line = points.map(function (p, i) { return (i ? "L" : "M") + p[0] + " " + p[1]; }).join(" ");
        $(".line", cell).setAttribute("d", line);
        $(".area", cell).setAttribute("d", points.length > 1 ? line + " L120 39 L" + points[0][0] + " 39 Z" : "");
        $("[data-net-value]", cell).textContent = bits(samples[samples.length - 1][dir]);
        if (!cell.dataset.hover) note(cell, dir);
      });
    };
    cells.forEach(function (cell) {
      var svg = $(".net-spark", cell), dir = cell.dataset.netDir;
      svg.addEventListener("mousemove", function (e) {
        if (!samples.length) return;
        var box = svg.getBoundingClientRect();
        var back = Math.round((box.right - e.clientX) / box.width * (SPAN - 1));
        var p = samples[Math.max(0, samples.length - 1 - back)];
        cell.dataset.hover = "1";
        $("[data-net-note]", cell).textContent = t("il y a %(s)s s : %(rate)s", { s: Math.round((Date.now() - p.at) / 1000), rate: bits(p[dir]) });
      });
      svg.addEventListener("mouseleave", function () { delete cell.dataset.hover; if (samples.length) note(cell, dir); });
    });
    var sample = function () {
      if (document.hidden) return;
      fetch(net.dataset.net, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) {
          $("[data-net-off]", net).classList.toggle("hidden", d.available);
          $(".net-grid", net).classList.toggle("hidden", !d.available);
          if (!d.available) return;
          $("[data-net-iface]", net).textContent = d.iface;
          if (previous && d.t > previous.t && d.iface === previous.iface) {
            var dt = d.t - previous.t;
            samples.push({ rx: Math.max(0, (d.rx - previous.rx) * 8 / dt), tx: Math.max(0, (d.tx - previous.tx) * 8 / dt), at: Date.now() });
            if (samples.length > SPAN) samples.shift();
            render();
          }
          previous = d;
        })
        .catch(function () {});
    };
    sample();
    setTimeout(sample, 1000);
    setInterval(sample, EVERY);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) { previous = null; sample(); } });
  }

  /* ---------------------------------------------------------------- supervision en direct */
  // Tant que l'onglet Supervision est visible, la page signale qu'elle regarde (l'agent envoie alors un relevé toutes
  // les 2 s) et trace les 3 dernières minutes. Onglet quitté ou fenêtre cachée : plus aucune demande.
  // Portée propre : tout app.js partage une seule fonction, et d'autres blocs déclarent aussi « tick », « start »…
  if ($("[data-live]")) (function (liveBox) {
    var SPAN = 180, X0 = 64, X1 = 1080, Y0 = 12, Y1 = 164;
    var panel = liveBox.closest("[data-panel]"), stateEl = $("[data-live-state]", liveBox);
    var svg = $(".live-chart", liveBox), tip = $(".live-tip", liveBox), cross = $(".live-cross", liveBox);
    var pts = [], since = 0, timer = null, started = 0, offset = 0;
    var lang = document.documentElement.lang || "fr";
    var UNITS = lang === "en" ? ["B", "KB", "MB", "GB", "TB"] : lang === "es" ? ["B", "KB", "MB", "GB", "TB"] : ["o", "Ko", "Mo", "Go", "To"];
    var human = function (n) {
      n = Number(n) || 0;
      for (var i = 0; i < UNITS.length; i++) {
        if (n < 1024 || i === UNITS.length - 1) {
          if (!i) return Math.round(n) + " " + UNITS[0];
          var text = n < 10 ? n.toFixed(2) : n.toFixed(1);
          return (lang === "en" ? text : text.replace(".", ",")) + " " + UNITS[i];
        }
        n /= 1024;
      }
    };
    var serverNow = function () { return Date.now() / 1000 + offset; };
    var xOf = function (at, now) { return X0 + (X1 - X0) * (1 - (now - at) / SPAN); };
    var yOf = function (v) { return Y1 - (Y1 - Y0) * Math.min(100, Math.max(0, v)) / 100; };
    var render = function () {
      var now = serverNow(), ends = [];
      ["cpu", "mem"].forEach(function (key) {
        var line = $('[data-live-line="' + key + '"]', liveBox), end = $('[data-live-end="' + key + '"]', liveBox);
        var shown = pts.filter(function (p) { return p[key] != null && now - p.at <= SPAN; });
        line.setAttribute("points", shown.map(function (p) { return xOf(p.at, now).toFixed(1) + "," + yOf(p[key]).toFixed(1); }).join(" "));
        var last = shown[shown.length - 1];
        end.hidden = !last;
        if (last) { $("span", end).textContent = Math.round(last[key]) + " %"; ends.push({ el: end, y: yOf(last[key]) }); }
      });
      ends.sort(function (a, b) { return a.y - b.y; });
      for (var i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 15) ends[i].y = ends[i - 1].y + 15;   // jamais superposées
      ends.forEach(function (e) { e.el.style.top = (e.y - 8) + "px"; });
    };
    var tile = function (key, html) { var el = $('[data-live-v="' + key + '"]'); if (el) el.innerHTML = html; };
    var tiles = function (p) {
      if (p.cpu != null) tile("cpu", Math.round(p.cpu) + "<small> %</small>");
      if (p.mem != null) tile("mem", Math.round(p.mem) + "<small> %</small>");
      if (p.net_rx != null) tile("net_rx", "↓ " + human(p.net_rx) + "/s");
      if (p.net_tx != null) tile("net_tx", "↑ " + human(p.net_tx) + "/s " + t("envoyés"));
    };
    var status = function () {
      var last = pts.length ? pts[pts.length - 1].at : 0, fresh = serverNow() - last < 8;
      stateEl.classList.toggle("on", fresh);
      stateEl.classList.toggle("off", !fresh && Date.now() - started > 15000);
      stateEl.textContent = fresh ? t("en direct · toutes les 2 s") : Date.now() - started > 15000
        ? t("le poste ne répond pas en direct (agent antérieur à 2.71) : courbes toutes les 2 min ci-dessous")
        : t("connexion au poste…");
    };
    var visible = function () { return !document.hidden && panel && panel.classList.contains("on"); };
    var tick = function () {
      if (!visible()) { stop(); return; }
      fetch(liveBox.dataset.live + "?since=" + since, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) {
          offset = d.now - Date.now() / 1000;
          d.points.forEach(function (p) { pts.push(p); since = p.at; });
          pts = pts.filter(function (p) { return d.now - p.at <= SPAN + 4; });
          if (pts.length) tiles(pts[pts.length - 1]);
          render(); status();
        })
        .catch(function () { status(); });
    };
    var stop = function () {
      if (!timer) return;
      clearInterval(timer[0]); clearInterval(timer[1]); timer = null;
      delete document.body.dataset.noReload;
    };
    var start = function () {
      if (timer) return;
      started = Date.now();
      document.body.dataset.noReload = "1";        // pas de rechargement de la page pendant le direct
      tick();
      timer = [setInterval(tick, 2000), setInterval(function () { render(); status(); }, 500)];   // défilement continu
    };
    var check = function () { if (visible()) start(); else stop(); };
    document.addEventListener("click", function (e) { if (e.target.closest(".tabs [data-tab]")) setTimeout(check, 0); });
    document.addEventListener("visibilitychange", check);
    setTimeout(check, 0);
    // Survol : réticule et valeurs du relevé le plus proche.
    svg.addEventListener("mousemove", function (e) {
      var box = svg.getBoundingClientRect(), vx = (e.clientX - box.left) / box.width * 1200, now = serverNow();
      var best = null;
      pts.forEach(function (p) { var d = Math.abs(xOf(p.at, now) - vx); if (now - p.at <= SPAN && (!best || d < best.d)) best = { p: p, d: d }; });
      if (!best || best.d > 40) { tip.hidden = true; cross.style.opacity = 0; return; }
      var x = xOf(best.p.at, now);
      cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.style.opacity = 1;
      var parts = [t("il y a %(s)s s", { s: Math.max(0, Math.round(now - best.p.at)) })];
      if (best.p.cpu != null) parts.push(t("Processeur") + " " + Math.round(best.p.cpu) + " %");
      if (best.p.mem != null) parts.push(t("Mémoire") + " " + Math.round(best.p.mem) + " %");
      if (best.p.net_rx != null) parts.push("↓ " + human(best.p.net_rx) + "/s ↑ " + human(best.p.net_tx) + "/s");
      tip.textContent = parts.join(" · ");
      tip.hidden = false;
      tip.style.left = Math.min(box.width - tip.offsetWidth - 4, Math.max(0, e.clientX - box.left + 12)) + "px";
    });
    svg.addEventListener("mouseleave", function () { tip.hidden = true; cross.style.opacity = 0; });
  })($("[data-live]"));

  /* ---------------------------------------------------------------- avancement d'une opération du serveur */
  // Barre en haut de « Mises à jour du serveur » : étape et pourcentage lus dans le journal de l'opération (toutes
  // les 2 s), durée écoulée chaque seconde. Pendant la mise à jour de loco, le portail redémarre : la barre l'annonce
  // et la page se recharge dès qu'il répond de nouveau.
  if ($("[data-job-progress]")) (function (box) {
    var stepEl = $("[data-op-step]", box), pctEl = $("[data-op-pct]", box), timeEl = $("[data-op-elapsed]", box);
    var noteEl = $("[data-op-note]", box), fill = $("[data-op-fill]", box), bar = $(".op-bar", box);
    var elapsed = Number(timeEl.dataset.elapsed) || 0, failures = 0, done = false, baseNote = noteEl.textContent;
    var duration = function (s) {
      var m = Math.floor(s / 60);
      return m ? t("%(m)s min %(s)s s", { m: m, s: s % 60 }) : t("%(s)s s", { s: s });
    };
    var showTime = function () { timeEl.textContent = duration(elapsed); };
    showTime();
    setInterval(function () { if (!done) { elapsed++; showTime(); } }, 1000);
    var apply = function (p) {
      if (p.elapsed != null) elapsed = p.elapsed;
      stepEl.textContent = p.step || "";
      var known = p.pct != null;
      bar.classList.toggle("indeterminate", !known);
      if (known) { fill.style.width = p.pct + "px"; bar.setAttribute("aria-valuenow", p.pct); pctEl.textContent = p.pct + " %"; }
      else { fill.style.width = ""; bar.removeAttribute("aria-valuenow"); pctEl.textContent = ""; }
    };
    var tick = function () {
      if (done || document.hidden) return;
      fetch(box.dataset.jobProgress, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (d) {
          if (failures) { noteEl.textContent = baseNote; failures = 0; }
          apply(d.progress || {});
          if (d.state !== "queued" && d.state !== "running") {
            done = true;
            setTimeout(function () { window.location.reload(); }, 1200);
          }
        })
        .catch(function () {
          // Portail injoignable : redémarrage de fin d'installation (ou réseau) ; on réessaie sans rien perdre.
          if (++failures === 1) noteEl.textContent = t("Le portail redémarre pour terminer l'installation… reconnexion automatique.");
          bar.classList.add("indeterminate");
        });
    };
    setInterval(tick, 2000);
  })($("[data-job-progress]"));

  var operations = $("[data-operations]");
  if (operations) {
    poll(operations.dataset.operations, 3000, function (d) {
      if (d.signature !== operations.dataset.signature) window.location.reload();
    });
  }
  /* ---------------------------------------------------------------- caches (Windows Update, APT, RPM…) */
  // Contenu d'un cache listé en direct : /cache/state renvoie l'index publié par l'agent root.
  // Lire l'index est gratuit (aucune analyse) ; seules l'ouverture, « Actualiser » et une purge demandent une analyse.
  $$("[data-cache]").forEach(function (panel) {
    var area = panel.dataset.cache, csrf = panel.dataset.csrf, canPurge = panel.dataset.canPurge === "1";
    var rows = $("[data-cache-rows]", panel), note = $("[data-cache-state]", panel);
    var sum = $("[data-cache-sum]", panel), bar = $("[data-cache-bar]", panel), filter = $("[data-cache-filter]", panel);
    var more = $("[data-cache-more]", panel);
    var current = [], loaded = false, count = 0, shownCount = 0, expanded = false;
    var LIMIT = 5;    // n'afficher que les 5 plus récents, le reste se déplie
    var del = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/></svg>';
    // Code renvoyé par l'agent -> libellé « à quoi le fichier correspond » (traduit à l'initialisation).
    var KIND = { "win-update": t("Mise à jour Windows"), "win-defender": t("Définitions Microsoft Defender"),
                 "win-edge": t("Microsoft Edge"), "win-office": t("Microsoft Office"), "win-image": t("Image Windows"),
                 "repo": t("Métadonnées du dépôt"), "flatpak": t("Objet Flatpak") };

    function setNote(msg) { note.textContent = msg; note.hidden = !msg; }

    function row(e) {
      var tr = document.createElement("tr");
      tr.innerHTML = '<td><strong class="cache-name"></strong><span class="sub mono cache-file"></span></td>' +
        '<td class="mono muted hide-sm cache-ctx"></td><td class="num nowrap cache-size"></td>' +
        '<td class="muted nowrap hide-sm cache-at"></td>' +
        (canPurge ? '<td class="right"><button type="button" class="btn icon sm ghost danger" data-cache-del="' + esc(e.id) + '">' + del + '</button></td>' : "");
      var label = KIND[e.kind] || "";
      $(".cache-name", tr).textContent = label || e.name || "—";
      $(".cache-file", tr).textContent = label ? (e.name || "") : "";    // nom de fichier en second, sous le libellé
      $(".cache-ctx", tr).textContent = e.context || "";
      $(".cache-size", tr).textContent = bytes(e.bytes);
      if (e.parts > 1) $(".cache-size", tr).title = t("%(n)s fragments", { n: e.parts });
      $(".cache-at", tr).textContent = e.at || "";
      if (canPurge) { var b = $("[data-cache-del]", tr); b.title = t("Supprimer du cache"); b.setAttribute("aria-label", t("Supprimer du cache")); }
      return tr;
    }

    function render() {
      var q = (filter && filter.value || "").trim().toLowerCase();
      var matches = current.filter(function (e) { return !q || (e.name + " " + e.context).toLowerCase().indexOf(q) >= 0; });
      rows.textContent = "";
      matches.slice(0, expanded ? matches.length : LIMIT).forEach(function (e) { rows.appendChild(row(e)); });
      // Déplié : la liste devient une zone à hauteur bornée (défilement interne) pour garder « Réduire » à portée.
      panel.classList.toggle("expanded", expanded && matches.length > LIMIT);
      if (more) {
        if (matches.length > LIMIT) { more.hidden = false; more.textContent = expanded ? t("Réduire la liste") : t("Afficher les %(n)s autres", { n: matches.length - LIMIT }); }
        else more.hidden = true;
      }
      if (!loaded) setNote(t("Analyse du cache…"));
      else if (count === 0) setNote(t("Le cache est vide."));
      else if (matches.length === 0) setNote(t("Aucun fichier ne correspond."));
      else if (shownCount < count) setNote(t("Les %(n)s objets les plus récents ; le reste du cache n'est pas listé.", { n: shownCount }));
      else setNote("");
    }

    function apply(d) {
      var info = d && d[area];
      if (!info || !info.present) { loaded = true; current = []; count = 0; if (sum) sum.hidden = true; if (bar) bar.hidden = true; setNote(info ? t("Le cache est vide.") : t("Cache indisponible.")); return; }
      loaded = true; current = info.entries || []; count = info.count || 0; shownCount = info.shown || current.length;
      if (sum) { sum.hidden = false; sum.textContent = count + " " + (count === 1 ? t("fichier") : t("fichiers")) + " · " + bytes(info.bytes || 0); }
      if (bar) bar.hidden = count === 0;
      render();
    }

    function load(scan) {
      fetch("/cache/state?areas=" + area + (scan ? "&scan=1" : ""), { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
        .then(function (r) { return r.json(); }).then(apply).catch(function () {});
    }

    function purge(body, title, text, ok) {
      confirmDialog(title, text, ok).then(function (yes) {
        if (!yes) return;
        var data = new FormData(); data.set("csrf", csrf); data.set("area", area);
        Object.keys(body).forEach(function (k) { data.set(k, body[k]); });
        fetch("/cache/purge", { method: "POST", body: data, headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
          .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
          .then(function () { setNote(t("Analyse du cache…")); [500, 1600, 3000].forEach(function (ms) { setTimeout(function () { load(false); }, ms); }); })
          .catch(function () { toast(t("La purge du cache a échoué."), "error"); });
      });
    }

    if (filter) filter.addEventListener("input", function () { expanded = false; render(); });
    if (more) more.addEventListener("click", function () { expanded = !expanded; render(); });
    var refresh = $("[data-cache-refresh]", panel);
    if (refresh) refresh.addEventListener("click", function () { if (loaded) setNote(t("Analyse du cache…")); load(true); [1500, 3000].forEach(function (ms) { setTimeout(function () { load(false); }, ms); }); });
    if (canPurge) panel.addEventListener("click", function (e) {
      var d = e.target.closest("[data-cache-del]");
      if (d) { purge({ id: d.dataset.cacheDel }, t("Supprimer ce fichier du cache ?"), t("Il repart du serveur d'origine à la prochaine demande d'un poste. Rien n'est perdu côté postes."), t("Supprimer")); return; }
      if (e.target.closest("[data-cache-clear]")) purge({ all: "1" }, t("Vider tout le cache ?"), t("Tous les fichiers en cache sont supprimés. Ils seront retéléchargés à la demande ; rien n'est perdu côté postes."), t("Vider le cache"));
    });

    load(true);
    [1300, 2800, 5000].forEach(function (ms) { setTimeout(function () { load(false); }, ms); });
    poll("/cache/state?areas=" + area, 10000, apply);
  });

  /* ---------------------------------------------------------------- composeur d'enrôlement */
  // On coche ce que le poste reçoit (gestion / dépôt / cache) ; la commande à lancer est générée en direct.
  $$("[data-composer]").forEach(function (box) {
    var os = box.dataset.composer;
    var out = $("[data-composer-out]", box);
    var all = $("[data-cap-all]", box);
    var caps = $$("[data-cap]", box).filter(function (c) { return c.dataset.cap !== "first"; });
    var first = $('[data-cap="first"]', box);
    function on(id) { var c = $('[data-cap="' + id + '"]', box); return !!(c && c.checked); }

    function steps() {
      var s = [];
      if (os === "windows") {
        if (on("manage")) s.push({ label: t("Installer l'agent"), cmd: box.dataset.agent, prompt: "PS>" });
        if (on("wu")) s.push({ label: t("Rediriger Windows Update"), cmd: box.dataset.wu, prompt: "PS>" });
        return s;
      }
      var m = on("manage"), r = on("repo"), c = on("cache"), fst = first && first.checked && box.dataset.http;
      if (m && r && c && box.dataset.full) {    // paquet complet : agent + dépôt + cache en une commande
        s.push({ label: t("Agent, dépôt et cache"), cmd: fst && box.dataset.fullHttp ? box.dataset.fullHttp : box.dataset.full });
        return s;
      }
      if (m) s.push({ label: t("Installer l'agent"), cmd: fst && box.dataset.agentHttp ? box.dataset.agentHttp : box.dataset.agent });
      if (r || c) {
        var fl = [];
        if (r && !c) fl.push("--no-proxy");
        if (c && !r) fl.push("--cache-only");
        if (fst) fl.push("--trust-ca");
        var host = fst ? box.dataset.http : box.dataset.base;
        s.push({ label: r && c ? t("Dépôt et cache") : (r ? t("Dépôt loco") : t("Cache des mises à jour")),
                 cmd: "curl -fsSL " + host + box.dataset.setupPath + " | sudo sh" + (fl.length ? " -s -- " + fl.join(" ") : "") });
      }
      return s;
    }

    function dim() {    // griser la ligne dont l'interrupteur est éteint (comme la page Fonctionnalités)
      $$(".feature-row", box).forEach(function (row) {
        var input = $("input[type=checkbox]", row);
        if (input) row.classList.toggle("off", !input.checked);
      });
    }

    function render() {
      dim();
      var s = steps();
      out.textContent = "";
      if (!s.length) {
        var p = document.createElement("p"); p.className = "hint"; p.textContent = t("Cochez au moins une option.");
        out.appendChild(p); return;
      }
      s.forEach(function (step, i) {
        if (s.length > 1) {
          var lab = document.createElement("span"); lab.className = "cmd-label";
          lab.textContent = (i + 1) + ". " + step.label; out.appendChild(lab);
        }
        var div = document.createElement("div"); div.className = "cmd";
        var code = document.createElement("code"); code.setAttribute("data-prompt", step.prompt || "#"); code.textContent = step.cmd;
        var btn = document.createElement("button"); btn.type = "button"; btn.className = "copy"; btn.textContent = t("Copier");
        div.appendChild(code); div.appendChild(btn); out.appendChild(div);
      });
    }

    function syncAll() { if (all) all.checked = caps.every(function (c) { return c.checked; }); }
    box.addEventListener("change", function (e) {
      if (e.target === all) caps.forEach(function (c) { c.checked = all.checked; });
      else if (e.target.dataset.cap && e.target.dataset.cap !== "first") syncAll();
      render();
    });
    syncAll(); render();
  });

  var live = $("[data-poll]");
  if (live) {
    var kind = live.dataset.pollKind;
    if (kind === "apps" && !$('tr[data-status="downloading"]')) return;
    // Parc : rechargement quand un poste ou une action change (rien n'est saisi sur ces pages).
    // Suspendu tant que le gestionnaire de fichiers est ouvert : ne pas interrompre un transfert ou une édition.
    var reload = function (d) { if (document.body.dataset.noReload) return; if (d.signature && d.signature !== live.dataset.signature) window.location.reload(); };
    var handler = { status: bind, wu: function (d) { bind(d); renderWuLog(d); }, apps: renderApps, job: renderJob,
                    reload: reload }[kind];
    var tick = poll(live.dataset.poll, Number(live.dataset.pollEvery) || 10000, handler);
    if (kind === "apps" || kind === "job") tick();
    if (kind === "job") { var log = $("#job-log"); log.scrollTop = log.scrollHeight; }
  }

  /* PWA : enregistre le service worker (installable + cache de la coque + repli hors-ligne). */
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () { /* contexte non sécurisé ou indisponible */ });
    });
  }
  // Retour d'un fournisseur SSO : la page intermédiaire enchaîne vers loco. Navigation partie de loco : le cookie de
  // session (SameSite=Strict) est envoyé, ce qu'une redirection depuis le fournisseur ne permettrait pas.
  (function () {
    var next = document.querySelector("[data-continue]");
    if (next) location.replace(next.getAttribute("data-continue"));
  })();

  // Accès MCP : préréglages des autorisations du formulaire (lecture seule, tout ce qui est permis, rien).
  (function () {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest && e.target.closest("[data-mcp-preset]");
      if (!btn) return;
      var mode = btn.getAttribute("data-mcp-preset");
      btn.closest("form").querySelectorAll("input[name=tools]:not(:disabled)").forEach(function (box) {
        box.checked = mode === "all" || (mode === "read" && box.hasAttribute("data-read"));
      });
    });
  })();

  // Bouton « Copier » à côté d'un champ (URL de redirection SSO…).
  (function () {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest && e.target.closest("[data-copy]");
      if (!btn) return;
      var field = btn.parentNode.querySelector("input, textarea");
      if (!field) return;
      var label = btn.textContent;
      var done = function () {
        btn.textContent = btn.getAttribute("data-copied") || label;
        setTimeout(function () { btn.textContent = label; }, 1500);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(field.value).then(done, function () { field.select(); });
      } else {
        field.select();
        try { if (document.execCommand("copy")) done(); } catch (err) { /* sélection laissée à l'utilisateur */ }
      }
    });
  })();
})();
