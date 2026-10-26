"""Cœur pur-Python de la bibliothèque qrcode (vendorisé, sans dépendance image/PIL/pypng).

Embarqué pour que la génération du QR de double authentification fonctionne partout,
y compris sans le paquet système python3-qrcode (appliance Alpine, installations minimales).
Source : https://github.com/lincolnloop/python-qrcode (licence BSD). Seul le nécessaire à
QRCode.get_matrix() est conservé ; le rendu d'image est retiré.
"""
from . import constants  # noqa: F401
from .main import QRCode  # noqa: F401
