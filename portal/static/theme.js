/* Transparence des panneaux, appliquée avant l'affichage (chargé en bloquant dans <head>).
   Le thème, l'accent et le fond sont rendus côté serveur en attributs data-* sur <html> — défaut de l'instance
   pour un visiteur, apparence du compte pour un utilisateur connecté — et appliqués par la CSS. Ici, on ne pose
   que la variable de transparence (que la CSS ne peut pas dériver seule d'un attribut). */
(function () {
  var root = document.documentElement;
  var n = parseInt(root.getAttribute("data-alpha"), 10);
  if (n >= 55 && n < 100) root.style.setProperty("--surface-alpha", (n / 100).toFixed(2));
})();
