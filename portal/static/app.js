/* loco — interactions du portail, sans dépendance. */
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

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

  var UNITS = ["o", "Ko", "Mo", "Go", "To"];
  function bytes(n) {
    n = Number(n) || 0;
    var i = 0;
    while (n >= 1024 && i < UNITS.length - 1) { n /= 1024; i++; }
    return (i === 0 ? String(Math.round(n)) : n.toFixed(n < 10 ? 2 : 1).replace(".", ",")) + " " + UNITS[i];
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
          el.textContent = v ? "actif" : "arrêté";
          break;
        default: el.textContent = typeof v === "number" ? v.toLocaleString("fr-FR") : v;
      }
    });
  }
  window.lrBind = bind;

  /* ---------------------------------------------------------------- thème */

  /* OLED par défaut ; la lune bascule vers le thème clair et inversement (choix mémorisé). */
  // Apparence : thème (OLED, sombre doux, clair) et couleur d'accent, mémorisés dans le navigateur.
  function applyAppearance() {
    var root = document.documentElement;
    $$("[data-set-theme]").forEach(function (b) { b.setAttribute("aria-checked", b.dataset.setTheme === root.dataset.theme); });
    $$("[data-set-accent]").forEach(function (b) { b.setAttribute("aria-checked", b.dataset.setAccent === (root.dataset.accent || "neutral")); });
  }
  applyAppearance();
  document.addEventListener("click", function (e) {
    var choice = e.target.closest("[data-set-theme], [data-set-accent]");
    if (!choice) return;
    var key = choice.dataset.setTheme ? "theme" : "accent", value = choice.dataset.setTheme || choice.dataset.setAccent;
    document.documentElement.dataset[key] = value;
    try { localStorage.setItem("lr-" + key, value); } catch (err) { /* navigation privée */ }
    applyAppearance();
  });

  /* ---------------------------------------------------------------- notifications */

  function toast(msg, kind) {
    var box = $(".toasts");
    var el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    box.appendChild(el);
    var close = function () { el.classList.add("out"); setTimeout(function () { el.remove(); }, 200); };
    var t = setTimeout(close, kind === "error" ? 8000 : 4000);
    el.addEventListener("click", function () { clearTimeout(t); close(); });
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
      btn.classList.add("done"); btn.textContent = "Copié";
      setTimeout(function () { btn.classList.remove("done"); btn.textContent = "Copier"; }, 1500);
    });
  });

  /* ---------------------------------------------------------------- onglets */

  document.addEventListener("click", function (e) {
    var b = e.target.closest(".tabs [data-tab]");
    if (!b) return;
    var tabs = b.closest(".tabs");
    $$("[data-tab]", tabs).forEach(function (x) { x.setAttribute("aria-selected", x === b); });
    $$("[data-panel]", tabs.parentElement).forEach(function (p) { p.classList.toggle("on", p.dataset.panel === b.dataset.tab); });
    if (b.dataset.cmd && tabs.dataset.target) $(tabs.dataset.target).textContent = b.dataset.cmd;
    // Onglet mémorisé dans l'adresse : conservé lors du rechargement automatique de la page.
    if (tabs.hasAttribute("data-hash")) history.replaceState(null, "", "#" + b.dataset.tab);
  });
  $$(".tabs[data-hash]").forEach(function (tabs) {
    var wanted = location.hash.slice(1), b = wanted && $('[data-tab="' + wanted.replace(/[^a-z0-9-]/gi, "") + '"]', tabs);
    if (b) b.click();
  });

  /* ---------------------------------------------------------------- filtres appliqués au choix */

  // Formulaire de filtres (GET) : l'adresse ne garde que les champs remplis (« ?compte=marie » et non « &q= »).
  function go(form) {
    var params = new URLSearchParams();
    new FormData(form).forEach(function (v, k) { if (v !== "") params.append(k, v); });
    var query = params.toString();
    location.href = (form.getAttribute("action") || location.pathname) + (query ? "?" + query : "");
  }

  document.addEventListener("change", function (e) {
    if (!e.target.matches("select[data-autosubmit], input[type=checkbox][data-autosubmit]")) return;
    if (e.target.form.hasAttribute("data-clean")) go(e.target.form); else e.target.form.submit();
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

  /* ---------------------------------------------------------------- confirmation */

  function confirmDialog(title, text, okLabel) {
    return new Promise(function (resolve) {
      var dlg = $("#confirm");
      $("h2", dlg).textContent = title;
      $(".dlg-b p", dlg).textContent = text || "";
      $("[data-ok]", dlg).textContent = okLabel || "Confirmer";
      var answered = false;
      var done = function (v) { if (answered) return; answered = true; dlg.close(); resolve(v); };
      $("[data-ok]", dlg).onclick = function () { done(true); };
      $("[data-cancel]", dlg).onclick = function () { done(false); };
      dlg.onclose = function () { done(false); };
      dlg.showModal();
    });
  }

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
      fail(xhr.status === 400 ? "Session expirée : rechargez la page." : "Erreur " + xhr.status + ".");
    };
    xhr.onerror = function () { fail("Le serveur ne répond pas."); };
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

  document.addEventListener("click", function (e) {
    var open = e.target.closest("[data-open]");
    if (open) {
      var dlg = $(open.dataset.open);
      if (open.dataset.app) fillEdit(dlg, JSON.parse(open.dataset.app));
      dlg.showModal();
      var first = $("input[type=text], input[type=url]", dlg);
      if (first) first.focus();
      return;
    }
    var close = e.target.closest("[data-close]");
    if (close) close.closest("dialog").close();
  });

  function fillEdit(dlg, app) {
    var form = $("form", dlg);
    form.setAttribute("action", form.dataset.base.replace("__slug__", app.slug));
    $("[data-title]", dlg).textContent = app.name;
    ["name", "version", "args", "url", "kind", "detect"].forEach(function (k) { if (form.elements[k]) form.elements[k].value = app[k] || ""; });
    $("[data-file]", dlg).textContent = app.filename ? app.filename + "  sha256:" + app.sha256.slice(0, 16) : "";
    $('input[type="file"]', form).value = "";
    $(".files", form).textContent = "";
  }

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
      $(".count", selbar).textContent = slugs.length + (slugs.length > 1 ? " sélectionnés" : " sélectionné");
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
      var who = r.name ? '<span class="mono">' + esc(r.name) + '</span><span class="sub">' + esc(r.client) + '</span>'
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
        ? "téléchargement " + Math.round(100 * a.progress.done / a.progress.total) + " %"
        : "téléchargement " + bytes(a.progress.done);
    });
    if (changed) window.location.reload();
  }

  // Opération de l'appliance : journal en direct, rechargement à la fin (le portail redémarre pendant
  // une mise à jour : les requêtes échouent quelques secondes, l'actualisation reprend ensuite).
  function renderJob(d) {
    var log = $("#job-log");
    var bottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.textContent = d.log || "Journal vide.";
    if (bottom) log.scrollTop = log.scrollHeight;
    if (d.state !== "queued" && d.state !== "running") window.location.reload();
  }

  var operations = $("[data-operations]");
  if (operations) {
    poll(operations.dataset.operations, 3000, function (d) {
      if (d.signature !== operations.dataset.signature) window.location.reload();
    });
  }
  var live = $("[data-poll]");
  if (live) {
    var kind = live.dataset.pollKind;
    if (kind === "apps" && !$('tr[data-status="downloading"]')) return;
    // Parc : rechargement quand un poste ou une action change (rien n'est saisi sur ces pages).
    var reload = function (d) { if (d.signature && d.signature !== live.dataset.signature) window.location.reload(); };
    var handler = { status: bind, wu: function (d) { bind(d); renderWuLog(d); }, apps: renderApps, job: renderJob,
                    reload: reload }[kind];
    var tick = poll(live.dataset.poll, Number(live.dataset.pollEvery) || 10000, handler);
    if (kind === "apps" || kind === "job") tick();
    if (kind === "job") { var log = $("#job-log"); log.scrollTop = log.scrollHeight; }
  }
})();
