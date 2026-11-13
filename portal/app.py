"""Point d'entrée gunicorn (« app:app »). Administration : python3 -m loco.cli."""

from loco import app, startup
from loco import alerts, android_apps, db, homeassistant, logforward, offsite, relay

startup()
db.keep_open()               # base tenue ouverte tant qu'on sert : une requête ne paie plus le rangement du journal SQLite
alerts.start_dispatcher()    # fils de fond : uniquement quand on sert, pas sur un import CLI
offsite.start()
logforward.start()
android_apps.start()
homeassistant.start()        # tâches Home Assistant en attente reprises, celles coupées par l'arrêt marquées en échec
relay.start()                # connexions persistantes des sessions distantes (agents et navigateurs prévenus, plus de relevé à vide)

__all__ = ["app"]
