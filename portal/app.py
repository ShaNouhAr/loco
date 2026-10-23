"""Point d'entrée gunicorn (« app:app »). Administration : python3 -m loco.cli."""

from loco import app, startup
from loco import alerts

startup()
alerts.start_dispatcher()    # fil de fond des notifications : uniquement quand on sert, pas sur un import CLI

__all__ = ["app"]
