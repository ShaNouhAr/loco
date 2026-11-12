/* Transparence des panneaux et couleur de l'entreprise, appliquées avant l'affichage (chargé en bloquant dans <head>).
   Le thème, l'accent et le fond sont rendus côté serveur en attributs data-* sur <html> — défaut de l'instance
   pour un visiteur, apparence du compte pour un utilisateur connecté — et appliqués par la CSS. Ici, on ne pose
   que la variable de transparence (que la CSS ne peut pas dériver seule d'un attribut). */
(function () {
  var root = document.documentElement;
  // Page rechargée (bouton du navigateur, ou actualisation d'une page qui se recharge seule) : les blocs n'entrent pas
  // de nouveau en glissant, seule une vraie navigation les anime.
  try {
    var nav = performance.getEntriesByType("navigation")[0];
    if (nav && nav.type === "reload") root.classList.add("no-enter");
  } catch (e) { /* mesure indisponible : animation d'entrée conservée */ }
  var n = parseInt(root.getAttribute("data-alpha"), 10);
  if (n >= 55 && n < 100) root.style.setProperty("--surface-alpha", (n / 100).toFixed(2));
  // Couleur de l'entreprise (accent « brand ») et texte lisible dessus, choisis par l'administration de l'instance.
  var brand = root.getAttribute("data-brand"), fg = root.getAttribute("data-brand-fg");
  if (/^#[0-9a-f]{6}$/i.test(brand || "")) {
    root.style.setProperty("--brand", brand);
    root.style.setProperty("--brand-fg", /^#[0-9a-f]{6}$/i.test(fg || "") ? fg : "#ffffff");
  }
})();
