"""Point d'entrée gunicorn (« app:app »). Administration : python3 -m loco.cli."""

from loco import app, startup
from loco import alerts, offsite

startup()
alerts.start_dispatcher()    # fils de fond : uniquement quand on sert, pas sur un import CLI
offsite.start()

__all__ = ["app"]
