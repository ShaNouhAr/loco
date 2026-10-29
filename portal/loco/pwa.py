"""Support PWA : manifeste d'application installable, service worker (cache de la coque + repli hors-ligne), page hors-ligne."""
import json

from flask import Response, render_template

from . import ASSET_V, app
from .i18n import _

MANIFEST = {
    "name": "loco",
    "short_name": "loco",
    "description": "Dépôt local : cache de mises à jour (Debian, Ubuntu, Red Hat, Windows, Flatpak) et déploiement du parc.",
    "lang": "fr",
    "id": "/",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "any",
    "background_color": "#070b14",
    "theme_color": "#0d1322",
    "icons": [
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/static/icons/icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}


@app.route("/manifest.webmanifest")
def web_manifest():
    return Response(json.dumps(MANIFEST), mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    resp = Response(render_template("sw.js", v=ASSET_V), mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"      # portée racine même si on voulait le servir ailleurs
    resp.headers["Cache-Control"] = "no-cache"         # laisse le navigateur détecter les mises à jour du worker
    return resp


@app.route("/offline")
def offline():
    return render_template("offline.html", title=_("Hors ligne"))
