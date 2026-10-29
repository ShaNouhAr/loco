/* Thème, accent, transparence et fond — appliqués avant l'affichage (chargé en bloquant dans <head>).
   Connecté : le serveur a déjà rendu les attributs data-* sur <html> (apparence du compte) ; on n'applique
   que la transparence (variable CSS). Déconnecté : on restaure le choix local (localStorage). */
(function () {
  var root = document.documentElement;
  var THEMES = /^(oled|dim|light|midnight|sepia)$/;
  var ACCENTS = /^(neutral|blue|green|violet|orange|pink|teal|amber)$/;
  var BG = /^(g:(aurora|ocean|forest|dusk|rose|slate)|w:(montagne|foret|desert|dunes|ocean|aurore|nuit|hiver))$/;

  function applyAlpha(a) {
    var n = parseInt(a, 10);
    if (n >= 55 && n < 100) root.style.setProperty("--surface-alpha", (n / 100).toFixed(2));
    else root.style.removeProperty("--surface-alpha");
  }

  if (root.getAttribute("data-appearance") === "user") {
    applyAlpha(root.getAttribute("data-alpha"));   // apparence déjà rendue côté serveur
    return;
  }

  // Base = ce que le serveur a rendu (appearance.default()), surchargée par le choix local éventuel.
  var theme = root.getAttribute("data-theme") || "oled",
      accent = root.getAttribute("data-accent") || "neutral",
      alpha = root.getAttribute("data-alpha") || "100",
      bg = root.getAttribute("data-bg") || "";
  try {
    var t = localStorage.getItem("lr-theme"), a = localStorage.getItem("lr-accent"),
        al = localStorage.getItem("lr-alpha"), b = localStorage.getItem("lr-bg");
    if (THEMES.test(t || "")) theme = t;
    if (ACCENTS.test(a || "")) accent = a;
    if (al) alpha = al;
    if (b !== null) bg = BG.test(b) ? b : "";      // choix local : fond valide, ou « aucun » ; « u » indisponible hors session
  } catch (e) { /* stockage indisponible */ }
  root.setAttribute("data-theme", theme);
  root.setAttribute("data-accent", accent);
  if (bg) root.setAttribute("data-bg", bg); else root.removeAttribute("data-bg");
  if (alpha !== "100") root.setAttribute("data-alpha", alpha); else root.removeAttribute("data-alpha");
  applyAlpha(alpha);
})();
