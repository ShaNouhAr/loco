"""Point d'entrée gunicorn (« app:app »). Administration : python3 -m loco.cli."""

from loco import app, startup
from loco import alerts, android_apps, backups, db, homeassistant, logforward, network, offsite, proxmox_system, relay

startup()
db.keep_open()               # base tenue ouverte tant qu'on sert : une requête ne paie plus le rangement du journal SQLite
alerts.start_dispatcher()    # fils de fond : uniquement quand on sert, pas sur un import CLI
offsite.start()
logforward.start()
android_apps.start()
homeassistant.start()        # tâches Home Assistant en attente reprises, celles coupées par l'arrêt marquées en échec
backups.start()              # planifications de sauvegarde ; suivi des sauvegardes Proxmox lancées repris
proxmox_system.start()       # mises à jour et redémarrages de Proxmox : suivis repris (ils continuent sur l'hôte)
network.start()              # équipements réseau : relevés et sauvegardes planifiées de la configuration
relay.start()                # connexions persistantes des sessions distantes (agents et navigateurs prévenus, plus de relevé à vide)

__all__ = ["app"]
