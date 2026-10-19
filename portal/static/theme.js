/* Applique le thème et la couleur d'accent avant l'affichage (chargé de façon bloquante dans <head>) : OLED neutre par défaut. */
(function () {
  var theme = "oled", accent = "neutral";
  try {
    var t = localStorage.getItem("lr-theme"), a = localStorage.getItem("lr-accent");
    if (t === "light" || t === "dim") theme = t;
    if (/^(blue|green|violet|orange|pink)$/.test(a || "")) accent = a;
  } catch (e) { /* stockage indisponible */ }
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.setAttribute("data-accent", accent);
})();
