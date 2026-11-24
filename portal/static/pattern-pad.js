/* Pavé de schéma 3 × 3 du portail, pour déverrouiller un appareil Android (session d'écran et « Contrôler l'écran »).
   Il se comporte comme la grille d'Android : un point n'est pris que si le pointeur passe près de son centre — on trace
   donc en diagonale sans accrocher les points voisins —, un geste vif ne saute aucun point traversé, le trait suit le
   pointeur, et un point sauté sur une ligne droite (1 → 3, 1 → 9…) est pris au passage, comme Android le fait.
   Cliquer les points un à un marche aussi, de même qu'Entrée ou Espace sur un point.
   LocoPatternPad(pad) → { dots() : les numéros tracés (1 à 9, dans l'ordre), clear() : tout effacer }. */
(function () {
  "use strict";
  var CELL = 100;          // repère du dessin (viewBox 300 × 300) : une case = 100
  var HIT = 32;            // rayon de prise autour d'un centre (Android : 0,6 case de diamètre)
  var STEP = 8;            // pas d'échantillonnage d'un geste entre deux positions du pointeur

  window.LocoPatternPad = function (pad) {
    var trace = pad.querySelector("polyline"), rubber = pad.querySelector("line");
    var dots = [], drawing = false, last = null;

    function centre(n) { return [CELL / 2 + (n - 1) % 3 * CELL, CELL / 2 + Math.floor((n - 1) / 3) * CELL]; }

    function redraw() {
      trace.setAttribute("points", dots.map(function (n) { return centre(n).join(","); }).join(" "));
      pad.querySelectorAll(".pattern-dot").forEach(function (d) { d.classList.toggle("on", dots.indexOf(+d.dataset.dot) >= 0); });
    }

    function add(n) {
      if (!n || dots.indexOf(n) >= 0 || dots.length >= 9) return;
      var m = dots[dots.length - 1];
      if (m) {
        var r1 = Math.floor((m - 1) / 3), c1 = (m - 1) % 3, r2 = Math.floor((n - 1) / 3), c2 = (n - 1) % 3;
        if ((r1 - r2) % 2 === 0 && (c1 - c2) % 2 === 0) {                  // même ligne, colonne ou diagonale, un point entre
          var mid = (r1 + r2) / 2 * 3 + (c1 + c2) / 2 + 1;
          if (dots.indexOf(mid) < 0) dots.push(mid);
        }
      }
      dots.push(n);
      redraw();
    }

    function local(ev) {
      var r = pad.getBoundingClientRect();
      return [(ev.clientX - r.left) * 3 * CELL / r.width, (ev.clientY - r.top) * 3 * CELL / r.height];
    }

    function hit(p) {
      var col = Math.floor(p[0] / CELL), row = Math.floor(p[1] / CELL);
      if (col < 0 || col > 2 || row < 0 || row > 2) return 0;
      var dx = p[0] - (col + 0.5) * CELL, dy = p[1] - (row + 0.5) * CELL;
      return dx * dx + dy * dy <= HIT * HIT ? row * 3 + col + 1 : 0;
    }

    function rubberTo(p) {
      if (!rubber) return;
      if (!p || !dots.length) { rubber.setAttribute("visibility", "hidden"); return; }
      var c = centre(dots[dots.length - 1]);
      rubber.setAttribute("x1", c[0]); rubber.setAttribute("y1", c[1]);
      rubber.setAttribute("x2", p[0]); rubber.setAttribute("y2", p[1]);
      rubber.setAttribute("visibility", "visible");
    }

    function follow(p) {
      var from = last || p;
      var steps = Math.max(1, Math.ceil(Math.hypot(p[0] - from[0], p[1] - from[1]) / STEP));
      for (var i = 1; i <= steps; i++) add(hit([from[0] + (p[0] - from[0]) * i / steps, from[1] + (p[1] - from[1]) * i / steps]));
      last = p;
      rubberTo(p);
    }

    function end() { drawing = false; last = null; rubberTo(null); }

    pad.addEventListener("pointerdown", function (ev) {
      ev.preventDefault();
      drawing = true;
      last = null;
      try { pad.setPointerCapture(ev.pointerId); } catch (e) { /* pointeur déjà relâché */ }
      follow(local(ev));
    });
    pad.addEventListener("pointermove", function (ev) { if (drawing) follow(local(ev)); });
    pad.addEventListener("pointerup", end);
    pad.addEventListener("pointercancel", end);
    pad.addEventListener("lostpointercapture", end);
    pad.addEventListener("click", function (ev) {                     // clavier : Entrée ou Espace sur un point
      var dot = ev.target.closest(".pattern-dot");
      if (dot && ev.detail === 0) add(+dot.dataset.dot);
    });

    return {
      dots: function () { return dots.slice(); },
      clear: function () { dots = []; end(); redraw(); }
    };
  };
})();
